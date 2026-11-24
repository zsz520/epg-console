from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase, override_settings, Client
from .models import Channel, Task


class ChannelDelete2102Tests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser('delete-admin', password='test-only')
        self.client.force_login(self.user)
        self.rows = [Channel.objects.create(name=f'频道{i}', tvg_name=f'频道{i}',
                     sort='央视', source='cctv', channel_id=f'<cctv:{i}>', has_epg=1, ineed=1)
                     for i in range(138)]
        self.url = '/admin/web/channel/'

    def payload(self):
        data = {'form-TOTAL_FORMS': '138', 'form-INITIAL_FORMS': '138',
                'form-MIN_NUM_FORMS': '0', 'form-MAX_NUM_FORMS': '1000',
                'action': 'delete_selected', 'index': '0', 'select_across': '0',
                '_selected_action': [str(self.rows[0].pk)]}
        for i, row in enumerate(self.rows):
            for name in ('id', 'name', 'tvg_id', 'tvg_name', 'sort', 'source', 'has_epg', 'ineed'):
                data[f'form-{i}-{name}'] = str(getattr(row, name) or '')
        return data

    @override_settings(DATA_UPLOAD_MAX_NUMBER_FIELDS=1000)
    def test_reproduce_original_138_row_bad_request(self):
        self.assertEqual(self.client.post(self.url, self.payload()).status_code, 400)
        self.assertEqual(Channel.objects.count(), 138)

    def test_large_page_bulk_delete_requires_confirmation_and_only_deletes_selected(self):
        response = self.client.post(self.url, self.payload())
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'admin/delete_selected_confirmation.html')
        self.assertEqual(Channel.objects.count(), 138)
        self.assertFalse(Task.objects.exists())
        data = {'action': 'delete_selected', 'post': 'yes',
                '_selected_action': [str(self.rows[0].pk)], 'select_across': '0'}
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(self.client.post(self.url, data).status_code, 302)
        self.assertFalse(Channel.objects.filter(pk=self.rows[0].pk).exists())
        self.assertEqual(Channel.objects.count(), 137)
        self.assertEqual(Task.objects.get().kind, 'xml')

    def test_large_inline_save_also_accepts_more_than_1000_fields(self):
        data = self.payload()
        for key in ('action', 'index', 'select_across', '_selected_action'):
            data.pop(key)
        data['_save'] = '保存'
        data['form-0-name'] = '修改后的频道'
        self.assertEqual(self.client.post(self.url, data).status_code, 302)
        self.rows[0].refresh_from_db()
        self.assertEqual(self.rows[0].name, '修改后的频道')
        self.assertEqual(Channel.objects.count(), 138)

    def test_quick_delete_link_confirmation_and_single_delete(self):
        row = self.rows[0]
        quick = f'/admin/channels/{row.pk}/quick/'
        delete = f'/admin/web/channel/{row.pk}/delete/'
        response = self.client.get(quick)
        self.assertContains(response, f'href="{delete}" target="_top">删除频道</a>')
        response = self.client.get(delete)
        self.assertTemplateUsed(response, 'admin/delete_confirmation.html')
        self.assertTrue(Channel.objects.filter(pk=row.pk).exists())
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(self.client.post(delete, {'post': 'yes'}).status_code, 302)
        self.assertFalse(Channel.objects.filter(pk=row.pk).exists())
        self.assertEqual(Task.objects.get().kind, 'xml')

    def test_delete_permission_and_csrf_are_preserved(self):
        staff = get_user_model().objects.create_user('editor', is_staff=True)
        staff.user_permissions.set(Permission.objects.filter(codename='change_channel'))
        self.client.force_login(staff)
        row = self.rows[0]
        response = self.client.get(f'/admin/channels/{row.pk}/quick/')
        self.assertNotContains(response, 'class="quick-delete"')
        delete = f'/admin/web/channel/{row.pk}/delete/'
        self.assertEqual(self.client.post(delete, {'post': 'yes'}).status_code, 403)
        self.assertEqual(self.client.post(self.url, self.payload()).status_code, 302)
        self.assertEqual(Channel.objects.count(), 138)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.user)
        self.assertEqual(csrf_client.post(delete, {'post': 'yes'}).status_code, 403)
        self.assertEqual(Channel.objects.count(), 138)
