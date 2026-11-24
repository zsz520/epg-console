import datetime
import tempfile
from types import SimpleNamespace
from unittest.mock import patch, Mock
import xml.etree.ElementTree as ET
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone
from .models import Channel, Epg


class Feature21Tests(TestCase):
    def channel(self, name='测试台', category='央视', **extra):
        return Channel.objects.create(name=name, tvg_name=name, sort=category,
            channel_id='<cctv:test>', source='cctv', has_epg=1, ineed=1, **extra)

    def test_editable_id_exports_without_changing_programme_links(self):
        channel = self.channel()
        original_pk = channel.pk
        Epg.objects.create(channel_id=str(original_pk), title='保留节目', program_date=timezone.localdate(),
            starttime=timezone.now(), endtime=timezone.now() + datetime.timedelta(hours=1))
        channel.tvg_id = 'CUSTOM-CCTV'
        channel.save()
        from crawl import crawl
        with tempfile.TemporaryDirectory() as directory, patch.dict(crawl.dirs, {'share': directory}):
            _, filename = crawl.gen_xml('all')
            tree = ET.parse(filename)
            self.assertIsNotNone(tree.find(".//channel[@id='CUSTOM-CCTV']"))
            self.assertIsNotNone(tree.find(".//programme[@channel='CUSTOM-CCTV']"))
            playlist = directory + '/test.m3u'
            crawl.gen_test_m3u(Channel.objects.all(), playlist)
            from pathlib import Path
            self.assertIn('tvg-id="CUSTOM-CCTV"', Path(playlist).read_text(encoding='utf-8'))
        self.assertEqual(channel.pk, original_pk)
        self.assertEqual(Epg.objects.get().channel_id, str(original_pk))

    def test_duplicate_reserved_and_default_ids(self):
        channel = self.channel(tvg_id='OWN')
        with self.assertRaises(ValidationError):
            self.channel('另一个', tvg_id='OWN')
        with self.assertRaises(ValidationError):
            self.channel('占位', tvg_id='9999')
        other = self.channel('默认')
        self.assertEqual(other.tvg_id, other.tvg_name)

    def test_public_category_search_and_local_category(self):
        self.channel('央视台', '央视')
        self.channel('卫视台', '卫视')
        self.channel('重庆台', '重庆')
        response = self.client.get('/category/cctv/')
        self.assertContains(response, '央视台')
        self.assertNotContains(response, '卫视台')
        response = self.client.get('/', {'sort': '重庆'})
        self.assertContains(response, '重庆台')
        self.assertNotContains(response, '央视台')
        response = self.client.get('/', {'q': '不存在的频道'})
        self.assertContains(response, '暂无匹配频道')

    def test_admin_category_and_inline_edit(self):
        user = get_user_model().objects.create_superuser('id-test', password='test-only')
        self.client.force_login(user)
        channel = self.channel('央视台', '央视')
        self.channel('卫视台', '卫视')
        response = self.client.get('/admin/web/channel/?category=cctv')
        self.assertEqual(response.status_code, 200)
        cl = response.context['cl']
        self.assertEqual(list(cl.queryset.values_list('name', flat=True)), ['央视台'])
        data = {'form-TOTAL_FORMS': '1', 'form-INITIAL_FORMS': '1', 'form-MIN_NUM_FORMS': '0',
                'form-MAX_NUM_FORMS': '1000', 'form-0-id': str(channel.pk), 'form-0-tvg_id': 'EDITED-ID',
                'form-0-sort_order': channel.sort_order, 'form-0-name': channel.name, 'form-0-tvg_name': channel.tvg_name, 'form-0-has_epg': '1', 'form-0-ineed': '1', 'form-0-sort': '卫视', 'form-0-source': 'cctv', '_save': '保存'}
        response = self.client.post('/admin/web/channel/?category=cctv', data)
        self.assertEqual(response.status_code, 302, response.content[:300])
        channel.refresh_from_db()
        self.assertEqual(channel.tvg_id, 'EDITED-ID')
        self.assertEqual(channel.sort, '卫视')

    def test_task_sidebar_opens_workspace_and_primary_source_choices(self):
        user = get_user_model().objects.create_superuser('workspace-test', password='test-only')
        self.client.force_login(user)
        channel = self.channel()
        channel.channel_id = '<cctv:test><tvmao:backup>'
        channel.save()
        response = self.client.get('/admin/web/channel/')
        form = response.context['cl'].formset.forms[0]
        self.assertEqual(dict(form.fields['source'].choices), {'': '自动选择', 'tvmao': '电视猫', 'cctv': '央视'})
        self.assertEqual(form.fields['sort'].widget.input_type, 'text')
        self.assertContains(response, 'id_form-0-sort-groups')
        response = self.client.get('/admin/web/task/')
        self.assertContains(response, '选择 后台任务 来修改')
        self.assertNotContains(response, 'class="console-nav"')
        self.assertContains(response, '任务中心')
        self.assertNotContains(response, 'row "后台任务"')
        response = self.client.get('/admin/operations/')
        self.assertContains(response, '提交任务')
        data = {'form-TOTAL_FORMS': '1', 'form-INITIAL_FORMS': '1', 'form-MIN_NUM_FORMS': '0',
                'form-MAX_NUM_FORMS': '1000', 'form-0-id': str(channel.pk), 'form-0-tvg_id': channel.tvg_id,
                'form-0-sort_order': channel.sort_order, 'form-0-name': channel.name, 'form-0-tvg_name': channel.tvg_name, 'form-0-has_epg': '1', 'form-0-ineed': '1', 'form-0-sort': '卫视', 'form-0-source': 'tvmao', '_save': '保存'}
        response = self.client.post('/admin/web/channel/', data)
        self.assertEqual(response.status_code, 302)
        channel.refresh_from_db()
        self.assertEqual((channel.sort, channel.source, channel.channel_id), ('卫视', 'tvmao', '<tvmao:backup><cctv:test>'))
        Channel.objects.filter(pk=channel.pk).update(channel_id='legacy-incomplete-id')
        self.assertEqual(self.client.get('/admin/web/channel/').status_code, 200)
        other = self.channel('另一个频道')
        data.update({'form-TOTAL_FORMS': '2', 'form-INITIAL_FORMS': '2',
                     'form-0-tvg_id': channel.tvg_id, 'form-1-id': str(other.pk),
                     'form-1-sort_order': other.sort_order, 'form-1-name': other.name, 'form-1-tvg_name': other.tvg_name, 'form-1-has_epg': '1', 'form-1-tvg_id': other.tvg_id, 'form-1-sort': '卫视',
                     'form-1-source': 'cctv', 'form-1-ineed': '1'})
        self.assertEqual(self.client.post('/admin/web/channel/', data).status_code, 302)
        other.refresh_from_db()
        self.assertEqual(other.sort, '卫视')

    def test_upgrade_only_replaces_default_identifiers(self):
        from importlib import import_module
        from django.apps import apps
        old = self.channel('CCTV1')
        custom = self.channel('CCTV2', tvg_id='my-custom-id')
        Channel.objects.filter(pk=old.pk).update(tvg_id=str(old.pk))
        upgrade = import_module('web.migrations.0004_name_default_ids')
        upgrade.use_name_for_default_ids(apps, None)
        old.refresh_from_db()
        custom.refresh_from_db()
        self.assertEqual(old.tvg_id, 'CCTV1')
        self.assertEqual(custom.tvg_id, 'my-custom-id')

    def test_edit_group_choose_existing_or_create(self):
        user = get_user_model().objects.create_superuser('group-test', password='test-only')
        self.client.force_login(user)
        channel = self.channel('分组测试台')
        self.channel('已有分组台', '地方频道')
        url = f'/admin/web/channel/{channel.pk}/change/'
        response = self.client.get(url)
        self.assertContains(response, '<option value="地方频道"></option>', html=True)
        form = response.context['adminform'].form
        data = {key: value for key, value in form.initial.items() if value is not None}
        data.update({'sort': '新建自定义分组', 'tvg_id': '', '_save': '保存'})
        self.assertEqual(self.client.post(url, data).status_code, 302)
        channel.refresh_from_db()
        self.assertEqual(channel.sort, '新建自定义分组')
        self.assertEqual(channel.tvg_id, channel.tvg_name)
        response = self.client.get(url)
        self.assertContains(response, '<option value="新建自定义分组"></option>', html=True)

    @patch.dict('os.environ', {'CHUANLIU_AUTHORIZATION': 'test-token'})
    @patch('crawl.spiders.chuanliu.requests.get')
    def test_token_is_sent_to_both_provider_endpoints(self, get):
        from crawl.spiders.chuanliu import get_channels_chuanliu, get_epgs_chuanliu
        response = Mock()
        response.json.return_value = {'ret_data': [{'bd_name': '测试', 'name': '测试', 'id': 1, 'channelType': '1002'}]}
        get.return_value = response
        self.assertEqual(len(get_channels_chuanliu()), 1)
        self.assertEqual(get.call_args.kwargs['headers']['Authorization'], 'Bearer test-token')
        response.json.return_value = {'ret_data': []}
        get_epgs_chuanliu(SimpleNamespace(id=1), '1', datetime.date.today(), 0)
        self.assertEqual(get.call_args.kwargs['headers']['Authorization'], 'Bearer test-token')



