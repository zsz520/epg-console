import datetime,json
from types import SimpleNamespace
from unittest.mock import patch,Mock
from pathlib import Path
from django.test import TestCase
from crawl.source_registry import registry,load
from .models import Channel,Epg

tvb=load(registry()[0]['tvb'])

class TvbTests(TestCase):
    def setUp(self):
        self.day=datetime.date(2026,11,23)
        self.channel=Channel.objects.create(name='翡翠台测试',tvg_name='翡翠台测试',sort='香港',source='tvb',channel_id='<tvb:J>')
    def row(self,day,hour,minute,title):
        start=datetime.datetime.combine(self.day+datetime.timedelta(days=day),datetime.time(hour,minute),tvb.ZONE)
        return {'network_code':'J','event_time':int(start.timestamp()),'event_datetime':start.strftime('%Y-%m-%d %H:%M:%S'),'programme_title':title}
    def payload(self,date,rows):
        return date,{'code':0,'data':{'list':[{'network_code':'j','ref_date':int(date.strftime('%Y%m%d')),'schedules':rows}]}}
    def examples(self):
        return [self.payload(self.day-datetime.timedelta(days=1),[self.row(-1,23,40,'昨晚'),self.row(0,0,35,'凌晨'),self.row(0,5,0,'清晨')]),
                self.payload(self.day,[self.row(0,6,0,'早晨'),self.row(0,23,55,'晚档'),self.row(1,0,25,'次日节目')])]
    def test_channel_literal_handles_escapes_overview_and_strings(self):
        script='const changedName=[{name:"总览",nameEn:"",code:"",num:null},{name:"\\u7fe1\\u7fe0台",nameEn:"jade",code:"J",description:"a,{word: value},x",num:81},{name:"明珠台",nameEn:"pearl",code:"P",num:84}];'
        rows=tvb.parse_channels(script)
        self.assertEqual([r['id'] for r in rows],[['J'],['P']]);self.assertEqual(rows[0]['name'],'翡翠台')
        self.assertEqual(rows[0]['desc'],'a,{word: value},x')
    def test_dynamic_asset_discovery_needs_no_hardcoded_filename(self):
        def fetch(url,params=None):
            if url.endswith('/'):return '<script src="/assets/index.newhash.js"></script>'
            if url.endswith('newhash.js'):return 'import{e}from"./index.changedchunk.js";'
            return 'const x=[{name:"翡翠台",nameEn:"jade",code:"J",num:81}];'
        with patch.object(tvb,'fetch_text',side_effect=fetch) as call:
            self.assertEqual(tvb.get_channels_tvb()[0]['id'],['J'])
        self.assertEqual(call.call_args_list[-1].args[0],tvb.SITE+'/assets/index.changedchunk.js')
    def test_calendar_day_includes_previous_broadcast_morning_and_excludes_next_date(self):
        rows=tvb.parse_programmes(self.examples(),self.channel,'J',self.day)
        self.assertEqual([r['title'] for r in rows],['凌晨','清晨','早晨','晚档'])
        self.assertEqual(rows[-1]['endtime'].date(),self.day+datetime.timedelta(days=1))
        self.assertEqual(rows[1]['endtime'].strftime('%H:%M'),'06:00')
        self.assertTrue(all(r['program_date']==self.day for r in rows))
    def test_wrong_owner_ref_date_and_timestamp_are_rejected(self):
        for change in ['owner','date','stamp']:
            samples=self.examples()
            if change=='owner':samples[0][1]['data']['list'][0]['network_code']='P'
            elif change=='date':samples[0][1]['data']['list'][0]['ref_date']=19990101
            else:samples[0][1]['data']['list'][0]['schedules'][0]['event_time']+=60
            with self.assertRaises(ValueError):tvb.parse_programmes(samples,self.channel,'J',self.day)
    def test_empty_and_business_errors_do_not_fake_success(self):
        for payload in [{'code':9},{'code':0,'data':{'list':[]}}]:
            with self.assertRaises(ValueError):tvb.parse_programmes([(self.day,payload)],self.channel,'J',self.day)
        self.assertEqual(tvb.parse_channels('const x=[{name:"坏",nameEn:"",code:runAnything()}]'),[])
    def test_lowercase_channel_code_and_both_broadcast_dates(self):
        samples=dict(self.examples())
        with patch.object(tvb,'fetch_schedule',side_effect=lambda date,identifier:(date,samples[date])) as fetch:
            result=tvb.get_epgs_tvb(self.channel,'j',self.day)
        self.assertTrue(result['success']);self.assertEqual(len(result['epgs']),4)
        self.assertEqual({c.args for c in fetch.call_args_list},{(self.day,'J'),(self.day-datetime.timedelta(days=1),'J')})
    def test_network_failure_keeps_tls_and_has_bounded_retry(self):
        with patch.object(tvb.requests,'get',side_effect=tvb.requests.ConnectionError('private-url')) as get:
            with self.assertRaisesRegex(ValueError,'网络请求失败'):tvb.fetch_text(tvb.SITE+'/')
        self.assertEqual(get.call_count,2);self.assertNotIn('verify',get.call_args.kwargs)
    def test_conflicting_simultaneous_programmes_are_rejected(self):
        samples=self.examples();row=dict(samples[1][1]['data']['list'][0]['schedules'][0]);row['programme_title']='冲突';samples[1][1]['data']['list'][0]['schedules'].append(row)
        with self.assertRaisesRegex(ValueError,'冲突'):tvb.parse_programmes(samples,self.channel,'J',self.day)
    def test_shared_ingest_keeps_raw_times_and_aligns_midnight(self):
        from .ingest import save_programmes
        rows=tvb.parse_programmes(self.examples(),self.channel,'J',self.day)
        save_programmes(self.channel,{'source':'tvb','epgs':rows})
        yesterday=self.row(-1,23,40,'昨晚')
        Epg.objects.create(channel_id=str(self.channel.pk),program_date=self.day-datetime.timedelta(days=1),starttime=datetime.datetime.fromtimestamp(yesterday['event_time'],tvb.ZONE),endtime=rows[0]['starttime'],title='昨晚',source='tvb')
        projected=Epg.get_single_epg(Epg,self.channel,self.day)
        self.assertEqual((projected[0]['starttime'],projected[0]['endtime'],projected[0]['title']),('00:00','00:35','昨晚'))
        self.assertEqual(projected[-1]['endtime'],'23:59')
        self.assertEqual(Epg.objects.get(title='晚档').endtime.astimezone(tvb.ZONE).strftime('%H:%M'),'00:25')
    def test_zhongshu_removed_and_fresh_seed_has_no_references(self):
        from crawl.source_registry import LEGACY,discover
        root=Path(__file__).resolve().parents[1]
        self.assertFalse((root/'crawl/spiders/zhongshu.py').exists());self.assertFalse((root/'crawl/spiders/tvb.py').exists())
        self.assertNotIn('zhongshu',LEGACY);self.assertNotIn('tvb',LEGACY)
        self.assertNotIn('zhongshu',registry()[0]);self.assertTrue(registry()[0]['tvb'].external)
        seeds=json.loads((root/'docker/channels.json').read_text())
        self.assertTrue(all(row['fields']['source']!='zhongshu' and '<zhongshu:' not in row['fields']['channel_id'] for row in seeds))
