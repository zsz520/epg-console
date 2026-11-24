import datetime
import json
import subprocess
import tempfile
from unittest.mock import patch, Mock
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase, override_settings
from django.utils import timezone
from .models import Channel, Channel_list, Epg, Task
from .quick_edit import revision, FIELDS


class LiveEditTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser('quick-test', password='test-only')
        self.client.force_login(self.user)
        self.channel = Channel.objects.create(name='测试台', tvg_name='测试台', sort='央视',
            channel_id='<cctv:test>', source='cctv', has_epg=1, ineed=1)
        self.row = Channel_list.objects.create(source='hks', out_name='香港卫视', out_channel_id='hks')
        self.url = f'/admin/channels/{self.channel.pk}/quick/'

    def data(self, **extra):
        data = {key: getattr(self.channel, key) or '' for key in FIELDS}
        data['_revision'] = revision(self.channel)
        return {**data, **extra}

    def test_quick_save_mapping_and_export(self):
        response = self.client.get(self.url)
        self.assertEqual(response['X-Frame-Options'], 'SAMEORIGIN')
        self.assertContains(response, '补全空字段')
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(self.url, self.data(name='快速改名', sort='自定义分组',
                tvg_id='', catalog_entries=[self.row.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['saved'])
        self.channel.refresh_from_db()
        self.row.refresh_from_db()
        self.assertEqual(self.channel.name, '快速改名')
        self.assertEqual(self.channel.sort, '自定义分组')
        self.assertEqual(self.channel.tvg_id, self.channel.tvg_name)
        self.assertIn('<hks:hks>', self.channel.channel_id)
        self.assertEqual(self.row.inner_channel_id, self.channel.pk)
        self.assertEqual(Task.objects.get().kind, 'xml')

    def test_stale_save_does_not_overwrite_and_validation_retry(self):
        data = self.data(name='旧页修改')
        Channel.objects.filter(pk=self.channel.pk).update(name='新页已保存')
        response = self.client.post(self.url, data)
        self.assertEqual(response.status_code, 409)
        self.assertContains(response, '频道已在其他页面修改', status_code=409)
        self.channel.refresh_from_db()
        self.assertEqual(self.channel.name, '新页已保存')
        response = self.client.post(self.url, self.data(channel_id='bad'))
        self.assertFalse(response.context['saved'])
        self.assertTrue(response.context['form'].errors)
        self.assertEqual(response.context['revision'], revision(self.channel))
        response = self.client.post(self.url, self.data(name='修正后保存'))
        self.assertTrue(response.context['saved'])

    @patch('web.preview.preview')
    def test_preview_resolves_only_known_rows_and_does_not_save(self, preview):
        preview.return_value = {'status': 'success', 'count': 1, 'programmes': []}
        before = (Channel.objects.count(), Epg.objects.count(), Task.objects.count())
        url = '/admin/sources/preview/'
        response = self.client.post(url, json.dumps({'catalog_id': self.row.pk}), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        preview.assert_called_with('hks', 'hks', '香港卫视', force=False)
        response = self.client.post(url, json.dumps({'channel_id': self.channel.pk, 'source': 'cctv', 'force': True}), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        preview.assert_called_with('cctv', 'test', '测试台', force=True)
        for data in ({'source': 'cctv', 'identifier': 'arbitrary'}, {'channel_id': self.channel.pk, 'source': 'hks'}, []):
            self.assertEqual(self.client.post(url, json.dumps(data), content_type='application/json').status_code, 400)
        self.assertEqual(self.client.get(url).status_code, 405)
        self.assertEqual(before, (Channel.objects.count(), Epg.objects.count(), Task.objects.count()))

    @patch('web.preview.preview')
    def test_staff_permissions(self, preview):
        staff = get_user_model().objects.create_user('readonly', is_staff=True)
        self.client.force_login(staff)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.client.post('/admin/sources/preview/', '{}', content_type='application/json').status_code, 403)
        staff.user_permissions.set(Permission.objects.filter(codename__in=['view_channel', 'view_channel_list']))
        preview.return_value = {'status': 'empty', 'programmes': []}
        self.assertEqual(self.client.post('/admin/sources/preview/', json.dumps({'catalog_id': self.row.pk}), content_type='application/json').status_code, 200)
        self.assertEqual(self.client.post(self.url, self.data()).status_code, 403)

    def test_inline_extended_fields_and_new_group(self):
        data = {'form-TOTAL_FORMS': '1', 'form-INITIAL_FORMS': '1', 'form-MIN_NUM_FORMS': '0',
            'form-MAX_NUM_FORMS': '1000', 'form-0-id': self.channel.pk, 'form-0-name': '列表改名',
            'form-0-tvg_name': '新匹配名', 'form-0-tvg_id': '', 'form-0-sort': '新分组',
            'form-0-source': 'cctv', 'form-0-has_epg': '1', 'form-0-ineed': '0', '_save': '保存'}
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(self.client.post('/admin/web/channel/', data).status_code, 302)
        self.assertEqual(Task.objects.get().kind, 'xml')
        self.channel.refresh_from_db()
        self.assertEqual((self.channel.name, self.channel.tvg_name, self.channel.tvg_id,
                          self.channel.sort, self.channel.ineed), ('列表改名', '新匹配名', '新匹配名', '新分组', 0))


class PreviewExecutionTests(TestCase):
    def test_normalize_day_timezone_and_inferred_end(self):
        from .preview_runner import normalize
        date = datetime.date(2026, 11, 22)
        result = normalize({'epgs': [
            {'title': '昨天', 'starttime': datetime.datetime(2026, 11, 21, 23)},
            {'title': '节目一', 'starttime': datetime.datetime(2026, 11, 22, 0)},
            {'title': '节目二', 'starttime': datetime.datetime(2026, 11, 22, 1)},
        ]}, date)
        self.assertEqual(len(result), 2)
        self.assertTrue(result[0]['inferred_end'])
        self.assertTrue(result[0]['start'].endswith('+08:00'))
        self.assertTrue(result[-1]['end'].endswith('23:59:59+08:00'))

    @patch('web.preview.run_preview')
    def test_cache_and_force(self, run):
        from .preview import preview
        run.return_value = {'status': 'success', 'count': 1, 'programmes': []}
        with tempfile.TemporaryDirectory() as directory, override_settings(DATA_DIR=directory):
            self.assertFalse(preview('hks', 'hks', '香港卫视')['cached'])
            self.assertTrue(preview('hks', 'hks', '香港卫视')['cached'])
            self.assertFalse(preview('hks', 'hks', '香港卫视', force=True)['cached'])
        self.assertEqual(run.call_count, 2)

    @patch('web.preview.os.killpg')
    @patch('web.preview.subprocess.Popen')
    def test_timeout_reaps_preview_process(self, popen, kill):
        from .preview import run_preview
        process = popen.return_value
        process.communicate.side_effect = [subprocess.TimeoutExpired('preview', 15), ('', '')]
        result = run_preview({})
        self.assertEqual(result['status'], 'timeout')
        kill.assert_called_once()
        self.assertEqual(process.communicate.call_count, 2)
