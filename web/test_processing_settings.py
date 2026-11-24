import datetime
import io
import json
import os
import tempfile
import xml.etree.ElementTree as ET
from unittest.mock import patch, Mock
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.utils import timezone
from .models import Channel,Epg,Task
from .runtime_config import get_config
from .options import options
from .programmes import project
from .settings_form import SettingsForm


class ProcessingTests(TestCase):
    def setUp(self):
        self.day=timezone.localdate()
        self.channel=Channel.objects.create(name='跨日台',tvg_name='跨日台',sort='央视',source='hks',channel_id='<hks:hks>',has_epg=1,ineed=1)

    def dt(self,days,hour,minute=0):
        return timezone.make_aware(datetime.datetime.combine(self.day+datetime.timedelta(days=days),datetime.time(hour,minute)))

    def row(self,start,end,title='长节目'):
        return {'channel_id':self.channel.pk,'title':title,'starttime':start,'endtime':end,'program_date':start.date(),'desc':''}

    def test_split_midnight_multi_day_and_exact_midnight(self):
        raw=self.row(self.dt(0,23,30),self.dt(2,1))
        records=project([raw],split=True,fill=False)
        self.assertEqual(len(records),3)
        self.assertEqual(records[0]['endtime'],self.dt(0,23,59)+datetime.timedelta(seconds=59))
        self.assertEqual(records[1]['starttime'],self.dt(1,0))
        self.assertEqual(records[1]['endtime'].strftime('%H:%M:%S'),'23:59:59')
        self.assertEqual(records[2]['endtime'],self.dt(2,1))
        self.assertEqual(raw['endtime'],self.dt(2,1))
        self.assertEqual(len(project([raw],split=False,fill=False)),1)
        self.assertEqual(len(project([self.row(self.dt(0,23),self.dt(1,0))],split=True,fill=False)),1)

    def test_timezone_inference_and_no_fabricated_last_stop(self):
        start=self.dt(0,23,30).astimezone(datetime.timezone.utc)
        rows=[self.row(start,None),self.row(self.dt(1,0,30),None,'下一档')]
        records=project(rows,split=True,fill=False)
        self.assertEqual(records[0]['starttime'].hour,23)
        self.assertTrue(records[1]['inferred_end'])
        self.assertEqual(records[-1]['endtime'].strftime('%H:%M:%S'),'23:59:59')
        self.assertEqual(records[1]['starttime'].hour,0)

    def test_raw_ingest_keeps_tomorrow_and_toggle_is_reversible(self):
        from .ingest import save_programmes
        late=self.row(self.dt(0,23,30),self.dt(1,0,30))
        tomorrow=self.row(self.dt(1,1),self.dt(1,2),'明日完整档')
        save_programmes(self.channel,{'source':'hks','epgs':[tomorrow]})
        save_programmes(self.channel,{'source':'hks','epgs':[late]})
        self.assertEqual(Epg.objects.count(),2)
        self.assertEqual(Epg.objects.get(title='长节目').endtime,self.dt(1,0,30))
        next_rows=Epg.get_single_epg(Epg,self.channel,self.day+datetime.timedelta(days=1))
        self.assertEqual([(row['starttime'],row['title']) for row in next_rows],[('00:00','长节目'),('01:00','明日完整档'),('02:00','精彩继续')])
        save_programmes(self.channel,{'source':'hks','epgs':[tomorrow]})
        self.assertEqual(len(Epg.get_single_epg(Epg,self.channel,self.day+datetime.timedelta(days=1))),3)
        cfg=get_config(); cfg.split_midnight=False; cfg.fill_programme_gaps=False; cfg.save()
        self.assertEqual(len(Epg.get_single_epg(Epg,self.channel,self.day+datetime.timedelta(days=1))),1)
        self.assertEqual(next(row for row in Epg.get_single_epg(Epg,self.channel,self.day) if row['title']=='长节目')['endtime'],'00:30')
        self.assertEqual(Epg.objects.count(),2)

    def test_missing_yesterday_stop_infers_to_first_today(self):
        for row in [self.row(self.dt(-1,23),None,'昨日末档'),self.row(self.dt(0,0,30),self.dt(0,1),'今日首档')]:
            Epg.objects.create(channel_id=str(self.channel.pk),title=row['title'],starttime=row['starttime'],endtime=row['endtime'],program_date=row['program_date'],source='hks')
        rows=Epg.get_single_epg(Epg,self.channel,self.day)
        self.assertEqual((rows[0]['starttime'],rows[0]['endtime']),('00:00','00:30'))
        self.assertTrue(rows[0]['inferred_end'])

    def test_previous_context_is_added_without_replacing_yesterdays_other_rows(self):
        from .programmes import add_previous_context
        from .ingest import save_programmes
        old=self.row(self.dt(-1,8),self.dt(-1,9),'昨日已有整档')
        save_programmes(self.channel,{'source':'hks','epgs':[old]})
        previous=self.row(self.dt(-1,23,30),self.dt(0,0,30),'昨日跨日档')
        today=self.row(self.dt(0,0,30),self.dt(0,1),'今日首档')
        with patch('crawl.spiders.epg_func',return_value={'epgs':[previous]}) as fetch:
            result=add_previous_context(self.channel,{'source':'hks','epgs':[today]},self.day,'hks','hks')
        self.assertEqual(len(result['context_epgs']),1)
        save_programmes(self.channel,result)
        self.assertEqual(Epg.objects.count(),3)
        self.assertTrue(Epg.objects.filter(title='昨日已有整档').exists())
        with patch('crawl.spiders.epg_func') as fetch:
            add_previous_context(self.channel,{'source':'hks','epgs':[today]},self.day,'hks','hks')
            fetch.assert_not_called()

    def test_export_and_preview_share_splitting_and_window(self):
        from .ingest import save_programmes
        from .preview_runner import normalize
        late=self.row(self.dt(0,23,30),self.dt(1,0,30))
        save_programmes(self.channel,{'source':'hks','epgs':[late]})
        self.assertEqual(normalize({'epgs':[late]},self.day)[-1]['end'][11:19],'23:59:59')
        from crawl import crawl
        with tempfile.TemporaryDirectory() as directory,patch.dict(crawl.dirs,{'share':directory}):
            count,path=crawl.gen_xml('all')
            records=ET.parse(path).getroot().findall("programme[@channel='跨日台']")
            self.assertEqual(count,4); self.assertEqual(len(records),4)
            self.assertIn('235959',records[1].get('stop'))
            self.assertIn('000000',records[2].get('start'))
            cfg=get_config(); cfg.options={'export_days':1}; cfg.save()
            count,path=crawl.gen_xml('all')
            self.assertEqual(count,2)

    def test_excluded_group_does_not_leave_orphan_programmes(self):
        from .ingest import save_programmes
        save_programmes(self.channel,{'source':'hks','epgs':[self.row(self.dt(0,8),self.dt(0,9))]})
        cfg=get_config(); cfg.options={'exclude_categories':['央视']}; cfg.save()
        from crawl import crawl
        with tempfile.TemporaryDirectory() as directory,patch.dict(crawl.dirs,{'share':directory}):
            count,path=crawl.gen_xml('all')
            root=ET.parse(path).getroot()
            self.assertIsNone(root.find("channel[@id='跨日台']")); self.assertEqual(count,0)
            self.assertIsNone(root.find("programme[@channel='跨日台']"))


class SettingsTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_superuser('settings-test',password='test-only')
        self.client.force_login(self.user)

    def data(self,**updates):
        cfg=get_config()
        data={key:getattr(cfg,key) for key in SettingsForm.Meta.fields}
        data.update(options(cfg)); data['include_channels']='\n'.join(data['include_channels'])
        return {**data,'_settings_full':'1',**updates}

    def test_settings_save_persist_hot_apply_and_permissions(self):
        response=self.client.post('/admin/settings/',self.data(split_midnight=False,list_per_page=10,site_name='自定义节目站',admin_name='自定义后台',preview_timeout=25))
        self.assertEqual(response.status_code,302)
        cfg=get_config(); self.assertFalse(cfg.split_midnight); self.assertEqual(cfg.options['preview_timeout'],25)
        self.assertContains(self.client.get('/'),'自定义节目站')
        response=self.client.get('/admin/web/channel/')
        self.assertEqual(response.context['cl'].list_per_page,10)
        self.assertContains(response,'自定义后台')
        self.assertEqual(Task.objects.get().kind,'xml')
        user=get_user_model().objects.create_user('settings-read',is_staff=True)
        user.user_permissions.add(Permission.objects.get(codename='view_runtimeconfig'))
        self.client.force_login(user)
        self.assertEqual(self.client.get('/admin/settings/').status_code,200)
        self.assertEqual(self.client.post('/admin/settings/',self.data()).status_code,403)

    def test_settings_validation_and_unchecked_flags(self):
        for update in ({'export_days':5,'crawl_days':2},{'cc_filename':'../bad.xml'},{'local_filename':'all.xml'},{'xmltv_download_mib':100,'xmltv_expanded_mib':10},{'preview_timeout':999}):
            form=SettingsForm(self.data(**update),instance=get_config())
            self.assertFalse(form.is_valid(),update)
        data=self.data(); data.pop('split_midnight'); data.pop('fallback')
        form=SettingsForm(data,instance=get_config()); self.assertTrue(form.is_valid(),form.errors)
        cfg=form.save(); self.assertFalse(cfg.split_midnight); self.assertFalse(cfg.options['fallback'])

    def test_source_parameter_ui_removed(self):
        self.assertEqual(self.client.get('/admin/web/sourcesetting/').status_code,404)
        self.assertNotContains(self.client.get('/admin/settings/'),'来源参数')

    def test_preview_cache_changes_with_processing_settings(self):
        from .preview import preview
        with tempfile.TemporaryDirectory() as directory, self.settings(DATA_DIR=directory),patch('web.preview.run_preview',return_value={'status':'success','programmes':[]}) as run:
            self.assertFalse(preview('hks','hks','测试')['cached'])
            self.assertTrue(preview('hks','hks','测试')['cached'])
            cfg=get_config(); cfg.split_midnight=not cfg.split_midnight; cfg.save()
            self.assertFalse(preview('hks','hks','测试')['cached'])
            self.assertEqual(run.call_count,2)

    def test_prepared_inference_flag_is_stored(self):
        from .ingest import save_programmes
        channel=Channel.objects.create(name='推算台',tvg_name='推算台',sort='央视',source='hks',channel_id='<hks:hks>')
        first=timezone.now().replace(hour=8,minute=0,second=0,microsecond=0)
        records=[{'channel_id':channel.pk,'title':'推算','starttime':first,'endtime':None,'program_date':first.date()},
                 {'channel_id':channel.pk,'title':'下一档','starttime':first+datetime.timedelta(hours=1),'endtime':None,'program_date':first.date()}]
        save_programmes(channel,{'source':'hks','epgs':records})
        self.assertTrue(Epg.objects.get(title='推算').end_inferred)
        self.assertFalse(Epg.objects.get(title='下一档').end_inferred)
