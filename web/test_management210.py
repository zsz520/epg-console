import datetime
import io
import json
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase,override_settings
from django.utils import timezone
from .models import Channel,Epg,Task
from .runtime_config import get_config
from .ingest import save_programmes
from . import channel_transfer as transfer
from . import programme_management as purge


class Management210Tests(TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        setting=override_settings(DATA_DIR=Path(self.tmp.name));setting.enable();self.addCleanup(setting.disable)
        self.user=get_user_model().objects.create_superuser('data-admin',password='test-only')
        self.client.force_login(self.user)
        self.channel=Channel.objects.create(name='迁移台',tvg_name='迁移台',sort='央视',source='cctv',channel_id='<cctv:1>',has_epg=1,ineed=1)
        self.day=timezone.localdate()
        cfg=get_config();cfg.auto_crawl=False;cfg.fill_programme_gaps=False;cfg.save()

    def row(self,day=None,**extra):
        day=day or self.day;start=timezone.make_aware(datetime.datetime.combine(day,datetime.time(8)))
        return dict(channel_id=self.channel.pk,program_date=day,starttime=start,endtime=start+datetime.timedelta(hours=1),title='臺灣新聞與藝術',desc='節目簡介',**extra)

    def backup(self):
        return json.loads(transfer.export_response('json').content)['channels']

    def options(self,**values):
        cfg=get_config();cfg.options.update(values);cfg.save()

    def test_conversion_is_reversible_and_raw_ingest_is_original(self):
        save_programmes(self.channel,{'source':'tvb','epgs':[self.row()]})
        self.assertEqual(Epg.objects.get().title,'臺灣新聞與藝術')
        self.assertEqual(Epg.objects.get().descr,'節目簡介')
        displayed=Epg.get_single_epg(Epg,self.channel,self.day)[0]
        self.assertEqual((displayed['title'],displayed['descr']),('台湾新闻与艺术','节目简介'))
        self.assertContains(self.client.get('/admin/web/epg/'),'台湾新闻与艺术')
        self.options(traditional_to_simplified=False)
        displayed=Epg.get_single_epg(Epg,self.channel,self.day)[0]
        self.assertEqual((displayed['title'],displayed['descr']),('臺灣新聞與藝術','節目簡介'))
        self.assertContains(self.client.get('/admin/web/epg/'),'臺灣新聞與藝術')
        self.assertEqual(Epg.objects.get().title,'臺灣新聞與藝術')

    def test_preview_export_and_diyp_follow_conversion_switch(self):
        from .preview_runner import normalize
        from crawl import crawl
        save_programmes(self.channel,{'source':'tvb','epgs':[self.row()]})
        with tempfile.TemporaryDirectory() as directory,patch.dict(crawl.dirs,{'share':directory}):
            for enabled,title in [(True,'台湾新闻与艺术'),(False,'臺灣新聞與藝術')]:
                self.options(traditional_to_simplified=enabled)
                self.assertEqual(normalize({'epgs':[self.row()]},self.day)[0]['title'],title)
                _,filename=crawl.gen_xml('all')
                node=ET.parse(filename).getroot().find("programme[@channel='迁移台']/title")
                self.assertEqual(node.text,title)
                response=self.client.get('/api/diyp/',{'ch':self.channel.tvg_id,'date':self.day.strftime('%Y%m%d')})
                self.assertEqual(response.json()['epg_data'][0]['title'],title)

    def test_settings_can_turn_conversion_off(self):
        from .settings_form import SettingsForm,SPECS
        form=SettingsForm(instance=get_config())
        data={key:getattr(form.instance,key) for key in form.Meta.fields}
        data.update(_settings_full='1')
        for key,*rest in SPECS:
            value=form.opts[key]
            if rest[1]=='bool':
                if value and key!='traditional_to_simplified':data[key]='on'
        saved=SettingsForm(data,instance=get_config())
        self.assertTrue(saved.is_valid(),saved.errors);saved.save()
        self.assertFalse(get_config().options['traditional_to_simplified'])

    def test_import_and_export_queue_failure_roll_back_together(self):
        from django.db import OperationalError
        rows=self.backup();rows[0]['name']='不该保存'
        _,token=transfer.stage(rows,'merge',self.user)
        with patch('web.data_views.enqueue',side_effect=OperationalError('模拟队列失败')):
            response=self.client.post('/admin/channels/transfer/',{'action':'apply','token':token,'confirmed':'on'})
        self.assertEqual(response.status_code,200)
        self.channel.refresh_from_db();self.assertEqual(self.channel.name,'迁移台')

    def test_json_and_csv_export_roundtrip_preserves_mapping_group_order(self):
        for kind in ['json','csv']:
            response=transfer.export_response(kind)
            rows=transfer.parse_file(SimpleUploadedFile('channels.'+kind,response.content))
            checked=transfer.validate_rows(rows,'merge')
            self.assertEqual(checked[0]['pk'],self.channel.pk)
            self.assertEqual(checked[0]['fields']['channel_id'],'<cctv:1>')
            self.assertEqual(checked[0]['fields']['sort_order'],self.channel.sort_order)
        self.assertNotIn('last_program_date',self.backup()[0])

    def test_csv_formula_escape_is_safe_and_lossless(self):
        self.channel.name='=HYPERLINK("x")';self.channel.remark="'=literal";self.channel.save()
        response=transfer.export_response('csv')
        self.assertIn("'=HYPERLINK",response.content.decode('utf-8-sig'))
        rows=transfer.parse_file(SimpleUploadedFile('channels.csv',response.content))
        self.assertEqual(rows[0]['name'],self.channel.name)
        self.assertEqual(rows[0]['remark'],self.channel.remark)

    def test_merge_updates_existing_and_adds_new_without_deleting_other_channels(self):
        rows=self.backup();rows[0]['name']='改名'
        rows.append({**rows[0],'id':9999,'tvg_id':'新频道','tvg_name':'新频道','name':'新频道'})
        Epg.objects.create(**{k:v for k,v in self.row().items() if k!='desc'})
        _,token=transfer.stage(rows,'merge',self.user)
        self.assertEqual(transfer.apply(token,self.user),(1,1))
        self.channel.refresh_from_db();self.assertEqual(self.channel.name,'改名')
        self.assertEqual(Epg.objects.get().channel_id,str(self.channel.pk))
        self.assertNotEqual(Channel.objects.get(tvg_id='新频道').pk,9999)

    def test_local_updates_tvg_id_with_identity_guard(self):
        rows=self.backup();rows[0]['tvg_id']='changed-id'
        _,token=transfer.stage(rows,'local',self.user)
        self.assertEqual(transfer.apply(token,self.user),(0,1))
        self.channel.refresh_from_db();self.assertEqual(self.channel.tvg_id,'changed-id')
        with self.assertRaises(ValueError):transfer.validate_rows(rows,'local')

    def test_bad_import_rejects_whole_file_and_unknown_source(self):
        original=self.channel.name;rows=self.backup();rows[0]['name']='不该写入'
        rows.append({**rows[0],'tvg_id':'bad','source':'missing-provider','channel_id':'<missing-provider:1>'})
        with self.assertRaises(ValueError):transfer.stage(rows,'merge',self.user)
        self.channel.refresh_from_db();self.assertEqual(self.channel.name,original)
        with self.assertRaises(ValueError):transfer.validate_rows([rows[0],rows[0]],'merge')

    def test_import_revision_change_replay_and_user_binding(self):
        rows=self.backup();_,token=transfer.stage(rows,'merge',self.user)
        self.channel.name='并发修改';self.channel.save()
        with self.assertRaises(ValueError):transfer.apply(token,self.user)
        _,token=transfer.stage(self.backup(),'merge',self.user)
        other=get_user_model().objects.create_user('other')
        with self.assertRaises(ValueError):transfer.apply(token,other)
        transfer.apply(token,self.user)
        with self.assertRaises(ValueError):transfer.apply(token,self.user)

    def test_oversized_malformed_and_wrong_csv_columns_are_rejected(self):
        for upload in [SimpleUploadedFile('a.csv',b'tvg_id,tvg_id\na,b'),SimpleUploadedFile('a.json',b'{bad'),
                       SimpleUploadedFile('a.json',b'{"channels":[]}'),SimpleUploadedFile('a.csv',b'tvg_id,evil\na,b')]:
            with self.assertRaises(ValueError):transfer.parse_file(upload)

    def test_import_view_previews_then_requires_confirm(self):
        content=transfer.export_response('json').content
        response=self.client.post('/admin/channels/transfer/',{'mode':'merge','file':SimpleUploadedFile('channels.json',content)})
        self.assertEqual(response.status_code,200);self.assertContains(response,'导入预览')
        token=response.context['token']
        response=self.client.post('/admin/channels/transfer/',{'action':'apply','token':token})
        self.assertContains(response,'请先确认')
        self.assertEqual(self.client.post('/admin/channels/transfer/',{'action':'apply','token':token,'confirmed':'on'}).status_code,302)

    def test_date_purge_keeps_other_dates_and_recalculates_latest(self):
        for day in [self.day,self.day+datetime.timedelta(days=1)]:
            save_programmes(self.channel,{'source':'cctv','epgs':[self.row(day)]})
        params,_=purge.preview({'scope':'dates','start':self.day+datetime.timedelta(days=1),'end':self.day+datetime.timedelta(days=1)},self.user)
        self.assertEqual(purge.execute(params),1)
        self.assertEqual(Epg.objects.count(),1)
        self.channel.refresh_from_db();self.assertEqual(self.channel.last_program_date,self.day)

    def test_all_purge_includes_orphans_but_preserves_channel_config(self):
        save_programmes(self.channel,{'source':'cctv','epgs':[self.row()]})
        Epg.objects.create(**{**{k:v for k,v in self.row().items() if k!='desc'},'channel_id':'orphan'})
        params,_=purge.preview({'scope':'all'},self.user)
        self.assertEqual(purge.execute(params),2)
        self.assertEqual(Epg.objects.count(),0);self.assertEqual(Channel.objects.count(),1)
        self.channel.refresh_from_db();self.assertIsNone(self.channel.last_program_date)

    def test_purge_does_not_delete_rows_added_after_preview(self):
        save_programmes(self.channel,{'source':'cctv','epgs':[self.row()]})
        params,_=purge.preview({'scope':'all'},self.user)
        new=Epg.objects.create(**{**{k:v for k,v in self.row().items() if k!='desc'},'title':'后加入'})
        self.assertEqual(purge.execute(params),1)
        self.assertEqual(Epg.objects.get().pk,new.pk)

    def test_purge_stale_count_is_rejected_without_deleting_remaining(self):
        for day in [self.day,self.day+datetime.timedelta(days=1)]:save_programmes(self.channel,{'source':'cctv','epgs':[self.row(day)]})
        params,_=purge.preview({'scope':'all'},self.user)
        Epg.objects.first().delete()
        with self.assertRaises(ValueError):purge.execute(params)
        self.assertEqual(Epg.objects.count(),1)

    def test_purge_view_is_preview_only_until_confirmation_and_worker(self):
        save_programmes(self.channel,{'source':'cctv','epgs':[self.row()]})
        response=self.client.post('/admin/programmes/purge/',{'scope':'all'})
        self.assertEqual(Epg.objects.count(),1);self.assertFalse(Task.objects.exists())
        token=response.context['token']
        response=self.client.post('/admin/programmes/purge/',{'action':'apply','token':token,'confirmed':'on'})
        self.assertEqual(response.status_code,302);self.assertEqual(Epg.objects.count(),1)
        task=Task.objects.get(kind='purge')
        from .task_runner import main
        main(task.pk)
        self.assertEqual(Epg.objects.count(),0)
        self.assertTrue(Task.objects.filter(kind='xml').exists())

    def test_purge_forms_and_bound_tokens(self):
        self.assertFalse(purge.PurgeForm({'scope':'dates'}).is_valid())
        self.assertFalse(purge.PurgeForm({'scope':'dates','start':'2026-11-24','end':'2026-11-23'}).is_valid())
        _,token=purge.preview({'scope':'all'},self.user)
        other=get_user_model().objects.create_user('purge-other')
        with self.assertRaises(ValueError):purge.confirm(token,other)

    def test_purge_and_export_queue_roll_back_together(self):
        save_programmes(self.channel,{'source':'cctv','epgs':[self.row()]})
        params,_=purge.preview({'scope':'all'},self.user)
        task=Task.objects.create(kind='purge',params=params)
        from .task_runner import main
        with patch('web.tasks.enqueue',side_effect=RuntimeError('模拟队列异常')):
            with self.assertRaises(RuntimeError):main(task.pk)
        self.assertEqual(Epg.objects.count(),1)

    def test_buttons_and_permissions(self):
        self.assertContains(self.client.get('/admin/web/channel/'),'导入频道')
        self.assertContains(self.client.get('/admin/web/epg/'),'删除节目单（日期 / 全部）')
        user=get_user_model().objects.create_user('no-permission',is_staff=True)
        self.client.force_login(user)
        for url in ['/admin/channels/transfer/','/admin/channels/export/json/','/admin/programmes/purge/']:
            self.assertEqual(self.client.get(url).status_code,403)
