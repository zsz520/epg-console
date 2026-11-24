from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from .models import Channel
from .catalog import parse_mapping
from .quick_edit import FIELDS, revision
from .runtime_config import get_config


class SourceOrder2103Tests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_superuser('order2103',password='test-only')
        self.client.force_login(self.user)
        self.channel=Channel.objects.create(name='排序测试',tvg_name='排序测试',sort='央视',
            source='cctv',channel_id='<tvmao:custom-2><cctv:cctv8k><tvsou:hidden-3>',has_epg=1,ineed=1)

    def test_primary_normalized_first_without_changing_backup_order(self):
        self.channel.refresh_from_db()
        self.assertEqual(list(parse_mapping(self.channel.channel_id)),['cctv','tvmao','tvsou'])
        self.assertIn('<tvmao:custom-2>',self.channel.channel_id)
        self.channel.source='tvsou';self.channel.save()
        self.channel.refresh_from_db()
        self.assertEqual(list(parse_mapping(self.channel.channel_id)),['tvsou','cctv','tvmao'])

    def test_quick_save_custom_id_and_drag_order_persist(self):
        data={key:getattr(self.channel,key) or '' for key in FIELDS}
        data.update(_revision=revision(self.channel),source='tvmao',
            channel_id='<tvmao:custom-2><tvsou:hidden-3><cctv:cctv8k>')
        response=self.client.post(f'/admin/channels/{self.channel.pk}/quick/',data)
        self.assertTrue(response.context['saved'])
        self.channel.refresh_from_db()
        self.assertEqual(self.channel.channel_id,data['channel_id'])
        self.assertEqual(self.channel.source,'tvmao')

    def test_inline_primary_change_persists_as_first_id(self):
        data={'form-TOTAL_FORMS':'1','form-INITIAL_FORMS':'1','form-MIN_NUM_FORMS':'0',
            'form-MAX_NUM_FORMS':'1000','form-0-id':str(self.channel.pk),'_save':'保存'}
        for field in ('name','tvg_id','tvg_name','sort','has_epg','ineed'):
            data['form-0-'+field]=str(getattr(self.channel,field))
        data['form-0-source']='tvsou'
        self.assertEqual(self.client.post('/admin/web/channel/',data).status_code,302)
        self.channel.refresh_from_db()
        self.assertEqual(list(parse_mapping(self.channel.channel_id)),['tvsou','cctv','tvmao'])

    def test_crawl_tries_exact_saved_priority_and_stops_on_success(self):
        from crawl import crawl
        self.channel.source='tvmao'
        self.channel.channel_id='<tvmao:custom-2><tvsou:hidden-3><cctv:cctv8k>'
        self.channel.save()
        cfg=get_config();cfg.options={'retries':1,'retry_delay_seconds':0,'fallback':True};cfg.save()
        calls=[]
        def fetch(channel,identifier,day,**kwargs):
            calls.append((kwargs['source'],identifier))
            return {'success':int(len(calls)==3),'epgs':[{'title':'节目'}] if len(calls)==3 else [],'ban':0}
        with patch.object(crawl,'epg_func',side_effect=fetch),patch('web.programmes.add_previous_context',side_effect=lambda c,r,*a:r):
            result=crawl.get_epg(self.channel,timezone.localdate())
        self.assertEqual(calls,[('tvmao','custom-2'),('tvsou','hidden-3'),('cctv','cctv8k')])
        self.assertEqual(result['source'],'cctv')

    def test_fallback_disabled_only_calls_primary(self):
        from crawl import crawl
        cfg=get_config();cfg.options={'retries':1,'fallback':False};cfg.save()
        with patch.object(crawl,'epg_func',return_value={'success':0,'epgs':[],'ban':0}) as fetch:
            crawl.get_epg(self.channel,timezone.localdate())
        self.assertEqual(fetch.call_count,1)
        self.assertEqual(fetch.call_args.kwargs['source'],'cctv')

    def test_legacy_primary_in_middle_then_exact_id_order_with_per_source_retries(self):
        from crawl import crawl
        Channel.objects.filter(pk=self.channel.pk).update(
            channel_id='<tvsou:third><tvmao:second><cctv:first><nowtv:fourth>')
        self.channel.refresh_from_db()
        cfg=get_config();cfg.options={'retries':2,'retry_delay_seconds':0,'fallback':True};cfg.save()
        with patch.object(crawl,'epg_func',return_value={'success':0,'epgs':[],'ban':0}) as fetch:
            crawl.get_epg(self.channel,timezone.localdate())
        self.assertEqual([(call.kwargs['source'],call.args[1]) for call in fetch.call_args_list],
            [('cctv','first')]*2+[('tvsou','third')]*2+[('tvmao','second')]*2+[('nowtv','fourth')]*2)

    def test_id_order_skips_uninstalled_source_and_stops_on_backup_success(self):
        from crawl import crawl
        Channel.objects.filter(pk=self.channel.pk).update(
            channel_id='<uninstalled:x><tvmao:backup><cctv:primary><tvsou:last>')
        self.channel.refresh_from_db()
        cfg=get_config();cfg.options={'retries':2,'retry_delay_seconds':0,'fallback':True};cfg.save()
        calls=[]
        def fetch(channel,identifier,day,**kwargs):
            calls.append((kwargs['source'],identifier))
            return {'success':int(len(calls)==3),'epgs':[{'title':'成功节目'}] if len(calls)==3 else [],'ban':0}
        with patch.object(crawl,'epg_func',side_effect=fetch),patch('web.programmes.add_previous_context',side_effect=lambda c,r,*a:r):
            result=crawl.get_epg(self.channel,timezone.localdate())
        self.assertEqual(calls,[('cctv','primary'),('cctv','primary'),('tvmao','backup')])
        self.assertEqual(result['source'],'tvmao')
