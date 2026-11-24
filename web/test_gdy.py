import datetime
import hashlib
from types import SimpleNamespace
from unittest.mock import Mock,patch
from django.test import TestCase
from .models import Channel,Channel_list,Epg
from .catalog import refresh_catalog
from crawl.spiders import epg_source, epg_func
from crawl.source_registry import registry as _registry, load as _load
gdy = _load(_registry()[0]['gdy'])

class GdyTests(TestCase):
    def setUp(self):
        self.day=datetime.date(2026,11,22)
        self.channel=Channel.objects.create(name='广电测试',tvg_name='广电测试',sort='重庆',source='gdy',channel_id='<gdy:cctv1HD>',has_epg=1,ineed=1)
    def raw_row(self,identifier='cctv1HD',name='CCTV1综合高清'):
        return {'identifier':identifier,'name':name,'logo':''}
    def epg_payload(self):
        start=datetime.datetime(2026,11,22,23,30,tzinfo=gdy.ZONE);end=start+datetime.timedelta(hours=1)
        return {'errcode':0,'data':{'results':[{'channelId':'cctv1HD','epgName':'晚间节目','startTimeStamps':str(int(start.timestamp()*1000)),'endTimeStamps':str(int(end.timestamp()*1000))}]}}
    def test_app_signature_sorted_fresh_and_business_errors(self):
        with patch.object(gdy.time,'time',return_value=1234),patch.object(gdy,'_json',return_value={'errcode':0}) as call:
            gdy.portal('/live/epg/list',{'date':'2026-11-22','cityId':'5A','channelId':'CaseID'})
        params={'date':'2026-11-22','cityId':'5A','channelId':'CaseID','appId':gdy.APP_ID,'timestamps':'1234000'}
        expected=hashlib.md5((gdy.APP_SECRET+''.join(k+params[k] for k in sorted(params))).encode()).hexdigest()
        self.assertEqual(call.call_args.args[2]['sign'],expected)
        self.assertEqual(call.call_args.args[1]['cityId'],'5A')
        with patch.object(gdy,'_json',return_value={'errcode':4,'errmsg':'sensitive'}):
            with self.assertRaisesRegex(ValueError,'业务错误 4'):gdy.portal('/live/epg/list',{})
    def test_content_id_never_used_and_same_name_ids_not_merged(self):
        payload={'result':{'state':0},'l':{'il':[{'id':'f'*32,'name':'同名台','arg_info':{'import_id':identifier}} for identifier in ['CaseID','caseid','CaseID']]}}
        rows=gdy.parse_cms_channels(payload)
        self.assertEqual([r['identifier'] for r in rows],['CaseID','caseid'])
        with self.assertRaises(ValueError):gdy.parse_cms_channels({'result':{'state':0},'l':{'il':[]}})
    def test_portal_pagination_complete_and_duplicate_guard(self):
        def payload(identifier):return {'data':{'totalCount':2,'results':[{'channelId':identifier,'channelName':identifier}]}}
        with patch.object(gdy,'portal',side_effect=[payload('one'),payload('two')]) as portal:
            self.assertEqual(len(gdy.get_portal_channels()),2)
            self.assertEqual(portal.call_args_list[1].args[1]['pageIndex'],'2')
        with patch.object(gdy,'portal',side_effect=[payload('one'),payload('one')]):
            with self.assertRaisesRegex(ValueError,'重复'):gdy.get_portal_channels()
    def test_all_38_districts_and_both_modes_are_actually_requested(self):
        def fetch(mode,area):return [self.raw_row('shared','共有台'),self.raw_row(area+':'+mode,'同名地方台')]
        with patch.object(gdy,'get_portal_channels',return_value=[self.raw_row('shared','门户名称')]),patch.object(gdy,'get_cms_channels',side_effect=fetch) as cms:
            rows=gdy.get_channels_gdy()
        expected={(mode,area) for area in gdy.AREA_CODES for mode in ('in','out')}
        self.assertEqual({c.args for c in cms.call_args_list},expected)
        self.assertEqual(len(gdy.DISTRICTS),38);self.assertEqual(cms.call_count,82)
        self.assertEqual(len(rows),83)
        shared=next(r for r in rows if r['id']==['shared'])
        self.assertEqual(shared['name'],'门户名称');self.assertEqual(len(shared['regions']),41)
        report=gdy.LAST_CATALOG_REPORT
        self.assertEqual(report['cms_lists'],82)
        self.assertTrue(next(s for s in report['snapshots'] if s['mode']=='in' and s['area']=='100')['same_as_default_ids'])
    def test_one_failed_region_never_publishes_partial_catalogue(self):
        old=[{'id':['keep'],'name':'旧目录','source':'gdy','regions':['500101 万州']}]
        refresh_catalog('gdy',old)
        def fetch(mode,area):
            if area=='500243':raise ValueError('district unavailable')
            return [self.raw_row('new')]
        with patch.object(gdy,'get_portal_channels',return_value=[self.raw_row('new')]),patch.object(gdy,'get_cms_channels',side_effect=fetch):
            with self.assertRaises(ValueError):gdy.get_channels_gdy()
        self.assertEqual(list(Channel_list.objects.values_list('out_channel_id',flat=True)),['keep'])
        self.assertEqual(gdy.LAST_CATALOG_REPORT,{})
    def test_region_metadata_persisted_without_changing_channel_id(self):
        refresh_catalog('gdy',[{'id':['CaseID'],'name':'某区频道','source':'gdy','regions':['500101 万州','500000 重庆总目录']}])
        row=Channel_list.objects.get(source='gdy');self.assertEqual(row.out_channel_id,'CaseID')
        self.assertEqual(row.regions,['500101 万州','500000 重庆总目录'])
    def test_epg_true_timestamp_milliseconds_and_cross_midnight_preserved(self):
        payload=self.epg_payload();rows=gdy.parse_programmes(payload,self.channel,self.day,'cctv1HD')
        self.assertEqual(rows[0]['starttime'].strftime('%H:%M'),'23:30')
        self.assertEqual(rows[0]['endtime'].date(),self.day+datetime.timedelta(days=1))
        payload['data']['results'][0]['startTimeStamps']=int(payload['data']['results'][0]['startTimeStamps'])//1000
        self.assertEqual(gdy.parse_programmes(payload,self.channel,self.day,'cctv1HD')[0]['starttime'],rows[0]['starttime'])
        with self.assertRaisesRegex(ValueError,'不平移日期'):gdy.parse_programmes(payload,self.channel,self.day+datetime.timedelta(days=1),'cctv1HD')
    def test_bad_wrong_channel_and_empty_results_rejected(self):
        for field,value in [('channelId','wrong'),('startTimeStamps','bad'),('endTimeStamps','0'),('epgName','')]:
            payload=self.epg_payload();payload['data']['results'][0][field]=value
            with self.assertRaises(ValueError):gdy.parse_programmes(payload,self.channel,self.day,'cctv1HD')
        with self.assertRaises(ValueError):gdy.parse_programmes({'errcode':0,'data':{'results':[]}},self.channel,self.day,'cctv1HD')
    def test_worker_registration_and_common_ingest_processing(self):
        from .ingest import save_programmes
        self.assertIn('gdy',epg_source)
        with patch.object(gdy,'portal',return_value=self.epg_payload()) as fetch:
            result=epg_func(self.channel,'cctv1HD',self.day,source='gdy')
        self.assertTrue(result['success']);self.assertEqual(fetch.call_args.args[1]['date'],'2026-11-22')
        save_programmes(self.channel,{**result,'source':'gdy'})
        self.assertEqual(Epg.objects.get().endtime.astimezone(gdy.ZONE).strftime('%H:%M'),'00:30')
        rows=Epg.get_single_epg(Epg,self.channel,self.day)
        self.assertEqual(rows[-1]['endtime'],'23:59');self.assertEqual(Epg.objects.count(),1)

    def test_same_real_id_different_category_names_are_aliases(self):
        payload={'result':{'state':0},'l':{'il':[{'id':'x'*32,'name':name,'arg_info':{'import_id':'fulingzh'}} for name in ['涪陵综合','涪陵综合高清']]}}
        rows=gdy.parse_cms_channels(payload)
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]['names'],['涪陵综合','涪陵综合高清'])
