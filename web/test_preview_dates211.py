import datetime
import json
import tempfile
from pathlib import Path
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.utils import timezone
from .models import Channel, Channel_list, Epg
from .preview import preview, resolve_date


class PreviewDates211Tests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_superuser('date-check',password='test-only')
        self.client.force_login(self.user)
        self.channel=Channel.objects.create(name='日期测试',tvg_name='日期测试',sort='央视',source='cctv',channel_id='<cctv:cctv1>')
        self.row=Channel_list.objects.create(source='cctv',out_name='日期测试',out_channel_id='cctv1')
        self.today=timezone.localdate()

    def test_dates_are_bounded_and_invalid_values_rejected(self):
        for delta in (-7,-1,0,1,7):
            day=self.today+datetime.timedelta(days=delta)
            self.assertEqual(resolve_date(day.isoformat()),day)
        for value in ('','bad','2026-02-30',123,True,(self.today-datetime.timedelta(days=8)).isoformat(),(self.today+datetime.timedelta(days=8)).isoformat()):
            with self.assertRaises(ValidationError):resolve_date(value)

    @patch('web.preview.preview')
    def test_catalog_custom_and_saved_mapping_forward_selected_date_without_ingest(self,fetch):
        fetch.return_value={'status':'empty','programmes':[],'date':self.today.isoformat()}
        date=(self.today-datetime.timedelta(days=1)).isoformat()
        for target in ({'catalog_id':self.row.pk},{'channel_id':self.channel.pk,'source':'cctv'}, {'source':'cctv','custom_id':'hidden','name':'自定义'}):
            response=self.client.post('/admin/sources/preview/',json.dumps({**target,'date':date}),content_type='application/json')
            self.assertEqual(response.status_code,200)
            self.assertEqual(fetch.call_args.kwargs['date'],date)
        self.assertFalse(Epg.objects.exists())

    @patch('web.preview.preview')
    def test_invalid_date_does_not_start_preview(self,fetch):
        for date in ('invalid',(self.today+datetime.timedelta(days=8)).isoformat()):
            self.assertEqual(self.client.post('/admin/sources/preview/',json.dumps({'catalog_id':self.row.pk,'date':date}),content_type='application/json').status_code,400)
        fetch.assert_not_called()

    def test_cache_is_isolated_by_selected_day_and_force_bypasses(self):
        with tempfile.TemporaryDirectory() as directory,override_settings(DATA_DIR=Path(directory)),patch('web.preview.run_preview',return_value={'status':'success','count':1,'programmes':[]}) as run:
            yesterday=(self.today-datetime.timedelta(days=1)).isoformat()
            self.assertFalse(preview('cctv','cctv1','测试',date=yesterday)['cached'])
            self.assertTrue(preview('cctv','cctv1','测试',date=yesterday)['cached'])
            self.assertFalse(preview('cctv','cctv1','测试',date=self.today.isoformat())['cached'])
            self.assertEqual(run.call_count,2)
            self.assertEqual(run.call_args.args[0]['date'],self.today.isoformat())
            self.assertFalse(preview('cctv','cctv1','测试',date=yesterday,force=True)['cached'])
            self.assertEqual(run.call_count,3)

    def test_picker_and_both_scroll_buttons_render_in_quick_and_public_pages(self):
        response=self.client.get(f'/admin/channels/{self.channel.pk}/quick/')
        for text in ('节目对比日期','预览前一天','预览后一天','回到底部','回到顶部'):
            self.assertContains(response,text)
        for path in ('/','/admin/web/channel/'):
            self.assertContains(self.client.get(path),'回到底部')
