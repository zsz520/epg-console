import datetime
import json
from unittest.mock import patch
from django.test import TestCase
from django.utils import timezone
from .models import Channel, Epg, Task, AutoScheduleState
from .runtime_config import get_config
from .options import options
from .ingest import save_programmes
from .scheduling import tick, spec, summaries


class Audit291Tests(TestCase):
    def setUp(self):
        self.day=timezone.localdate()
        self.channel=Channel.objects.create(name='审查测试',tvg_name='审查测试',sort='其他',source='cctv',
            channel_id='<cctv:1><tvmao:2>',has_epg=1,ineed=1)
        cfg=get_config();cfg.options={'retry_delay_seconds':0};cfg.auto_crawl=False;cfg.save()

    def row(self, day=None, **extra):
        day=day or self.day
        start=timezone.make_aware(datetime.datetime.combine(day,datetime.time(8)))
        return dict(channel_id=self.channel.pk,program_date=day,starttime=start,
                    endtime=start+datetime.timedelta(hours=1),title='真实节目',**extra)

    def test_every_source_uses_attempt_limit_and_stops_on_success(self):
        from crawl import crawl
        calls=[]
        def fetch(channel,identifier,day,**kwargs):
            calls.append(kwargs['source'])
            success=calls==['cctv']*3+['tvmao']*2
            return {'success':int(success),'epgs':[self.row()] if success else [],'msg':'失败','ban':0}
        with patch.object(crawl,'epg_func',side_effect=fetch),patch('web.programmes.add_previous_context',side_effect=lambda c,r,*a:r):
            result=crawl.get_epg(self.channel,self.day)
        self.assertEqual(calls,['cctv']*3+['tvmao']*2)
        self.assertEqual(result['source'],'tvmao');self.assertEqual(self.channel.source,'cctv')

    def test_missing_primary_uses_valid_backup_only_when_enabled(self):
        from crawl import crawl
        self.channel.source='obsolete'
        with patch.object(crawl,'epg_func',return_value={'success':1,'epgs':[self.row()],'ban':0}) as fetch,patch('web.programmes.add_previous_context',side_effect=lambda c,r,*a:r):
            self.assertEqual(crawl.get_epg(self.channel,self.day)['source'],'cctv')
        self.assertEqual(fetch.call_count,1)
        cfg=get_config();cfg.options={'fallback':False};cfg.save()
        with patch.object(crawl,'epg_func') as fetch:
            self.assertFalse(crawl.get_epg(self.channel,self.day)['success']);fetch.assert_not_called()

    def test_retry_wait_and_ban_skips_repeated_requests_to_same_source(self):
        from crawl import crawl
        cfg=get_config();cfg.options={'retry_delay_seconds':2,'retries':2};cfg.save()
        with patch.object(crawl,'epg_func',return_value={'success':0,'epgs':[],'ban':0,'msg':'失败'}),patch('time.sleep') as sleep:
            crawl.get_epg(self.channel,self.day)
        self.assertEqual(sleep.call_count,2)
        with patch.object(crawl,'epg_func',side_effect=[{'success':0,'epgs':[],'ban':1}, {'success':1,'epgs':[self.row()],'ban':0}]) as fetch,patch('web.programmes.add_previous_context',side_effect=lambda c,r,*a:r):
            self.assertEqual(crawl.get_epg(self.channel,self.day)['source'],'tvmao')
        self.assertEqual(fetch.call_count,2)

    def test_banned_channel_is_deferred_so_other_channels_are_processed(self):
        from crawl import crawl
        other=Channel.objects.create(name='后续频道',tvg_name='后续频道',sort='其他',source='cctv',channel_id='<cctv:2>',has_epg=1,ineed=1)
        cfg=get_config();cfg.options={'crawl_days':1,'export_days':1};cfg.save()
        seen=[]
        def fetch(channel,*args):
            seen.append(channel.pk)
            return {'epgs':[],'msg':'拒绝' if channel.pk==self.channel.pk else '空','ban':int(channel.pk==self.channel.pk),'source':'cctv'}
        with patch.object(crawl,'get_epg',side_effect=fetch),patch.object(crawl,'cname',False),patch.object(crawl,'xmlinfo',{}),patch.object(crawl,'gen_test_m3u',return_value=(False,'')):
            crawl.main()
        self.assertEqual(seen,[self.channel.pk,other.pk,self.channel.pk])

    def test_future_maximum_date_does_not_hide_missing_today(self):
        self.channel.last_program_date=self.day+datetime.timedelta(days=7);self.channel.save()
        self.assertIn(self.channel,Channel.get_crawl_channels(Channel,self.day))
        Epg.objects.create(**self.row())
        self.assertNotIn(self.channel,Channel.get_crawl_channels(Channel,self.day))
        self.assertIn(self.channel,Channel.get_crawl_channels(Channel,self.day+datetime.timedelta(days=1)))

    def test_adapter_rejects_wrong_date_channel_blank_title_and_partial_failure(self):
        from crawl.spiders import epg_func,epg_funcs
        cases=[{'epgs':[self.row(self.day+datetime.timedelta(days=1))]},
               {'epgs':[{**self.row(),'channel_id':9999}]},
               {'epgs':[{**self.row(),'title':' '}]},
               {'success':0,'epgs':[self.row()],'msg':'部分失败'}]
        for result in cases:
            with patch.dict(epg_funcs,{'cctv':lambda *args:result}):
                response=epg_func(self.channel,'1',self.day)
            self.assertEqual(response['success'],0);self.assertEqual(response['epgs'],[])

    def test_preview_only_future_records_never_becomes_full_day_success(self):
        from .preview_runner import normalize
        self.assertEqual(normalize({'epgs':[self.row(self.day+datetime.timedelta(days=1))]},self.day),[])
        self.assertEqual(normalize({'epgs':[]},self.day),[])

    def test_failed_ingest_preserves_old_snapshot(self):
        Epg.objects.create(**self.row())
        with self.assertRaises(ValueError):save_programmes(self.channel,{'source':'cctv','epgs':[{**self.row(),'title':''}]})
        self.assertEqual(Epg.objects.get().title,'真实节目')

    def test_valid_fallback_can_save_without_revalidating_obsolete_primary(self):
        future=self.day+datetime.timedelta(days=7)
        Channel.objects.filter(pk=self.channel.pk).update(source='obsolete',last_program_date=future)
        self.channel.refresh_from_db()
        self.assertEqual(save_programmes(self.channel,{'source':'cctv','epgs':[self.row()]}),1)
        self.channel.refresh_from_db()
        self.assertEqual(self.channel.source,'obsolete')
        self.assertEqual(self.channel.last_program_date,future)
        self.assertEqual(Epg.objects.get().source,'cctv')

    def test_conflicting_programmes_preserve_snapshot_and_duplicates_collapse(self):
        Epg.objects.create(**self.row())
        with self.assertRaises(ValueError):
            save_programmes(self.channel,{'source':'cctv','epgs':[self.row(),{**self.row(),'title':'冲突'}]})
        self.assertEqual(Epg.objects.get().title,'真实节目')
        self.assertEqual(save_programmes(self.channel,{'source':'cctv','epgs':[self.row(),self.row()]}),1)
        self.assertEqual(Epg.objects.count(),1)

    def test_diyp_keeps_exact_custom_id_case_before_legacy_normalization(self):
        self.channel.tvg_id='my-Custom-id';self.channel.save()
        Epg.objects.create(**self.row())
        response=self.client.get('/api/diyp/',{'ch':'my-Custom-id','date':self.day.strftime('%Y%m%d')})
        self.assertEqual(response.json()['channel_name'],self.channel.name)
        self.assertIn('真实节目',[row['title'] for row in response.json()['epg_data']])

    def test_diyp_respects_fill_disabled_and_unknown_channels_are_empty(self):
        cfg=get_config();cfg.fill_programme_gaps=False;cfg.save()
        for name in [self.channel.tvg_name,'不存在']:
            response=self.client.get('/api/diyp/',{'ch':name,'date':self.day.strftime('%Y%m%d')})
            self.assertEqual(response.status_code,200)
            self.assertEqual(response.json()['epg_data'],[])
        response=self.client.get('/api/web/',{'channel_id':self.channel.pk})
        self.assertContains(response,'该日期暂无已采集节目')
        self.assertEqual(response.context['epgs'],[])

    def test_public_synthetic_rows_are_labelled(self):
        response=self.client.get('/api/web/',{'channel_id':self.channel.pk})
        self.assertContains(response,'缺失补全')

    def test_daily_unused_interval_changes_do_not_duplicate_current_slot(self):
        cfg=get_config();cfg.auto_crawl=True;cfg.epg_schedule_mode='daily';cfg.epg_schedule_times='08:00,12:00';cfg.save()
        now=timezone.make_aware(datetime.datetime.combine(self.day,datetime.time(8,0,20)))
        tick(now);Task.objects.update(status='success')
        cfg.interval_seconds=7200;cfg.crawl_on_start=not cfg.crawl_on_start;cfg.save()
        tick(now+datetime.timedelta(seconds=10))
        self.assertEqual(Task.objects.count(),1)

    def test_old_schedule_signature_preserves_next_run_on_upgrade(self):
        cfg=get_config();cfg.auto_crawl=True;cfg.epg_schedule_mode='daily';cfg.epg_schedule_times='08:00,12:00';cfg.save()
        now=timezone.make_aware(datetime.datetime.combine(self.day,datetime.time(8,0,20)))
        due=now.replace(hour=12,minute=0,second=0)
        AutoScheduleState.objects.create(kind='epg',signature=json.dumps(spec(cfg,'epg'),sort_keys=True),next_run=due)
        tick(now)
        self.assertEqual(Task.objects.count(),0)
        self.assertEqual(AutoScheduleState.objects.get(pk='epg').next_run,due)

    def test_pending_interval_shows_pending_instead_of_false_next_time(self):
        cfg=get_config();cfg.auto_crawl=True;cfg.save();tick()
        self.assertTrue(summaries()[0]['pending']);self.assertIsNone(summaries()[0]['next_run'])

    def test_cancel_queued_interval_reanchors_instead_of_immediate_resubmission(self):
        from django.contrib.auth import get_user_model
        user=get_user_model().objects.create_superuser('cancel-audit',password='test-only')
        self.client.force_login(user)
        cfg=get_config();cfg.auto_crawl=True;cfg.save();tick()
        task=Task.objects.get(kind='epg')
        AutoScheduleState.objects.filter(pk='epg').update(next_run=timezone.now()-datetime.timedelta(minutes=1))
        self.assertEqual(self.client.post(f'/admin/operations/{task.pk}/cancel/').status_code,302)
        tick();task.refresh_from_db()
        self.assertEqual(task.status,'cancelled');self.assertEqual(Task.objects.count(),1)
        self.assertGreater(AutoScheduleState.objects.get(pk='epg').next_run,timezone.now())

    def test_readonly_task_page_has_no_cancel_control_and_log_limit_is_actual(self):
        from django.contrib.auth import get_user_model
        from django.contrib.auth.models import Permission
        user=get_user_model().objects.create_user('task-reader',is_staff=True)
        user.user_permissions.add(Permission.objects.get(codename='view_task'))
        self.client.force_login(user)
        task=Task.objects.create(kind='epg',status='success')
        cfg=get_config();cfg.options={'task_log_bytes':8000};cfg.save()
        response=self.client.get(f'/admin/operations/{task.pk}/')
        self.assertNotContains(response,'id="cancel-task"')
        self.assertContains(response,'最近 8 KiB')
