import datetime
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase, Client
from .models import Channel, Channel_list, Epg, Task
from .catalog import import_catalog, refresh_catalog, parse_mapping
from .forms import ChannelForm


class CatalogueTests(TestCase):
    def snapshot(self, source='cctv', name='测试频道', identifier='test1'):
        return refresh_catalog(source, [{'name': name, 'id': [identifier], 'sort': '测试', 'logo': ''}])

    def test_refresh_empty_invalid_and_reappearing(self):
        self.snapshot()
        row = Channel_list.objects.get()
        row.is_alive = False
        row.save()
        self.snapshot()
        row.refresh_from_db()
        self.assertTrue(row.is_alive)
        self.assertEqual(Channel_list.objects.count(), 1)
        with self.assertRaises(ValueError):
            refresh_catalog('cctv', [])
        self.assertTrue(Channel_list.objects.get().is_alive)
        with self.assertRaises(ValueError):
            refresh_catalog('cctv', [{'name': '坏数据', 'id': []}])
        self.assertEqual(Channel_list.objects.get().out_name, '测试频道')

    def test_merge_name_and_preserve_existing_programmes(self):
        self.snapshot(name='测试頻道')
        self.snapshot('tvmao', ' 测试频道 ', 'provider-test')
        rows = list(Channel_list.objects.values_list('pk', flat=True))
        result = import_catalog(rows)
        self.assertEqual(result, {'created': 1, 'updated': 0})
        channel = Channel.objects.get()
        self.assertEqual(parse_mapping(channel.channel_id), {'cctv': 'test1', 'tvmao': 'provider-test'})
        epg = Epg.objects.create(channel_id=str(channel.pk), title='保留节目', program_date=datetime.date.today(), starttime=datetime.datetime.now(datetime.timezone.utc))
        import_catalog(rows)
        self.assertEqual(Channel.objects.get().pk, channel.pk)
        self.assertTrue(Epg.objects.filter(pk=epg.pk).exists())
        channel.name, channel.tvg_name = '自定义名称', 'CUSTOM'
        channel.save()
        import_catalog(rows)
        self.assertEqual(Channel.objects.count(), 1)
        self.assertEqual(Channel.objects.get().tvg_name, 'CUSTOM')

    def test_conflict_rolls_back_whole_batch(self):
        self.snapshot()
        self.snapshot('tvmao', '测试频道', 'one')
        import_catalog(list(Channel_list.objects.values_list('pk', flat=True)))
        original = Channel.objects.get().channel_id
        self.snapshot('tvmao', '测试频道', 'two')
        with self.assertRaises(ValidationError):
            import_catalog(list(Channel_list.objects.filter(is_alive=True).values_list('pk', flat=True)))
        self.assertEqual(Channel.objects.get().channel_id, original)

    def test_different_hd_names_not_merged(self):
        self.snapshot()
        self.snapshot('tvmao', '测试频道HD', 'testhd')
        import_catalog(list(Channel_list.objects.values_list('pk', flat=True)))
        self.assertEqual(Channel.objects.count(), 2)

    def test_legacy_unused_mapping_preserved(self):
        channel = Channel.objects.create(name='旧频道', tvg_name='OLD', sort='测试', source='cctv', channel_id='<cctv:test><tianyi:old-id>')
        self.assertIn('<tianyi:old-id>', Channel.objects.get(pk=channel.pk).channel_id)

    def test_form_generates_mapping_and_rejects_bad_source(self):
        self.snapshot()
        form = ChannelForm(data={'name': '测试频道', 'tvg_name': '测试频道', 'sort': '测试',
            'catalog_entries': [Channel_list.objects.get().pk], 'channel_id': '', 'source': '',
            'has_epg': '1', 'ineed': '1', 'recrawl': '0'})
        self.assertTrue(form.is_valid(), form.errors)
        channel = form.save()
        self.assertEqual(channel.channel_id, '<cctv:test1>')
        self.assertEqual(channel.source, 'cctv')
        self.assertEqual(Channel.get_crawl_channels(Channel, datetime.date.today()).count(), 1)
        channel.source = 'tvmao'
        with self.assertRaises(ValidationError):
            channel.save()


class ConsoleTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser('test-admin', password='test-only-password')
        self.client.force_login(self.user)

    def test_console_pages_and_picker_search(self):
        refresh_catalog('cctv', [{'name': '央视一套', 'id': ['cctv1']}])
        for url in ['/admin/', '/admin/operations/', '/admin/catalog/', '/admin/web/channel/add/', '/admin/web/channel_list/']:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200, (url, response.content[-500:]))
        response = self.client.get('/admin/catalog/search/?q=央视')
        self.assertEqual(response.json()['results'][0]['identifier'], 'cctv1')

    def test_task_creation_dedup_cancel_and_csrf(self):
        self.assertEqual(self.client.get('/admin/operations/start/').status_code, 405)
        response = self.client.post('/admin/operations/start/', {'kind': 'catalog', 'source': 'cctv'})
        self.assertEqual(response.status_code, 302)
        self.client.post('/admin/operations/start/', {'kind': 'catalog', 'source': 'cctv'})
        self.assertEqual(Task.objects.count(), 1)
        task = Task.objects.get()
        self.assertEqual(self.client.get(f'/admin/operations/{task.pk}/?format=json').json()['status'], 'queued')
        self.client.post(f'/admin/operations/{task.pk}/cancel/')
        task.refresh_from_db()
        self.assertEqual(task.status, 'cancelled')
        secure = Client(enforce_csrf_checks=True)
        secure.force_login(self.user)
        self.assertEqual(secure.post('/admin/operations/start/', {'kind': 'xml'}).status_code, 403)

    def test_catalog_web_import_and_permissions(self):
        refresh_catalog('cctv', [{'name': '测试频道', 'id': ['test']}])
        pk = Channel_list.objects.get().pk
        response = self.client.post('/admin/catalog/', {'selected': [pk], 'target': ''})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Channel.objects.get().channel_id, '<cctv:test>')
        staff = get_user_model().objects.create_user('limited', is_staff=True)
        self.client.force_login(staff)
        self.assertEqual(self.client.post('/admin/operations/start/', {'kind': 'catalog'}).status_code, 403)
        self.assertEqual(self.client.get('/admin/catalog/search/').status_code, 403)

    def test_public_programme_render_and_bad_date(self):
        channel = Channel.objects.create(name='测试台', tvg_name='TEST+', sort='测试', channel_id='<cctv:test>', source='cctv', has_epg=1, ineed=1)
        from django.utils import timezone
        Epg.objects.create(channel_id=str(channel.pk), title='真实数据结构测试', program_date=timezone.localdate(), starttime=timezone.now(), endtime=timezone.now())
        response = self.client.get('/api/web/', {'ch': 'TEST+', 'date': timezone.localdate().strftime('%Y%m%d')})
        self.assertContains(response, '真实数据结构测试')
        self.assertContains(response, 'TEST%2B')
        self.assertEqual(self.client.get('/api/diyp/').status_code, 400)
        self.assertEqual(self.client.get('/api/diyp/?ch=TEST&date=20260231').status_code, 400)


class ProgrammeIngestTests(TestCase):
    def test_repeat_fetch_is_idempotent_and_invalid_fetch_keeps_previous_data(self):
        from .ingest import save_programmes
        from django.utils import timezone
        channel = Channel.objects.create(name='测试节目台', tvg_name='TEST', sort='测试', source='cctv', channel_id='<cctv:test>')
        record = {'channel_id': channel.pk, 'starttime': timezone.now(), 'endtime': timezone.now() + datetime.timedelta(hours=1),
                  'title': '应保留节目', 'desc': '', 'program_date': timezone.localdate()}
        result = {'source': 'cctv', 'epgs': [record]}
        save_programmes(channel, result)
        save_programmes(channel, result)
        self.assertEqual(Epg.objects.filter(channel_id=str(channel.pk)).count(), 1)
        with self.assertRaises(ValueError):
            save_programmes(channel, {'source': 'cctv', 'epgs': [{**record, 'starttime': None}]})
        self.assertEqual(Epg.objects.get(channel_id=str(channel.pk)).title, '应保留节目')
