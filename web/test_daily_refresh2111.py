import datetime
from contextlib import ExitStack
from unittest.mock import patch
from django.test import TestCase
from django.contrib.auth import get_user_model
from django.utils import timezone
from .models import Channel, Epg
from .runtime_config import get_config
from .settings_form import SettingsForm
from .options import options


class DailyFullRefresh2111Tests(TestCase):
    def setUp(self):
        self.day=timezone.localdate()
        self.channel=Channel.objects.create(name='每日全量',tvg_name='每日全量',sort='央视',source='cctv',channel_id='<cctv:1>',recrawl=0,ineed=1,has_epg=1)
        self.disabled=Channel.objects.create(name='停用测试',tvg_name='停用测试',sort='央视',source='cctv',channel_id='<cctv:2>',recrawl=1,ineed=0,has_epg=1)
        for day in (self.day,self.day+datetime.timedelta(days=1)):
            start=timezone.make_aware(datetime.datetime.combine(day,datetime.time(8)))
            Epg.objects.create(channel_id=str(self.channel.pk),program_date=day,starttime=start,endtime=start+datetime.timedelta(hours=1),title='已有节目')
        cfg=get_config();cfg.auto_crawl=False;cfg.options={'daily_first_full_refresh':True,'enable_recrawl':False,'crawl_days':2,'export_days':1};cfg.save()

    def run_crawl(self,env=None,day=None,export_error=False):
        from crawl import crawl
        with ExitStack() as stack:
            stack.enter_context(patch.object(crawl,'cname',False))
            stack.enter_context(patch.object(crawl,'save_to_db',True))
            stack.enter_context(patch.object(crawl,'xmlinfo',{}))
            stack.enter_context(patch.dict('os.environ',{'EPG_CHANNEL_IDS':env or ''}))
            stack.enter_context(patch.object(crawl,'gen_test_m3u',side_effect=RuntimeError('export failed') if export_error else None,return_value=(False,'')))
            fetch=stack.enter_context(patch.object(crawl,'get_epg',return_value={'epgs':[],'ban':0,'msg':'模拟失败，保留已有数据','source':'cctv'}))
            if day:stack.enter_context(patch.object(crawl.timezone,'localdate',return_value=day))
            crawl.main()
            return [(call.args[0].pk,call.args[1]) for call in fetch.call_args_list]

    def test_first_round_forces_every_configured_day_even_with_both_recrawl_switches_off(self):
        calls=self.run_crawl()
        self.assertEqual(calls,[(self.channel.pk,self.day),(self.channel.pk,self.day+datetime.timedelta(days=1))])
        self.assertEqual(get_config().last_daily_full_refresh,self.day)
        self.channel.refresh_from_db();self.assertEqual(self.channel.recrawl,0)
        self.assertEqual(Epg.objects.count(),2)

    def test_second_round_and_reloaded_state_do_not_force_again(self):
        self.run_crawl()
        self.assertEqual(get_config().last_daily_full_refresh,self.day)
        self.assertEqual(self.run_crawl(),[])

    def test_new_day_gets_new_full_round(self):
        self.run_crawl()
        tomorrow=self.day+datetime.timedelta(days=1)
        self.assertEqual(self.run_crawl(day=tomorrow),[(self.channel.pk,tomorrow),(self.channel.pk,tomorrow+datetime.timedelta(days=1))])
        self.assertEqual(get_config().last_daily_full_refresh,tomorrow)

    def test_switch_off_keeps_existing_rules_and_does_not_mark_date(self):
        cfg=get_config();cfg.options['daily_first_full_refresh']=False;cfg.save()
        self.assertEqual(self.run_crawl(),[])
        self.assertIsNone(get_config().last_daily_full_refresh)

    def test_selected_channel_task_does_not_consume_daily_round(self):
        self.run_crawl(env=str(self.channel.pk))
        self.assertIsNone(get_config().last_daily_full_refresh)
        self.assertEqual(len(self.run_crawl()),2)

    def test_aborted_round_does_not_mark_complete(self):
        with self.assertRaises(RuntimeError):self.run_crawl(export_error=True)
        self.assertIsNone(get_config().last_daily_full_refresh)
        self.assertEqual(len(self.run_crawl()),2)

    def test_first_day_does_not_fetch_previous_day_context(self):
        from crawl import crawl
        start=timezone.make_aware(datetime.datetime.combine(self.day,datetime.time(8)))
        result={'success':1,'epgs':[{'channel_id':str(self.channel.pk),'program_date':self.day,'starttime':start,'endtime':start+datetime.timedelta(hours=1),'title':'今天'}],'ban':0}
        with patch.object(crawl,'epg_func',return_value=result),patch('web.programmes.add_previous_context') as context:
            crawl.get_epg(self.channel,self.day,earliest_date=self.day)
        context.assert_not_called()

    def test_scoped_ingestion_does_not_replace_or_insert_history(self):
        from .ingest import save_programmes
        yesterday=self.day-datetime.timedelta(days=1)
        def row(day,title):
            start=timezone.make_aware(datetime.datetime.combine(day,datetime.time(8)))
            return dict(channel_id=str(self.channel.pk),program_date=day,starttime=start,endtime=start+datetime.timedelta(hours=1),title=title)
        history=Epg.objects.create(**row(yesterday,'保留历史'))
        result={'source':'cctv','epgs':[row(yesterday,'来源旧节目'),row(self.day,'今天更新')],
                'context_epgs':[row(yesterday,'昨日衔接')]}
        self.assertEqual(save_programmes(self.channel,result,date_range=(self.day,self.day+datetime.timedelta(days=1))),1)
        history.refresh_from_db();self.assertEqual(history.title,'保留历史')
        self.assertEqual(Epg.objects.filter(program_date=yesterday).count(),1)
        self.assertEqual(Epg.objects.get(program_date=self.day).title,'今天更新')

    def test_setting_renders_saves_and_keeps_internal_date(self):
        user=get_user_model().objects.create_superuser('daily-settings',password='test-only')
        self.client.force_login(user)
        self.assertContains(self.client.get('/admin/settings/'),'id_daily_first_full_refresh')
        cfg=get_config();cfg.last_daily_full_refresh=self.day;cfg.save()
        data={key:getattr(cfg,key) for key in SettingsForm.Meta.fields}
        data.update(options(cfg));data['include_channels']='\n'.join(data['include_channels'])
        data.update(_settings_full='1',daily_first_full_refresh=False)
        self.assertEqual(self.client.post('/admin/settings/',data).status_code,302)
        cfg=get_config();self.assertFalse(options(cfg)['daily_first_full_refresh'])
        self.assertEqual(cfg.last_daily_full_refresh,self.day)
