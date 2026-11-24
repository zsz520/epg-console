import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from django.test import SimpleTestCase
from crawl.source_registry import load, registry, LEGACY

viu = load(registry()[0]['viu'])


class ViuTests(SimpleTestCase):
    day = datetime.date(2026, 11, 23)
    channel = SimpleNamespace(pk=7)

    def row(self, offset=0, hour=1, code=96):
        start = datetime.datetime.combine(self.day + datetime.timedelta(days=offset), datetime.time(hour), viu.ZONE)
        return {'channelId': code, 'start': int(start.timestamp()*1000),
                'end': int((start+datetime.timedelta(hours=1)).timestamp()*1000),
                'program_title': '节目', 'episode_title': '第1集', 'short_synopsis': '简介'}

    def test_discovers_both_official_channel_links(self):
        rows = viu.parse_channels('<a href="/epg/99">ViuTV <span>Ch. 99</span></a><a href="/epg/96">ViuTVsix <span>Ch. 96</span></a>')
        self.assertEqual([(r['id'][0],r['name']) for r in rows], [('99','ViuTV'),('96','ViuTVsix')])

    def test_empty_directory_preserves_existing(self):
        with self.assertRaises(ValueError): viu.parse_channels('<html>维护中</html>')

    def test_real_requested_date_only_and_description(self):
        result = viu.parse_programmes({'epgs':[self.row(-1),self.row(),self.row(1)]},self.channel,'96',self.day)
        self.assertEqual(len(result),1)
        self.assertEqual(result[0]['desc'],'第1集 -- 简介')
        self.assertEqual(result[0]['starttime'].strftime('%H:%M'),'01:00')

    def test_cross_midnight_keeps_original_end_for_shared_processing(self):
        result = viu.parse_programmes({'epgs':[self.row(hour=23)]},self.channel,'96',self.day)
        self.assertEqual(result[0]['endtime'].date(),self.day+datetime.timedelta(days=1))

    def test_feed_last_record_without_end_does_not_break_other_dates(self):
        last=self.row(offset=6);last['end']=None
        result=viu.parse_programmes({'epgs':[self.row(),last]},self.channel,'96',self.day)
        self.assertEqual(len(result),1)
        result=viu.parse_programmes({'epgs':[last]},self.channel,'96',self.day+datetime.timedelta(days=6))
        self.assertIsNone(result[0]['endtime'])

    def test_channel_id_selects_correct_endpoint_and_old_alias(self):
        for identifier,code in [('96','96'),('99','99'),('viu','99')]:
            with patch.object(viu,'fetch',return_value=Mock(json=lambda:{'epgs':[self.row(code=int(code))]})) as fetch:
                self.assertEqual(viu.get_epgs_viu(self.channel,identifier,self.day)['success'],1)
            self.assertEqual(fetch.call_args.args[0],viu.API+code)

    def test_wrong_channel_invalid_time_empty_and_conflict_fail(self):
        conflict=self.row();conflict['program_title']='冲突'
        invalid=self.row();invalid['end']=invalid['start']
        for rows in [[self.row(code=99)],[invalid],[],[self.row(),conflict]]:
            with self.assertRaises(ValueError):viu.parse_programmes({'epgs':rows},self.channel,'96',self.day)

    def test_network_failure_retries_without_disabling_tls(self):
        with patch.object(viu.requests,'get',side_effect=viu.requests.ConnectionError('private-url')) as get:
            result=viu.get_epgs_viu(self.channel,'96',self.day)
        self.assertEqual(result['success'],0);self.assertEqual(get.call_count,2)
        self.assertNotIn('private-url',result['msg']);self.assertNotIn('verify',get.call_args.kwargs)

    def test_external_only_and_invalid_id_never_fetches(self):
        self.assertTrue(registry()[0]['viu'].external);self.assertNotIn('viu',LEGACY)
        self.assertFalse((Path(__file__).resolve().parents[1]/'crawl/spiders/viu.py').exists())
        with patch.object(viu,'fetch') as fetch:
            self.assertEqual(viu.get_epgs_viu(self.channel,'../99',self.day)['success'],0)
            fetch.assert_not_called()
