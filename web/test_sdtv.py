import datetime
import json
from types import SimpleNamespace
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.test import TestCase
from crawl.source_registry import registry as _registry, load as _load
sdtv = _load(_registry()[0]['sdtv'])


class SdtvTests(TestCase):
    def payload(self, date):
        start = datetime.datetime.combine(date, datetime.time(23, 30), sdtv.SHANGHAI)
        return json.dumps({'code': 1, 'value': {'list': [{'name': ' 节目（上） ',
            'begintime': int(start.timestamp()), 'endtime': int((start + datetime.timedelta(hours=1)).timestamp())}]}})

    def test_channels_come_from_current_player_script(self):
        page = '<script src="https://file.iqilu.com/custom/v/zhibo/js/time-shifting-local.js?v=2"></script>'
        script = 'var t={sdtv:{catname:"山东卫视",id:24,live:"sdtv"},qlpd:{id:25,live:"qilu",catname:"齐鲁频道"}};'
        with patch.object(sdtv, 'fetch_text', side_effect=[page, script]):
            channels = sdtv.get_channels_sdtv()
        self.assertEqual(len(channels), 2)
        self.assertEqual(channels[1]['url'], 'https://v.iqilu.com/live/qlpd/')
        self.assertEqual(channels[0]['id'], ['24'])
        self.assertEqual(channels[0]['sort'], '卫视')

    def test_programmes_keep_real_dates_and_internal_channel_id(self):
        date = datetime.date(2026, 11, 22)
        text = 'epg_callback(' + self.payload(date) + ');'
        programmes = sdtv.parse_programmes(text, SimpleNamespace(id=77), date)
        self.assertEqual(programmes[0]['channel_id'], 77)
        self.assertEqual(programmes[0]['title'], '节目（上）')
        self.assertEqual(programmes[0]['endtime'].date(), date + datetime.timedelta(days=1))
        with self.assertRaises(ValueError):
            sdtv.parse_programmes(text, SimpleNamespace(id=77), date + datetime.timedelta(days=1))

    def test_failures_return_no_programmes(self):
        for text in ['<html>error</html>', '{"code":0}', '{"code":1,"value":{"list":[]}}']:
            with patch.object(sdtv, 'fetch_text', return_value=text):
                result = sdtv.get_epgs_sdtv(SimpleNamespace(id=77), '24', datetime.date(2026, 11, 22), 0)
            self.assertEqual(result['success'], 0)
            self.assertEqual(result['epgs'], [])
        with patch.object(sdtv, 'fetch_text', return_value='<html>layout changed</html>'):
            with self.assertRaises(ValueError):
                sdtv.get_channels_sdtv()

    def test_task_entries_are_in_sidebar_only(self):
        user = get_user_model().objects.create_superuser('nav-test', password='test-only')
        self.client.force_login(user)
        response = self.client.get('/admin/operations/')
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(response.content, 'html.parser')
        sidebar = soup.select_one('#nav-sidebar')
        self.assertIn('后台任务', sidebar.get_text())
        self.assertIn('任务中心', sidebar.get_text())
        self.assertIsNone(soup.select_one('.console-nav'))

