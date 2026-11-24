import datetime
import tempfile
import xml.etree.ElementTree as ET
from unittest.mock import patch
from django.test import TestCase
from django.utils import timezone
from .models import Channel,Epg
from .programmes import project
from .runtime_config import get_config
from .settings_form import SettingsForm

class DayAlignmentTests(TestCase):
    def setUp(self):
        self.day=timezone.localdate()
        self.channel=Channel.objects.create(name='对齐测试',tvg_name='对齐测试',sort='卫视',source='hks',channel_id='<hks:hks>',has_epg=1,ineed=1)
    def dt(self,offset,hour,minute=0):
        return timezone.make_aware(datetime.datetime.combine(self.day+datetime.timedelta(days=offset),datetime.time(hour,minute)))
    def row(self,start,end,title):
        return dict(channel_id=str(self.channel.pk),starttime=start,endtime=end,title=title,source='hks',program_date=start.date())
    def examples(self):
        return [self.row(self.dt(-1,23,30),self.dt(0,0,21),'昨日笑动剧场'),self.row(self.dt(0,0,16),self.dt(0,0,31),'今日文艺播报'),self.row(self.dt(0,1),self.dt(0,2),'下一档')]
    def test_previous_title_uses_today_first_start_without_overlap(self):
        rows=project(self.examples(),date=self.day,split=True,fill=True)
        self.assertEqual([(r['title'],r['starttime'].strftime('%H:%M'),r['endtime'].strftime('%H:%M')) for r in rows[:3]],
                         [('昨日笑动剧场','00:00','00:16'),('今日文艺播报','00:16','00:31'),('精彩继续','00:31','01:00')])
        self.assertEqual(rows[-1]['title'],'精彩继续')
        self.assertEqual(rows[-1]['endtime'].strftime('%H:%M:%S'),'23:59:59')
        self.assertTrue(all(a['endtime']==b['starttime'] for a,b in zip(rows,rows[1:])))
    def test_yesterday_stop_before_midnight_still_uses_last_title(self):
        raw=self.examples();raw[0]['endtime']=self.dt(-1,23,50)
        rows=project(raw,date=self.day,split=True,fill=False)
        self.assertEqual(rows[0]['title'],'昨日笑动剧场')
        self.assertEqual(rows[0]['endtime'],self.dt(0,0,16))
        self.assertEqual(raw[0]['endtime'],self.dt(-1,23,50))
    def test_no_yesterday_fills_head_internal_tail_and_empty_day(self):
        rows=project(self.examples()[1:],date=self.day,split=True,fill=True)
        self.assertEqual([r['title'] for r in rows],['精彩继续','今日文艺播报','精彩继续','下一档','精彩继续'])
        empty=project([],date=self.day,channel_ids=[self.channel.pk],split=True,fill=True)
        self.assertEqual(len(empty),1);self.assertEqual(empty[0]['title'],'精彩继续')
        self.assertEqual(project([],date=self.day,channel_ids=[self.channel.pk],split=True,fill=False),[])
    def test_first_today_midnight_does_not_duplicate_yesterday(self):
        raw=self.examples();raw[1]['starttime']=self.dt(0,0)
        rows=project(raw,date=self.day,split=True,fill=True)
        self.assertEqual(rows[0]['title'],'今日文艺播报')
        self.assertNotIn('昨日笑动剧场',[r['title'] for r in rows])
    def test_overlapping_current_programmes_are_clipped_to_next_start(self):
        raw=self.examples()[1:];raw[0]['endtime']=self.dt(0,1,30)
        rows=project(raw,date=self.day,split=True,fill=True)
        self.assertEqual(rows[1]['endtime'],self.dt(0,1))
        self.assertTrue(all(a['endtime']<=b['starttime'] for a,b in zip(rows,rows[1:])))
    def test_independent_switch_combinations_and_no_raw_mutation(self):
        raw=self.examples()
        for split in [False,True]:
            for fill in [False,True]:
                cfg=get_config();cfg.split_midnight=split;cfg.fill_programme_gaps=fill;cfg.save()
                rows=project(raw,date=self.day)
                self.assertEqual(any(r.get('gap_filler') for r in rows),fill)
                self.assertEqual(any(r['title']=='昨日笑动剧场' for r in rows),split)
        self.assertEqual(raw[0]['endtime'],self.dt(0,0,21))
    def test_database_preview_and_xml_same_alignment_and_filler_title(self):
        from .ingest import save_programmes
        from .preview_runner import normalize
        raw=self.examples()
        save_programmes(self.channel,{'source':'hks','epgs':raw})
        cfg=get_config();cfg.options={'title_suffix':'后缀'};cfg.save()
        public=Epg.get_single_epg(Epg,self.channel,self.day)
        preview=normalize({'epgs':raw},self.day)
        self.assertEqual([(r['title'],r['starttime'],r['endtime']) for r in public],
                         [(r['title'],r['start'][11:16],r['end'][11:16]) for r in preview])
        self.assertEqual(public[2]['title'],'精彩继续')
        from crawl import crawl
        with tempfile.TemporaryDirectory() as directory,patch.dict(crawl.dirs,{'share':directory}):
            _,path=crawl.gen_xml('all')
            nodes=ET.parse(path).getroot().findall("programme[@channel='对齐测试']")
            today=[node for node in nodes if node.get('start').startswith(self.day.strftime('%Y%m%d'))]
            self.assertEqual(today[0].get('stop')[8:14],'001600')
            self.assertEqual(today[1].get('start')[8:14],'001600')
            self.assertEqual(today[2].findtext('title'),'精彩继续')
        self.assertEqual(Epg.objects.count(),3)
    def test_unchecked_gap_switch_saved_independently(self):
        cfg=get_config()
        data={key:getattr(cfg,key) for key in SettingsForm.Meta.fields}
        data.update(_settings_full='1',split_midnight=True)
        data.pop('fill_programme_gaps')
        form=SettingsForm(data,instance=cfg)
        self.assertTrue(form.is_valid(),form.errors)
        saved=form.save()
        self.assertTrue(saved.split_midnight);self.assertFalse(saved.fill_programme_gaps)

    def test_public_source_ignores_filler_metadata(self):
        start=self.dt(0,8)
        Epg.objects.create(channel_id=str(self.channel.pk),program_date=self.day,starttime=start,endtime=self.dt(0,9),title='实际来源节目',source='cctv')
        response=self.client.get('/api/web/',{'channel_id':self.channel.pk})
        self.assertEqual(response.context['source'],'央视')
