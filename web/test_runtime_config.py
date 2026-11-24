import signal
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from .models import RuntimeConfig, Task
from .runtime_config import get_config


class RuntimeConfigTests(TestCase):
    def test_legacy_environment_seeds_once_then_database_wins(self):
        with patch.dict('os.environ', {'CRAWL_INTERVAL_SECONDS': '7200'}):
            config = get_config()
        self.assertEqual(config.interval_seconds, 7200)
        config.interval_seconds = 1234
        config.save()
        with patch.dict('os.environ', {'CRAWL_INTERVAL_SECONDS': '86400'}):
            self.assertEqual(get_config().interval_seconds, 1234)

    def test_settings_save_validation_and_permission(self):
        user = get_user_model().objects.create_superuser('settings-test', password='test-only')
        self.client.force_login(user)
        data = {'auto_crawl': 'on', 'interval_seconds': '7200', 'task_timeout_seconds': '90', 'source_timeout_seconds': '30'}
        self.assertEqual(self.client.post('/admin/operations/', data).status_code, 302)
        config = get_config()
        self.assertEqual((config.interval_seconds, config.task_timeout_seconds, config.source_timeout_seconds), (7200, 90, 30))
        self.assertFalse(config.crawl_on_start)
        data['source_timeout_seconds'] = '100'
        response = self.client.post('/admin/operations/', data)
        self.assertContains(response, '单来源超时不能超过单次任务超时')
        self.assertEqual(get_config().source_timeout_seconds, 30)
        staff = get_user_model().objects.create_user('readonly-settings', is_staff=True)
        staff.user_permissions.add(Permission.objects.get(codename='view_task'))
        self.client.force_login(staff)
        self.assertNotContains(self.client.get('/admin/operations/'), '保存任务设置')
        self.assertEqual(self.client.post('/admin/operations/', data).status_code, 403)

    def test_worker_uses_database_timeout_for_new_job(self):
        from . import tasks
        config = get_config()
        config.task_timeout_seconds = 10
        config.save()
        task = Task.objects.create(kind='xml', status='running')
        child = Mock(pid=123, returncode=0)
        child.poll.return_value = None
        with tempfile.TemporaryDirectory() as directory, patch.object(tasks, 'DATA', Path(directory)), \
             patch.object(tasks.subprocess, 'Popen', return_value=child), \
             patch.object(tasks.time, 'monotonic', side_effect=[0, 11]), \
             patch.object(tasks.os, 'killpg') as kill:
            tasks.run_job(task, [False])
        kill.assert_called_once_with(123, signal.SIGTERM)
        task.refresh_from_db()
        self.assertEqual(task.status, 'failed')
        self.assertIn('10 秒', task.summary)

    def test_catalogue_subprocess_uses_database_source_timeout(self):
        from . import task_runner
        config = get_config()
        config.source_timeout_seconds = 17
        config.save()
        task = Task.objects.create(kind='catalog', source='cctv', status='running')
        with patch.object(task_runner.subprocess, 'run', return_value=Mock(returncode=1)) as run:
            task_runner.main(task.pk)
        self.assertEqual(run.call_args.kwargs['timeout'], 17)

    @patch.dict('os.environ', {'CHUANLIU_AUTHORIZATION': ''})
    def test_builtin_provider_default_requires_no_compose_setting(self):
        from crawl.spiders.chuanliu import authorization_headers
        self.assertTrue(authorization_headers()['Authorization'].startswith('Bearer '))
        self.assertGreater(len(authorization_headers()['Authorization']), 20)
