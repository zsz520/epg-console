import datetime
from types import SimpleNamespace
from unittest.mock import Mock, patch
from django.test import TestCase
from crawl.source_registry import registry as _registry, load as _load
hks = _load(_registry()[0]['hks'])
from .models import Channel, Epg
from .ingest import save_programmes


class HksTests(TestCase):
    date = datetime.date(2026, 11, 22)

    def row(self, **extra):
        return {'channel_id': 12, 'title': '跨午夜节目', 'play_time': '23:50:00',
                'duration': 900, 'program_date': '2026-11-21T16:00:00.000Z', **extra}

    def test_timezone_duration_and_internal_id(self):
        rows = hks.parse_programmes({'data': [self.row()]}, SimpleNamespace(id=77), self.date, 12)
        self.assertEqual(rows[0]['channel_id'], 77)
        self.assertEqual(rows[0]['starttime'].date(), self.date)
        self.assertEqual(rows[0]['endtime'].date(), self.date + datetime.timedelta(days=1))
        self.assertEqual(rows[0]['endtime'].strftime('%H:%M'), '00:05')
        self.assertEqual(rows[0]['starttime'].utcoffset(), datetime.timedelta(hours=8))

    def test_dates_channels_and_invalid_records_do_not_shift_or_invent(self):
        for payload in [{'data': []}, {'data': [self.row(channel_id=99)]},
                        {'data': [self.row(program_date='2026-11-20T16:00:00.000Z')]},
                        {'data': [self.row(duration=0)]}, {'data': [self.row(play_time='25:00')]},
                        {'data': [self.row(program_date=None)]}]:
            with self.assertRaises(ValueError):
                hks.parse_programmes(payload, SimpleNamespace(id=77), self.date, 12)

    @patch.object(hks, 'request_json')
    def test_legacy_mapping_and_official_directory(self, request):
        request.side_effect = [{'data': {'id': 12, 'channel': '香港卫视'}}, {'data': [self.row()]}]
        result = hks.get_epgs_hks(SimpleNamespace(id=77), 'hks', self.date)
        self.assertEqual(result['success'], 1)
        self.assertEqual(request.call_args.kwargs['params']['date'], '2026-11-22')
        request.side_effect = None
        request.return_value = {'data': {'id': 12, 'channel': '香港卫视'}}
        channel = hks.get_channels_hks()[0]
        self.assertEqual(channel['id'], ['hks'])
        self.assertEqual(channel['url'], 'https://www.hkstv.tv/live')

    @patch.object(hks, 'request_json', side_effect=ValueError('request failed'))
    def test_failure_never_references_unassigned_start_time(self, request):
        result = hks.get_epgs_hks(SimpleNamespace(id=77), 'hks', self.date)
        self.assertEqual(result['success'], 0)
        self.assertEqual(result['epgs'], [])
        self.assertEqual(result['last_program_date'], self.date)

    @patch.object(hks.requests, 'get', return_value=Mock(status_code=503))
    def test_http_failure_is_not_parsed_as_programmes(self, request):
        result = hks.get_epgs_hks(SimpleNamespace(id=77), 'hks', self.date)
        self.assertEqual(result['success'], 0)
        self.assertIn('503', result['msg'])

    def test_empty_result_preserves_database(self):
        channel = Channel.objects.create(name='香港卫视测试', tvg_name='香港卫视测试', sort='香港', source='hks', channel_id='<hks:hks>')
        start = datetime.datetime(2026, 11, 22, 23, 50, tzinfo=hks.ZONE)
        Epg.objects.create(channel_id=str(channel.pk), title='原节目', program_date=self.date, starttime=start)
        with patch.object(hks, 'request_json', side_effect=[{'data': {'id': 12, 'channel': '香港卫视'}}, {'data': []}]):
            result = hks.get_epgs_hks(channel, 'hks', self.date)
        with self.assertRaises(ValueError):
            save_programmes(channel, result)
        self.assertEqual(Epg.objects.get().title, '原节目')
