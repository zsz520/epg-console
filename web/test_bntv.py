import datetime
from unittest.mock import Mock,patch
from django.test import TestCase
from django.utils import timezone
from .models import Channel,Channel_list,Epg
from crawl.spiders import epg_func, epg_source
from crawl.source_registry import registry as _registry, load as _load
bntv = _load(_registry()[0]['bntv'])
from .catalog import refresh_catalog,import_catalog

class BntvTests(TestCase):
    def setUp(self):
        self.day=datetime.datetime.now(bntv.ZONE).date()
        self.channel=Channel.objects.create(name='巴南综合',tvg_name='巴南综合',sort='重庆',source='bntv',channel_id='<bntv:28>',has_epg=1,ineed=1)
    def catalogue(self):
        return {'code':0,'data':{'hasNext':True,'dataList':[{'categoryData':None,'radioOrTelevisions':[{'id':28,'name':'巴南电视','icon':'https://example.com/logo.png','url':'https://example.com/live.m3u8'}]}]}}
    def programmes(self):
        return {'code':0,'data':[{'appId':34,'programDate':self.day.isoformat()+' 00:00:00','programStartTime':t,'programName':name} for t,name in [('08:05','一周&nbsp;要闻'),('08:25','<b>我们的婚姻</b>')]]}
    def test_catalogue_real_structure_and_mapping(self):
        channels=bntv.parse_channels(self.catalogue())
        self.assertEqual(channels[0]['id'],['28']);self.assertEqual(channels[0]['name'],'巴南电视')
        self.assertEqual(channels[0]['sort'],'重庆');self.assertEqual(channels[0]['url'],'')
        refresh_catalog('bntv',channels)
        imported=import_catalog([Channel_list.objects.get(source='bntv').pk])
        self.assertTrue(Channel.objects.filter(source='bntv',channel_id='<bntv:28>').exists())
        self.assertIn('bntv',epg_source)
    def test_start_only_real_dates_no_source_placeholder(self):
        rows=bntv.parse_programmes(self.programmes(),self.channel,self.day)
        self.assertEqual(len(rows),2);self.assertEqual(rows[0]['title'],'一周 要闻')
        self.assertIsNone(rows[-1]['endtime']);self.assertEqual(rows[0]['channel_id'],self.channel.pk)
        self.assertEqual(rows[0]['starttime'].utcoffset(),datetime.timedelta(hours=8))
        with self.assertRaisesRegex(ValueError,'请求日期'):
            bntv.parse_programmes(self.programmes(),self.channel,self.day+datetime.timedelta(days=1))
    def test_wrong_channel_and_non_today_never_request(self):
        with patch.object(bntv,'request_json') as request:
            for identifier,day in [('34',self.day),('28',self.day+datetime.timedelta(days=1)),('28',self.day-datetime.timedelta(days=1))]:
                result=bntv.get_epgs_bntv(self.channel,identifier,day)
                self.assertFalse(result['success']);self.assertEqual(result['epgs'],[])
            request.assert_not_called()
    def test_bad_programme_preserves_snapshot(self):
        for field,value in [('appId',35),('programDate','invalid'),('programStartTime','25:99'),('programName','')]:
            payload=self.programmes();payload['data'][0][field]=value
            with self.assertRaises(ValueError):bntv.parse_programmes(payload,self.channel,self.day)
        with self.assertRaises(ValueError):bntv.parse_channels({'data':{'dataList':[]}})
    def test_public_request_uses_fresh_session_and_standard_tls(self):
        response=Mock(status_code=200);response.json.return_value=self.catalogue()
        with patch.object(bntv.requests,'post',return_value=response) as post:
            bntv.get_channels_bntv();bntv.get_channels_bntv()
            calls=post.call_args_list
            self.assertNotEqual(calls[0].kwargs['data']['sessionId'],calls[1].kwargs['data']['sessionId'])
            self.assertEqual(calls[0].kwargs['data']['appId'],'34')
            self.assertEqual(calls[0].kwargs['data']['type'],'2')
            self.assertNotIn('verify',calls[0].kwargs)
            self.assertNotIn('sign',calls[0].kwargs['headers'])
    def test_failure_messages_do_not_echo_response_or_credentials(self):
        response=Mock(status_code=200);response.json.return_value={'code':9,'message':'private-token'}
        with patch.object(bntv.requests,'post',return_value=response):
            result=bntv.get_epgs_bntv(self.channel,'28',self.day)
        self.assertFalse(result['success']);self.assertNotIn('private-token',result['msg'])
    def test_registered_worker_ingest_and_shared_processing(self):
        from .ingest import save_programmes
        from .preview_runner import normalize
        with patch.object(bntv,'request_json',return_value=self.programmes()):
            result=epg_func(self.channel,'28',self.day,source='bntv')
        self.assertTrue(result['success']);result['source']='bntv'
        save_programmes(self.channel,result)
        self.assertEqual(Epg.objects.filter(channel_id=str(self.channel.pk)).count(),2)
        rows=Epg.get_single_epg(Epg,self.channel,self.day)
        self.assertEqual((rows[0]['title'],rows[0]['starttime'],rows[0]['endtime']),('精彩继续','00:00','08:05'))
        self.assertEqual(rows[-1]['endtime'],'23:59')
        self.assertEqual(len(normalize(result,self.day)),3)
