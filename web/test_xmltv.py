import datetime
import gzip
from pathlib import Path
import tempfile
from unittest.mock import patch, Mock
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from .models import Channel, Channel_list, Epg, Task, XmltvFeed
from .xmltv import refresh_feed, identifier, split_identifier, get_programmes, parse_time

XML = '''<?xml version="1.0" encoding="utf-8"?><tv>
<channel id="test:id&lt;1&gt;"><display-name>测试频道</display-name><icon src="https://example.com/icon.png"/></channel>
<programme channel="test:id&lt;1&gt;" start="20261122180000 +0800" stop="20261122190000 +0800"><title>原始节目</title><desc>节目详情</desc></programme>
<programme channel="test:id&lt;1&gt;" start="20261122233000 +0800"><title>跨午夜</title></programme>
<programme channel="test:id&lt;1&gt;" start="20261123003000 +0800" stop="20261123010000 +0800"><title>次日节目</title></programme>
</tv>'''


class XmltvTests(TestCase):
    def test_standard_external_doctype_is_ignored_without_network_requests(self):
        for declaration in ('<!DOCTYPE tv SYSTEM "xmltv.dtd">',
                            "<!DOCTYPE tv SYSTEM 'https://example.com/xmltv.dtd'>",
                            '<!DOCTYPE tv PUBLIC "-//XMLTV//DTD XMLTV//EN" "xmltv.dtd">'):
            self.body = XML.replace('<tv>', declaration + '<tv>', 1).encode()
            with patch('requests.Session.get', side_effect=AssertionError('DTD must not be fetched')):
                result = refresh_feed(self.feed.pk)
            self.assertEqual(result['programmes'], 3)

    def test_internal_subset_entity_and_multiple_doctypes_are_rejected(self):
        declarations = ('<!DOCTYPE tv [<!ELEMENT tv ANY>]>',
            '<!DOCTYPE tv [<!ENTITY leak SYSTEM "file:///etc/passwd">]>',
            '<!DOCTYPE tv SYSTEM "xmltv.dtd"><!DOCTYPE tv SYSTEM "other.dtd">')
        for declaration in declarations:
            self.body = XML.replace('<tv>', declaration + '<tv>',1).encode()
            with self.assertRaises(ValueError):
                refresh_feed(self.feed.pk)

    def test_detailed_error_is_preserved_by_parent_task(self):
        from .xmltv import refresh_feeds_for_task, directory
        message = '解压和检查 XML：文件内容不是 XML；已保留原有数据。'
        task = Task.objects.create(kind='xmltv')
        def child(*args, **kwargs):
            import json
            (directory()/f'result-{task.pk}-{self.feed.pk}.json').write_text(json.dumps({'status':'failed','error':message}))
            return Mock(returncode=1)
        with patch('subprocess.run', side_effect=child):
            results = refresh_feeds_for_task(task)
        self.assertEqual(results[0]['error'], message)
        self.feed.refresh_from_db()
        self.assertEqual(self.feed.last_error, message)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        override = override_settings(DATA_DIR=self.temp.name)
        override.enable()
        self.addCleanup(override.disable)
        self.feed = XmltvFeed.objects.create(name='测试文件', url='https://example.com/epg.xml.gz')
        self.body = XML.encode()
        self.download_patch = patch('web.xmltv.download', side_effect=lambda url,path: Path(path).write_bytes(self.body))
        self.download_patch.start()
        self.addCleanup(self.download_patch.stop)

    def test_plain_gzip_mapping_dates_and_ingest(self):
        result = refresh_feed(self.feed.pk)
        self.assertEqual((result['channels'], result['programmes']), (1, 3))
        row = Channel_list.objects.get(source='xmltv')
        self.assertEqual(split_identifier(row.out_channel_id), (self.feed.pk, 'test:id<1>'))
        self.assertNotIn('<', row.out_channel_id)
        channel = Channel.objects.create(name='本地频道', tvg_name='本地频道', sort='测试', source='xmltv',
            channel_id=f'<xmltv:{row.out_channel_id}>', has_epg=1, ineed=1)
        programme = get_programmes(channel, row.out_channel_id, datetime.date(2026,11,22))
        self.assertEqual(len(programme['epgs']), 2)
        self.assertTrue(programme['epgs'][1]['inferred_end'])
        self.assertEqual(programme['epgs'][1]['endtime'].day, 23)
        self.assertEqual(programme['origin_name'], '测试文件')
        from .ingest import save_programmes
        save_programmes(channel, {**programme, 'source':'xmltv'})
        self.assertEqual(Epg.objects.count(), 2)
        self.body = gzip.compress(XML.replace('原始节目','更新后节目').encode())
        refresh_feed(self.feed.pk)
        programme = get_programmes(channel, row.out_channel_id, datetime.date(2026,11,22))
        self.assertEqual(programme['epgs'][0]['title'], '更新后节目')
        save_programmes(channel, {**programme, 'source':'xmltv'})
        self.assertEqual(Epg.objects.count(), 2)
        self.assertEqual(Epg.objects.order_by('starttime').first().title, '更新后节目')

    def test_invalid_empty_html_and_dtd_keep_snapshot(self):
        refresh_feed(self.feed.pk)
        self.feed.refresh_from_db()
        snapshot = self.feed.snapshot
        for bad in (b'<html>wrong</html>', b'<tv/>', b'<tv><channel', b'<!DOCTYPE tv [<!ENTITY x "secret">]><tv/>', b'\x1f\x8bbroken'):
            self.body = bad
            with self.assertRaises(ValueError):
                refresh_feed(self.feed.pk)
            self.feed.refresh_from_db()
            self.assertEqual(self.feed.snapshot, snapshot)
            self.assertTrue(self.feed.last_error)
            self.assertEqual(Channel_list.objects.filter(is_alive=True).count(), 1)

    def test_refresh_only_marks_own_removed_channels(self):
        refresh_feed(self.feed.pk)
        other = XmltvFeed.objects.create(name='第二来源', url='https://example.com/other.xml')
        refresh_feed(other.pk)
        self.body = XML.replace('test:id&lt;1&gt;', 'new-id').encode()
        refresh_feed(self.feed.pk)
        self.assertEqual(Channel_list.objects.filter(is_alive=True).count(), 2)
        self.assertFalse(Channel_list.objects.get(out_channel_id=identifier(self.feed.pk,'test:id<1>')).is_alive)
        self.assertTrue(Channel_list.objects.get(out_channel_id=identifier(other.pk,'test:id<1>')).is_alive)

    def test_size_limits_and_timezone(self):
        with patch('web.xmltv.MAX_XML', 10), self.assertRaises(ValueError):
            refresh_feed(self.feed.pk)
        self.assertEqual(parse_time('20261122000000 +0000').hour, 8)
        self.assertEqual(parse_time('202611222330').utcoffset(), datetime.timedelta(hours=8))
        with self.assertRaises(ValueError):
            parse_time('invalid')

    def test_xmltv_channels_refresh_even_if_future_date_already_saved(self):
        row = Channel.objects.create(name='更新测试', tvg_name='更新测试', sort='测试',
            source='xmltv', channel_id='<xmltv:1:dGVzdA>', has_epg=1, ineed=1,
            last_program_date=datetime.date(2099,1,1))
        self.assertIn(row, Channel.get_crawl_channels(Channel, timezone.localdate()))

    def test_source_admin_and_catalog_labels(self):
        self.client.force_login(get_user_model().objects.create_superuser('xmltv-test',password='test-only'))
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post('/admin/web/xmltvfeed/add/', {'name':'新来源','url':'https://example.com/new.xml','enabled':'on','_save':'保存'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Task.objects.get().kind,'xmltv')
        refresh_feed(self.feed.pk)
        result = self.client.get('/admin/catalog/search/',{'q':'测试频道','source':'xmltv'}).json()
        self.assertEqual(result['results'][0]['source_name'],'XMLTV · 测试文件')
        channel = Channel.objects.create(name='显示名称',tvg_name='默认搜索名',sort='测试', source='hks',channel_id='<hks:hks>')
        self.assertContains(self.client.get(f'/admin/channels/{channel.pk}/quick/'),'value="默认搜索名"')
        self.assertContains(self.client.get(f'/admin/web/channel/{channel.pk}/change/'),'value="默认搜索名"')

    def test_persistent_sort_order_and_navigation(self):
        self.client.force_login(get_user_model().objects.create_superuser('sort-test',password='test-only'))
        a = Channel.objects.create(name='甲',tvg_name='甲',sort='测试',source='hks',channel_id='<hks:hks>',sort_order=20)
        b = Channel.objects.create(name='乙',tvg_name='乙',sort='测试',source='hks',channel_id='<hks:hks>',sort_order=10)
        response = self.client.get('/admin/web/channel/')
        self.assertEqual(list(response.context['cl'].queryset), [b,a])
        self.assertNotIn('sort_order', response.context['cl'].formset.forms[0].fields)
        response = self.client.get(f'/admin/web/channel/{b.pk}/change/')
        self.assertEqual(response.context['channel_navigation']['next']['name'],'甲')

    @patch('web.preview.run_preview')
    def test_preview_cache_invalidates_after_feed_refresh(self, run):
        from .preview import preview
        run.return_value = {'status':'success','count':1,'programmes':[]}
        value = identifier(self.feed.pk, 'test:id<1>')
        self.assertFalse(preview('xmltv', value, '测试')['cached'])
        self.assertTrue(preview('xmltv', value, '测试')['cached'])
        refresh_feed(self.feed.pk)
        self.assertFalse(preview('xmltv', value, '测试')['cached'])
        self.assertEqual(run.call_count,2)

    @patch('subprocess.run', side_effect=__import__('subprocess').TimeoutExpired('xmltv',5))
    def test_task_timeout_keeps_old_snapshot_and_marks_failure(self, run):
        from .xmltv import refresh_feeds_for_task
        refresh_feed(self.feed.pk)
        self.feed.refresh_from_db()
        snapshot = self.feed.snapshot
        task = Task.objects.create(kind='xmltv')
        result = refresh_feeds_for_task(task)
        self.assertEqual(result[0]['status'],'failed')
        self.feed.refresh_from_db()
        self.assertEqual(self.feed.snapshot,snapshot)
        self.assertTrue(self.feed.last_error)

