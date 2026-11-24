from django.contrib.auth import get_user_model
from django.test import TestCase
from .models import Channel


class ListRecrawl2114Tests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_superuser('list-recrawl',password='test-only'))
        self.channel=Channel.objects.create(name='列表重抓',tvg_name='列表重抓',sort='央视',source='cctv',channel_id='<cctv:1>',recrawl=1)

    def payload(self):
        data={'form-TOTAL_FORMS':'1','form-INITIAL_FORMS':'1','form-MIN_NUM_FORMS':'0',
              'form-MAX_NUM_FORMS':'1000','form-0-id':self.channel.pk,'_save':'保存'}
        for key in ('name','tvg_id','tvg_name','sort','source','has_epg','ineed'):
            data['form-0-'+key]=getattr(self.channel,key)
        return data

    def test_column_contains_editable_switch_and_can_toggle_both_ways(self):
        response=self.client.get('/admin/web/channel/')
        self.assertContains(response,'需重新获取当天数据')
        self.assertContains(response,'name="form-0-recrawl"')
        for value in (0,1):
            data={**self.payload(),'form-0-recrawl':str(value)}
            self.assertEqual(self.client.post('/admin/web/channel/',data).status_code,302)
            self.channel.refresh_from_db();self.assertEqual(self.channel.recrawl,value)
            quick=self.client.get(f'/admin/channels/{self.channel.pk}/quick/')
            self.assertEqual(quick.context['form'].initial['recrawl'],value)

    def test_old_page_without_switch_retains_saved_value(self):
        self.assertEqual(self.client.post('/admin/web/channel/',self.payload()).status_code,302)
        self.channel.refresh_from_db();self.assertEqual(self.channel.recrawl,1)

    def test_invalid_switch_does_not_save_other_changes(self):
        data={**self.payload(),'form-0-recrawl':'2','form-0-name':'不能保存'}
        self.assertEqual(self.client.post('/admin/web/channel/',data).status_code,200)
        self.channel.refresh_from_db()
        self.assertEqual(self.channel.name,'列表重抓')
        self.assertEqual(self.channel.recrawl,1)
