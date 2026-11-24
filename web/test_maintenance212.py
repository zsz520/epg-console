import datetime
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import zipfile
from unittest.mock import patch
from bs4 import BeautifulSoup
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import TestCase,override_settings,Client
from django.utils import timezone
from .models import Channel,Epg,Task,CrawlFailure,Crawl_log,AutoScheduleState
from .runtime_config import get_config
from . import backups,failures,maintenance
from .ingest import save_programmes


class Maintenance212Tests(TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        setting=override_settings(DATA_DIR=self.root);setting.enable();self.addCleanup(setting.disable)
        self.user=get_user_model().objects.create_superuser('maintenance',password='test-only')
        self.client.force_login(self.user)
        self.channel=Channel.objects.create(name='维护测试',tvg_name='维护测试',sort='央视',source='cctv',channel_id='<cctv:1>',ineed=1,has_epg=1)
        self.day=timezone.localdate()
        cfg=get_config();cfg.auto_crawl=False;cfg.options={'retry_delay_seconds':0};cfg.save()

    def opts(self,**values):
        cfg=get_config();cfg.options.update(values);cfg.save()

    def row(self,hour=0,title='节目'):
        start=timezone.make_aware(datetime.datetime.combine(self.day,datetime.time(hour)))
        return dict(channel_id=str(self.channel.pk),program_date=self.day,starttime=start,endtime=start+datetime.timedelta(hours=1),title=title)

    def test_failure_records_unique_dates_sanitizes_and_success_resolves(self):
        failures.failed(self.channel,self.day,'失败 https://private.example/token')
        failures.failed(self.channel,self.day,'仍失败')
        row=CrawlFailure.objects.get();self.assertEqual(row.attempts,2)
        save_programmes(self.channel,{'epgs':[self.row()],'source':'cctv'})
        row.refresh_from_db();self.assertTrue(row.resolved)
        failures.failed(self.channel,self.day,'新一轮失败')
        row.refresh_from_db();self.assertEqual(row.attempts,1)

    def test_automatic_retry_is_bounded_deduplicated_and_never_retries_history(self):
        now=timezone.now()
        for day in (self.day,self.day-datetime.timedelta(days=1)):
            failures.failed(self.channel,day,'失败')
        CrawlFailure.objects.update(next_retry=now-datetime.timedelta(seconds=1))
        failures.queue_due(now);self.assertFalse(Task.objects.exists())
        self.opts(auto_retry_failures=True,failure_retry_rounds=2)
        failures.queue_due(now);failures.queue_due(now)
        task=Task.objects.get();self.assertEqual(task.kind,'retry')
        self.assertEqual(task.params['failure_ids'],[CrawlFailure.objects.get(program_date=self.day).pk])
        task.status='success';task.save()
        CrawlFailure.objects.update(auto_attempts=2)
        failures.queue_due(now);self.assertEqual(Task.objects.count(),1)

    def test_retry_execution_only_attempts_requested_day_and_resolves(self):
        failures.failed(self.channel,self.day,'失败')
        row=CrawlFailure.objects.get()
        task=Task.objects.create(kind='retry',params={'failure_ids':[row.pk],'automatic':True})
        with patch('crawl.crawl.get_epg',return_value={'epgs':[self.row()],'source':'cctv'}) as fetch,patch.dict('utils.general.xmlinfo',{},clear=True):
            failures.run(task)
        self.assertEqual(fetch.call_args.args[1],self.day)
        row.refresh_from_db();self.assertTrue(row.resolved);self.assertEqual(row.auto_attempts,1)

    def test_quality_rejection_preserves_previous_snapshot(self):
        for hour in range(10):Epg.objects.create(**self.row(hour,title='旧节目'))
        self.opts(quality_check=True,quality_min_old_count=5,quality_min_percent=50)
        with self.assertRaisesMessage(ValueError,'完整性保护'):
            save_programmes(self.channel,{'epgs':[self.row(0,'不完整')],'source':'cctv'})
        self.assertEqual(Epg.objects.count(),10)
        self.assertEqual(Epg.objects.get(starttime=self.row()['starttime']).title,'旧节目')
        self.opts(quality_check=False)
        save_programmes(self.channel,{'epgs':[self.row(0,'新节目')],'source':'cctv'})
        self.assertEqual(Epg.objects.count(),1)

    def test_quality_gap_guard_checks_raw_gaps(self):
        Epg.objects.create(**self.row());Epg.objects.create(**self.row(1))
        self.opts(quality_check=True,quality_min_old_count=2,quality_min_percent=1,quality_gap_minutes=60)
        with self.assertRaisesMessage(ValueError,'空缺'):
            save_programmes(self.channel,{'epgs':[self.row(8),self.row(9)],'source':'cctv'})
        self.assertEqual(Epg.objects.count(),2)

    def test_quality_failure_tries_next_source_in_id_order(self):
        from crawl import crawl
        self.channel.channel_id='<cctv:1><tvmao:backup>';self.channel.save()
        for hour in range(10):Epg.objects.create(**self.row(hour))
        self.opts(quality_check=True,quality_min_old_count=5,quality_min_percent=50)
        calls=[]
        def fetch(channel,identifier,day,**kwargs):
            calls.append(kwargs['source'])
            return {'epgs':[self.row(h) for h in range(1 if kwargs['source']=='cctv' else 10)],'success':1,'ban':0}
        with patch.object(crawl,'epg_func',side_effect=fetch),patch('web.programmes.add_previous_context',side_effect=lambda c,r,*a:r):
            result=crawl.get_epg(self.channel,self.day)
        self.assertEqual(calls,['cctv','tvmao'])
        self.assertEqual(result['source'],'tvmao')

    def make_cleanup(self):
        now=timezone.now();old=now-datetime.timedelta(days=30)
        task=Task.objects.create(kind='xml',status='success',finished_at=old)
        active=Task.objects.create(kind='epg',status='running')
        for directory in ('tasks','source-previews','sources','xmltv','backups'):(self.root/directory).mkdir(exist_ok=True)
        (self.root/'tasks'/f'{task.pk}.log').write_text('old')
        (self.root/'tasks'/f'{active.pk}.log').write_text('active')
        cache=self.root/'source-previews'/('a'*64+'.json');cache.write_text('{}');os.utime(cache,(old.timestamp(),old.timestamp()))
        (self.root/'source-previews'/'slot-0.lock').touch()
        (self.root/'sources'/'keep.py').write_text('keep')
        (self.root/'xmltv'/'keep.sqlite3').write_text('keep')
        return old

    def test_cleanup_only_targets_expired_allowed_files(self):
        old=self.make_cleanup();plan=maintenance.candidates()
        self.assertEqual(len(plan),2)
        row=Crawl_log.objects.create(msg='old');Crawl_log.objects.filter(pk=row.pk).update(dt=old)
        deleted,logs=maintenance.execute(plan,(timezone.now()-datetime.timedelta(days=14)).isoformat())
        self.assertEqual((deleted,logs),(2,1))
        self.assertTrue((self.root/'sources/keep.py').exists());self.assertTrue((self.root/'xmltv/keep.sqlite3').exists())
        self.assertTrue((self.root/'source-previews/slot-0.lock').exists())

    def test_cleanup_skips_changed_files_and_symlinks(self):
        self.make_cleanup();plan=maintenance.candidates()
        for item in plan:(self.root/item['path']).write_text('changed after preview')
        deleted,_=maintenance.execute(plan,(timezone.now()-datetime.timedelta(days=14)).isoformat())
        self.assertEqual(deleted,0)

    def test_maintenance_and_backup_scheduling_is_opt_in(self):
        now=timezone.now();maintenance.schedule_maintenance(now)
        self.assertFalse(Task.objects.exists())
        self.opts(auto_backup=True,auto_cleanup=True)
        maintenance.schedule_maintenance(now)
        self.assertEqual(AutoScheduleState.objects.filter(kind__in=['backup','cleanup']).count(),2)
        AutoScheduleState.objects.filter(kind__in=['backup','cleanup']).update(next_run=now-datetime.timedelta(seconds=1))
        maintenance.schedule_maintenance(now);maintenance.schedule_maintenance(now)
        self.assertEqual(set(Task.objects.values_list('kind',flat=True)),{'backup','cleanup'})

    def fixture_db(self):
        path=self.root/'epg.sqlite3'
        with sqlite3.connect(path) as dest,connection.cursor() as cur:
            cur.execute("SELECT type,name,sql FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%' ORDER BY type DESC")
            objects=cur.fetchall()
            for kind,name,sql in objects:
                if kind=='table':dest.execute(sql)
            for kind,name,sql in objects:
                if kind=='table':
                    cur.execute('SELECT * FROM "'+name+'"');rows=cur.fetchall()
                    if rows:dest.executemany('INSERT INTO "'+name+'" VALUES ('+','.join('?' for _ in rows[0])+')',rows)
            for kind,name,sql in objects:
                if kind=='index':dest.execute(sql)
        (self.root/'secret-key').write_text('backup-test-secret')
        (self.root/'sources').mkdir(exist_ok=True);(self.root/'sources/test.py').write_text('original-source')
        return path

    def backup_upload(self):
        self.fixture_db();name=backups.create(self.root)
        return SimpleUploadedFile(name,(self.root/'backups'/name).read_bytes(),content_type='application/zip')

    def test_backup_stage_and_cold_restore_roundtrip(self):
        upload=self.backup_upload();summary,token=backups.stage_restore(upload,self.user)
        self.assertEqual(summary['channels'],1)
        params=backups.confirm_restore(token,self.user)
        with sqlite3.connect(self.root/'epg.sqlite3') as db:db.execute("UPDATE web_channel SET name='changed'")
        (self.root/'sources/test.py').write_text('changed-source')
        backups.request_restore(params)
        self.assertTrue(backups.apply_pending(self.root))
        with sqlite3.connect(self.root/'epg.sqlite3') as db:self.assertEqual(db.execute('SELECT name FROM web_channel').fetchone()[0],'维护测试')
        self.assertEqual((self.root/'sources/test.py').read_text(),'original-source')
        self.assertFalse((self.root/'restore-pending.json').exists())
        self.assertEqual(len(list((self.root/'backups').glob('*.zip'))),2)

    def test_restore_rejects_path_traversal_and_does_not_touch_data(self):
        data=io.BytesIO()
        with zipfile.ZipFile(data,'w') as z:
            z.writestr('../outside','bad');z.writestr('manifest.json',json.dumps({'format':1,'files':{'../outside':'bad'}}))
        with self.assertRaisesMessage(ValueError,'非法'):
            backups.stage_restore(SimpleUploadedFile('bad.zip',data.getvalue()),self.user)
        self.assertFalse((self.root.parent/'outside').exists())

    def test_restore_rolls_back_on_swap_error(self):
        upload=self.backup_upload();_,token=backups.stage_restore(upload,self.user)
        params=backups.confirm_restore(token,self.user)
        with sqlite3.connect(self.root/'epg.sqlite3') as db:db.execute("UPDATE web_channel SET name='current-data'")
        backups.request_restore(params)
        real=os.replace
        def replace(src,dst):
            if 'restore-staging' in str(src) and Path(src).name=='secret-key':raise OSError('simulated failure')
            return real(src,dst)
        with patch('web.backups.os.replace',side_effect=replace),self.assertRaises(OSError):backups.apply_pending(self.root)
        with sqlite3.connect(self.root/'epg.sqlite3') as db:self.assertEqual(db.execute('SELECT name FROM web_channel').fetchone()[0],'current-data')
        self.assertFalse((self.root/'restore-pending.json').exists())

    def test_interrupted_restore_journal_recovers_before_retry(self):
        upload=self.backup_upload();_,token=backups.stage_restore(upload,self.user)
        params=backups.confirm_restore(token,self.user);backups.request_restore(params)
        stage=self.root/'restore-staging'/params['nonce']/'files'
        old=self.root/('restore-old-'+params['nonce']);old.mkdir()
        os.replace(self.root/'sources',old/'sources')
        os.replace(stage/'sources',self.root/'sources')
        (self.root/'restore-journal.json').write_text(json.dumps({'nonce':params['nonce'],'destinations':['sources'],'originals':['sources']}))
        self.assertTrue(backups.apply_pending(self.root))
        self.assertEqual((self.root/'sources/test.py').read_text(),'original-source')

    def test_restore_cancels_old_pending_jobs_and_verifies_checksums(self):
        Task.objects.create(kind='epg',status='queued')
        upload=self.backup_upload();_,token=backups.stage_restore(upload,self.user)
        params=backups.confirm_restore(token,self.user)
        database=self.root/'restore-staging'/params['nonce']/'files/epg.sqlite3'
        with sqlite3.connect(database) as db:self.assertEqual(db.execute('SELECT status FROM web_task').fetchone()[0],'cancelled')
        (database.parent/'sources/test.py').write_text('tampered')
        backups.request_restore(params)
        with self.assertRaisesMessage(ValueError,'发生变化'):backups.apply_pending(self.root)
        self.assertEqual((self.root/'sources/test.py').read_text(),'original-source')

    def test_restore_refuses_when_worker_lock_is_held(self):
        import fcntl
        upload=self.backup_upload();_,token=backups.stage_restore(upload,self.user)
        params=backups.confirm_restore(token,self.user);backups.request_restore(params)
        with (self.root/'worker.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            with self.assertRaises(BlockingIOError):backups.apply_pending(self.root)
        self.assertTrue((self.root/'restore-pending.json').exists())

    def test_backup_restore_download_permission_and_csrf(self):
        staff=get_user_model().objects.create_user('reader',is_staff=True)
        staff.user_permissions.add(Permission.objects.get(codename='view_runtimeconfig'))
        self.client.force_login(staff)
        self.assertEqual(self.client.post('/admin/maintenance/',{'action':'backup'}).status_code,403)
        self.assertEqual(self.client.get('/admin/maintenance/backups/epg-full-20261124-000000-12345678.zip/').status_code,403)
        client=Client(enforce_csrf_checks=True);client.force_login(self.user)
        self.assertEqual(client.post('/admin/maintenance/',{'action':'backup'}).status_code,403)

    def test_restore_view_requires_preview_and_explicit_confirmation(self):
        upload=self.backup_upload()
        response=self.client.post('/admin/maintenance/',{'action':'restore-preview','archive':upload})
        self.assertEqual(response.status_code,200)
        token=response.context['restore_token']
        self.assertFalse(Task.objects.filter(kind='restore').exists())
        self.client.post('/admin/maintenance/',{'action':'restore-apply','token':token})
        self.assertFalse(Task.objects.filter(kind='restore').exists())
        response=self.client.post('/admin/maintenance/',{'action':'restore-apply','token':token,'confirmed':'on'})
        self.assertEqual(response.status_code,302)
        self.assertEqual(Task.objects.get(kind='restore').requested_by,self.user)
        self.assertFalse((self.root/'restore-pending.json').exists())

    def test_committed_restore_journal_is_cleaned_without_pending_marker(self):
        self.fixture_db()
        (self.root/'restore-journal.json').write_text(json.dumps({'nonce':'b'*32,'committed':True}))
        self.assertTrue(backups.apply_pending(self.root))
        self.assertFalse((self.root/'restore-journal.json').exists())
        with sqlite3.connect(self.root/'epg.sqlite3') as db:self.assertEqual(db.execute('SELECT count(*) FROM web_channel').fetchone()[0],1)

    def test_settings_search_and_public_links_not_duplicated(self):
        self.assertContains(self.client.get('/admin/settings/'),'搜索设置')
        soup=BeautifulSoup(self.client.get('/').content,'html.parser')
        self.assertEqual(len(soup.select('a[href="/admin/"]')),1)
        self.assertEqual(len(soup.select('a[href="/download/all.xml"]')),1)
