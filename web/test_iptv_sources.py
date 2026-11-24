import datetime as dt
from types import SimpleNamespace
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.test import TestCase
from crawl.source_registry import registry as _registry, load as _load
cqct = _load(_registry()[0]['cqct'])
fjyd = _load(_registry()[0]['fjyd'])
yangshipin = _load(_registry()[0]['yangshipin'])
sdgd = _load(_registry()[0]['sdgd'])
gzyd = _load(_registry()[0]['gzyd'])
MODULES = {'cqct': cqct, 'fjyd': fjyd, 'yangshipin': yangshipin, 'sdgd': sdgd, 'gzyd': gzyd}
from .models import Channel, Epg
from .ingest import save_programmes


def varint(number):
    result = bytearray()
    while number > 127:
        result.append((number & 127) | 128)
        number >>= 7
    result.append(number)
    return bytes(result)


def field(key, value):
    if isinstance(value, int):
        return varint(key << 3) + varint(value)
    if isinstance(value, str):
        value = value.encode()
    return varint((key << 3) | 2) + varint(len(value)) + value


class IptvSourceTests(TestCase):
    date = dt.date(2026, 11, 22)

    def test_each_source_loads_standalone_with_standard_entry_points(self):
        import importlib.util
        import inspect
        for source, module in MODULES.items():
            spec = importlib.util.spec_from_file_location('independent_' + source, module.__file__)
            standalone = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(standalone)
            self.assertTrue(callable(getattr(standalone, 'get_channels_' + source)))
            function = getattr(standalone, 'get_epgs_' + source)
            self.assertEqual(list(inspect.signature(function).parameters), ['channel', 'channel_id', 'dt', 'func_arg'])

    def setUp(self):
        self.start = int(dt.datetime(2026, 11, 22, 23, 30, tzinfo=cqct.ZONE).timestamp())

    def test_cqct_and_fjyd_directory_preserves_provider_ids(self):
        rows = cqct.parse_channels({'resultSet': [[{'name': '央视频道', 'channels': [{'id': 72, 'name': 'CCTV-1综合(高清)'}]}]]})
        self.assertEqual(rows[0]['id'], ['72'])
        self.assertEqual(rows[0]['name'], 'CCTV-1综合(高清)')
        fj = fjyd.parse_channels({'content': {'channels': [{'uuid': 'Umai:CHAN/1346@BESTV.SMG.SMG', 'name': 'CCTV-1'}]}})
        from utils.general import channel_ids_to_dict
        text = '<fjyd:' + fj[0]['id'][0] + '>'
        self.assertEqual(channel_ids_to_dict(text)['fjyd'], 'Umai:CHAN/1346@BESTV.SMG.SMG')

    def test_cqct_duration_date_filter_and_internal_id(self):
        def item(start):
            return {'startDatetime': start * 1000, 'durationBySecond': 3600, 'name': '节目'}
        data = {'resultSet': [{'schedules': [{'scheduleDetails': [item(self.start - 86400), item(self.start)]}]}]}
        rows = cqct.parse_programmes(data, SimpleNamespace(id=77), self.date)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['channel_id'], 77)
        self.assertEqual(rows[0]['endtime'].date(), self.date + dt.timedelta(days=1))

    def test_fjyd_seconds_and_sdgd_milliseconds(self):
        fj = {'content': [{'programs': [{'startTime': self.start, 'endTime': self.start + 3600, 'programName': '福建节目'}]}]}
        sd = {'errcode': 0, 'data': {'results': [{'startTimeStamps': self.start * 1000, 'endTimeStamps': (self.start + 3600) * 1000, 'epgName': '山东节目'}]}}
        for source, data in [('fjyd', fj), ('sdgd', sd)]:
            row = MODULES[source].parse_programmes(data, SimpleNamespace(id=77), self.date)[0]
            self.assertEqual(row['starttime'].hour, 23)
            self.assertEqual(row['program_date'], self.date)
            with self.assertRaises(ValueError):
                MODULES[source].parse_programmes(data, SimpleNamespace(id=77), self.date + dt.timedelta(days=1))

    def test_yangshipin_protobuf_and_compact_date_url(self):
        channel = field(2, 'CCTV1') + field(4, '600001859') + field(11, 'yangshi')
        directory = field(1, 200) + field(2, field(2, field(15, channel)))
        rows = yangshipin.parse_channels(directory)
        self.assertEqual(rows[0]['id'], ['600001859'])
        programme = field(2, ' 节目（版权原因不可回看） ') + field(3, self.start) + field(4, self.start + 3600)
        result = yangshipin.parse_programmes(field(1, 200) + field(2, programme), SimpleNamespace(id=77), self.date)
        self.assertEqual(result[0]['title'], '节目')
        self.assertTrue(yangshipin.format_url('600001859', self.date).endswith('/20261122'))
        for bad in [b'\x12\x7f', b'\x09', b'\x80']:
            with self.assertRaises(ValueError):
                yangshipin.protobuf_fields(bad)

    def test_gzyd_cross_midnight_and_quality_separation(self):
        data = {'code': 0, 'result': {'scheduleList': [{'startDate': '2026-11-22', 'startTime': '23:30', 'endTime': '00:30', 'programName': '跨天节目'}]}}
        result = gzyd.parse_programmes(data, SimpleNamespace(id=77), self.date)
        self.assertEqual(result[0]['endtime'].date(), self.date + dt.timedelta(days=1))
        self.assertNotEqual(gzyd.gz_name('CCTV16（高清）'), gzyd.gz_name('CCTV16（4K）'))
        self.assertNotEqual(gzyd.gz_name('CCTV4欧洲'), gzyd.gz_name('CCTV4美洲'))

    def test_sdgd_guest_token_renewed_on_rejection(self):
        replies = [{'errcode': 0, 'data': {'result': {'token': 'fresh-guest-a'}}}, {'errcode': 20120003},
                   {'errcode': 0, 'data': {'result': {'token': 'fresh-guest-b'}}}, {'errcode': 0, 'data': {'results': []}}]
        with patch.object(sdgd, '_token', ''), patch.object(sdgd, '_token_until', 0), patch.object(sdgd, 'request', side_effect=replies) as request:
            result = sdgd.get_data('https://example.test/channels')
        self.assertEqual(result['errcode'], 0)
        self.assertEqual(request.call_args.kwargs['extra_headers']['token'], 'fresh-guest-b')

    def test_sdgd_directory_collects_all_pages(self):
        first = {'errcode': 0, 'data': {'totalCount': 2, 'results': [{'channelId': 'a', 'channelName': '山东卫视'}]}}
        second = {'errcode': 0, 'data': {'results': [{'channelId': 'b', 'channelName': '生活台'}]}}
        with patch.object(sdgd, 'get_data', side_effect=[first, second]):
            self.assertEqual(len(sdgd.get_channels_sdgd()), 2)

    def test_gzyd_multiple_codes_choose_working_candidate(self):
        def fetch(url, body=None, **kwargs):
            if 'getClassifySchedule' in url:
                return {'code': 0, 'result': {'records': [{'channelCode': '500000-dead', 'title': 'CCTV1（高清）'}, {'channelCode': 'PD-working', 'title': 'CCTV1 HD'}]}}
            return {'code': 0, 'result': {'scheduleList': [1] if body['channelCode'] == 'PD-working' else []}}
        with patch.object(gzyd, 'request', side_effect=fetch):
            rows = gzyd.get_channels_gzyd()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['id'], ['PD-working'])

    def test_registry_picker_and_ingestion_with_new_source(self):
        from crawl.spiders import epg_source, epg_funcs, func_args
        from .catalog import SOURCE_NAMES, refresh_catalog
        from .forms import ChannelForm
        for source in MODULES:
            self.assertIn(source, SOURCE_NAMES)
            self.assertIn(source, epg_source)
            self.assertIn(source, epg_funcs)
            self.assertIn(source, func_args)
        refresh_catalog('fjyd', [{'name': '新增台', 'id': ['Umai:CHAN/test@BESTV'], 'sort': '福建'}])
        channel = Channel.objects.create(name='新增台', tvg_name='新增台', sort='福建', source='fjyd', channel_id='<fjyd:Umai:CHAN/test@BESTV>')
        result = {'success': 1, 'source': 'fjyd', 'epgs': fjyd.parse_programmes({'content': [{'programs': [{'startTime': self.start, 'endTime': self.start + 3600, 'programName': '节目'}]}]}, channel, self.date)}
        self.assertEqual(save_programmes(channel, result), 1)
        self.assertEqual(Epg.objects.get().channel_id, str(channel.pk))
        user = get_user_model().objects.create_superuser('new-source-ui', password='test-only')
        self.client.force_login(user)
        response = self.client.get('/admin/catalog/search/', {'q': '新增台', 'source': 'fjyd'})
        self.assertEqual(response.json()['results'][0]['identifier'], 'Umai:CHAN/test@BESTV')
        for source in MODULES:
            self.assertIn(source, dict(ChannelForm(instance=channel).fields['source'].choices))

    def test_failure_keeps_existing_programmes(self):
        channel = Channel.objects.create(name='保留台', tvg_name='保留台', sort='央视', source='sdgd', channel_id='<sdgd:provider-id>')
        Epg.objects.create(channel_id=str(channel.pk), title='原节目', program_date=self.date,
            starttime=dt.datetime.fromtimestamp(self.start, cqct.ZONE), endtime=dt.datetime.fromtimestamp(self.start + 3600, cqct.ZONE))
        with patch.object(sdgd, 'get_data', return_value={'errcode': 0, 'data': {'results': []}}):
            result = sdgd.get_epgs_sdgd(channel, 'provider-id', self.date)
        self.assertEqual(result['success'], 0)
        with self.assertRaises(ValueError):
            save_programmes(channel, result)
        self.assertEqual(Epg.objects.get().title, '原节目')
