import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from django.test import TestCase,SimpleTestCase
from crawl.source_registry import discover,invoke,registry,get_source_choices
from .models import Channel
from .catalog import SOURCE_NAMES

CODE="""SOURCE_ID = 'autotest'
SOURCE_NAME = '自动来源'
def get_channels_autotest(): return [{'name':'频道','id':['abc'],'source':SOURCE_ID}]
def get_epgs_autotest(channel,channel_id,dt,func_arg=0): return {'epgs':[], 'msg':str(func_arg)}
"""
class AutoSourceTests(SimpleTestCase):
    def test_metadata_scan_does_not_execute_source(self):
        with TemporaryDirectory() as directory:
            path=Path(directory);(path/'new.py').write_text(CODE+"\nraise RuntimeError('must not execute during discovery')\n")
            sources,errors=discover(path,path/'absent')
            self.assertEqual(list(sources),['autotest']);self.assertEqual(errors,[])
    def test_external_source_lazy_invocation(self):
        with TemporaryDirectory() as directory:
            path=Path(directory);(path/'auto.py').write_text(CODE)
            sources,errors=discover(path/'absent',path)
            source=sources['autotest'];self.assertTrue(source.external)
            import sys
            sys.modules.pop('_epg_source_autotest',None)
            self.assertEqual(invoke(source,'channels')[0]['id'],['abc'])
            self.assertEqual(invoke(source,'epg',None,'abc',datetime.date.today())['msg'],'0')
            sys.modules.pop('_epg_source_autotest',None)
    def test_bad_file_duplicate_and_helper_do_not_break_good_source(self):
        with TemporaryDirectory() as directory:
            path=Path(directory);(path/'good.py').write_text(CODE)
            (path/'bad.py').write_text('SOURCE_ID = (')
            (path/'helper.py').write_text('raise Exception("helper must not execute")')
            (path/'_template.py').write_text('invalid(')
            sources,errors=discover(path,path/'absent')
            self.assertEqual(list(sources),['autotest']);self.assertEqual(len(errors),1)
            (path/'duplicate.py').write_text(CODE)
            sources,errors=discover(path,path/'absent')
            self.assertNotIn('autotest',sources);self.assertTrue(any('多个来源' in e for e in errors))
    def test_missing_function_invalid_id_and_disabled(self):
        with TemporaryDirectory() as directory:
            path=Path(directory)
            for filename,code in [('missing.py',"SOURCE_ID='missing'\nSOURCE_NAME='缺函数'"),('invalid.py',CODE.replace("'autotest'","'Bad-ID'")),('off.py',CODE+'\nSOURCE_ENABLED=False')]:
                (path/filename).write_text(code)
            sources,errors=discover(path,path/'absent')
            self.assertEqual(sources,{});self.assertEqual(len(errors),2)
    def test_three_argument_function_and_literal_argument(self):
        with TemporaryDirectory() as directory:
            path=Path(directory);(path/'auto.py').write_text(CODE.replace('dt,func_arg=0','dt').replace("str(func_arg)","'three'"))
            sources,_=discover(path/'absent',path)
            self.assertEqual(invoke(sources['autotest'],'epg',None,'a',datetime.date.today())['msg'],'three')
            import sys;sys.modules.pop('_epg_source_autotest',None)
    def test_builtin_override_requires_same_filename(self):
        with TemporaryDirectory() as directory:
            path=Path(directory);built=path/'builtin';external=path/'external';built.mkdir();external.mkdir()
            (built/'auto.py').write_text(CODE);(external/'other.py').write_text(CODE)
            sources,errors=discover(built,external);self.assertFalse(sources['autotest'].external);self.assertTrue(errors)
            (external/'other.py').rename(external/'auto.py')
            sources,errors=discover(built,external);self.assertTrue(sources['autotest'].external);self.assertFalse(errors)
    def test_builtin_old_and_new_sources_registered_without_errors(self):
        sources,errors=registry();self.assertEqual(errors,[])
        for source in ['migu','cqcu','fjyst','4gtv','tvmao','xmltv','gdy','chuanliu']:
            self.assertIn(source,sources)
        choices=dict(Channel._meta.get_field('source').choices)
        self.assertEqual(choices['migu'],'咪咕视频');self.assertIn('cqcu',SOURCE_NAMES)
    def test_dynamic_choices_do_not_create_new_migrations_for_new_file(self):
        field=Channel._meta.get_field('source');_,_,_,kwargs=field.deconstruct()
        self.assertIs(kwargs['choices'],get_source_choices)

    def test_externalized_providers_have_no_builtin_or_legacy_fallback(self):
        from crawl.source_registry import LEGACY
        keys={'cqct','fjyd','yangshipin','sdgd','gzyd','bntv','gdy','migu','cqcu','fjyst','fjaijia','hks','sdtv','tvb'}
        builtin=Path(__file__).resolve().parents[1]/'crawl'/'spiders'
        self.assertFalse(any((builtin/(key+'.py')).exists() for key in keys))
        self.assertFalse(keys.intersection(LEGACY))
        sources,errors=discover(external=builtin/'_absent_external_folder')
        self.assertFalse(keys.intersection(sources));self.assertEqual(errors,[])
        self.assertTrue(all(registry()[0][key].external for key in keys))

class AddedSourceParsingTests(TestCase):
    def setUp(self):
        self.day=datetime.date(2026,11,23)
        self.channel=Channel.objects.create(name='来源测试',tvg_name='来源测试',sort='其他',source='migu',channel_id='<migu:1>')
    def test_migu_registration_parser_and_wrong_date(self):
        from crawl.spiders import epg_func
        from crawl.source_registry import registry as _registry, load as _load
        migu = _load(_registry()[0]['migu'])
        start=datetime.datetime(2026,11,23,8,tzinfo=migu.ZONE)
        payload={'code':200,'body':{'program':[{'programName':'2026年11月23日','content':[{'pID':'1','contName':'新闻','startTime':int(start.timestamp()*1000),'endTime':int((start+datetime.timedelta(hours=1)).timestamp()*1000)}]}]}}
        with patch.object(migu,'fetch_json',return_value=payload):
            self.assertTrue(epg_func(self.channel,'1',self.day,source='migu')['success'])
            self.assertFalse(epg_func(self.channel,'1',self.day+datetime.timedelta(days=1),source='migu')['success'])
    def test_cqcu_registration_and_preserves_id_and_date(self):
        from crawl.spiders import epg_func
        from crawl.source_registry import registry as _registry, load as _load
        cqcu = _load(_registry()[0]['cqcu'])
        identifier='0'*31+'1'
        payload={'channel':{'channelcode':identifier},'schedules':[{'title':'节目','starttime':'2026-11-23 23:30:00','endtime':'2026-11-24 00:30:00'}]}
        with patch.object(cqcu,'fetch_json',return_value=payload):
            result=epg_func(self.channel,identifier,self.day,source='cqcu')
        self.assertTrue(result['success']);self.assertEqual(result['epgs'][0]['endtime'].day,24)
        self.assertEqual(cqcu.parse_channels([[{'contentID':identifier,'name':'测试','multiCastUrl':'igmp://example'}]])[0]['id'],[identifier])
    def test_fjyst_registration_and_wrong_channel(self):
        from crawl.spiders import epg_func
        from crawl.source_registry import registry as _registry, load as _load
        fjyst = _load(_registry()[0]['fjyst'])
        start=datetime.datetime(2026,11,23,8,tzinfo=fjyst.ZONE)
        payload={'content':[{'programs':[{'uuid':'one','programName':'节目','startTime':start.timestamp(),'endTime':start.timestamp()+3600}]}]}
        with patch.object(fjyst,'fetch_json',return_value=payload):
            self.assertTrue(epg_func(self.channel,'one',self.day,source='fjyst')['success'])
            self.assertFalse(epg_func(self.channel,'other',self.day,source='fjyst')['success'])


