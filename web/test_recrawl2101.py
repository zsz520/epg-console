import datetime
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from .models import Channel,Epg
from .quick_edit import FIELDS,revision
from .runtime_config import get_config


class Recrawl2101Tests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_superuser('recrawl-test',password='test-only')
        self.client.force_login(self.user)
        self.day=timezone.localdate()
        self.channel=Channel.objects.create(name='重抓测试',tvg_name='重抓测试',sort='其他',source='cctv',channel_id='<cctv:1>',has_epg=1,ineed=1)
        for day in [self.day,self.day+datetime.timedelta(days=1)]:
            start=timezone.make_aware(datetime.datetime.combine(day,datetime.time(8)))
            Epg.objects.create(channel_id=str(self.channel.pk),title='已存在节目',program_date=day,starttime=start,endtime=start+datetime.timedelta(hours=1),source='cctv')
        cfg=get_config();cfg.auto_crawl=False;cfg.options={'crawl_days':2,'export_days':1};cfg.save()

    def select(self,day=None):
        return Channel.get_crawl_channels(Channel,day or self.day)

    def config(self,**values):
        cfg=get_config();cfg.options.update(values);cfg.save()

    def test_master_and_channel_switch_must_both_be_enabled(self):
        for master in [False,True]:
            for own in [0,1]:
                self.config(enable_recrawl=master)
                Channel.objects.filter(pk=self.channel.pk).update(recrawl=own)
                self.assertEqual(self.select().filter(pk=self.channel.pk).exists(),bool(master and own))

    def test_missing_day_still_fetched_with_either_switch_off(self):
        self.config(enable_recrawl=False)
        self.assertIn(self.channel,self.select(self.day+datetime.timedelta(days=2)))

    def test_today_switch_does_not_refresh_covered_tomorrow(self):
        Channel.objects.filter(pk=self.channel.pk).update(recrawl=1)
        self.assertIn(self.channel,self.select())
        self.assertNotIn(self.channel,self.select(self.day+datetime.timedelta(days=1)))

    def test_channel_disabled_from_crawling_is_never_selected(self):
        for field in ['ineed','has_epg']:
            Channel.objects.filter(pk=self.channel.pk).update(recrawl=1,**{field:0})
            self.assertNotIn(self.channel,self.select())
            Channel.objects.filter(pk=self.channel.pk).update(**{field:1})

    def test_normal_worker_crawl_honors_channel_switch_without_cli_flag(self):
        from crawl import crawl
        Channel.objects.filter(pk=self.channel.pk).update(recrawl=1)
        with patch.object(crawl,'recrawl',0),patch.object(crawl,'cname',False),patch.object(crawl,'xmlinfo',{}),patch.object(crawl,'gen_test_m3u',return_value=(False,'')),patch.object(crawl,'get_epg',return_value={'epgs':[],'ban':0,'msg':'模拟空数据','source':'cctv'}) as fetch:
            crawl.main()
        self.assertEqual(fetch.call_count,1)
        self.assertEqual(fetch.call_args.args[1],self.day)

    def test_global_refresh_strategy_cannot_override_channel_off(self):
        from crawl import crawl
        self.config(enable_recrawl=True,refresh_existing=True,recrawl_days=2)
        with patch.object(crawl,'cname',False),patch.object(crawl,'xmlinfo',{}),patch.object(crawl,'gen_test_m3u',return_value=(False,'')),patch.object(crawl,'get_epg') as fetch:
            crawl.main()
        fetch.assert_not_called()

    def test_xmltv_covered_today_also_respects_both_switches(self):
        Channel.objects.filter(pk=self.channel.pk).update(source='xmltv',channel_id='<xmltv:1:YQ>')
        self.assertNotIn(self.channel,self.select())
        Channel.objects.filter(pk=self.channel.pk).update(recrawl=1)
        self.assertIn(self.channel,self.select())
        self.config(enable_recrawl=False)
        self.assertNotIn(self.channel,self.select())

    def test_quick_editor_exposes_and_persists_recrawl(self):
        url=f'/admin/channels/{self.channel.pk}/quick/'
        self.assertContains(self.client.get(url),'id_recrawl')
        data={key:getattr(self.channel,key) or '' for key in FIELDS}
        data.update(_revision=revision(self.channel),recrawl='1')
        response=self.client.post(url,data)
        self.assertTrue(response.context['saved'])
        self.channel.refresh_from_db();self.assertEqual(self.channel.recrawl,1)

    def test_old_quick_form_missing_field_preserves_value_and_stale_conflicts(self):
        self.channel.recrawl=1;self.channel.save()
        data={key:getattr(self.channel,key) or '' for key in FIELDS if key!='recrawl'}
        data['_revision']=revision(self.channel)
        response=self.client.post(f'/admin/channels/{self.channel.pk}/quick/',data)
        self.assertTrue(response.context['saved'])
        self.channel.refresh_from_db();self.assertEqual(self.channel.recrawl,1)
        before=revision(self.channel)
        Channel.objects.filter(pk=self.channel.pk).update(recrawl=0)
        response=self.client.post(f'/admin/channels/{self.channel.pk}/quick/',{**data,'_revision':before})
        self.assertEqual(response.status_code,409)

    def test_shared_navigation_controls_and_icon_are_present(self):
        for url in ['/','/admin/settings/',f'/admin/channels/{self.channel.pk}/quick/']:
            response=self.client.get(url)
            self.assertContains(response,'aria-label="回到顶部"')
            self.assertContains(response,'rel="icon"')
            self.assertContains(response,'epg-icon.')
        self.assertContains(self.client.get('/admin/settings/'),'id_enable_recrawl')
