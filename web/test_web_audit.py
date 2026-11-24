import datetime
import json
from urllib.parse import parse_qs, urlsplit
from bs4 import BeautifulSoup
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from .models import Channel, Channel_list, Epg
from .channel_order import token


class WebAuditTests(TestCase):
    def test_group_priority_and_continuous_numbers(self):
        entries=[self.channel(name,sort=group,sort_order=rank) for name,group,rank in
            [('地方','重庆',1),('港','香港',2),('付费','数字付费',3),('卫视A','卫视',10),('央视B','央视',20),('央视A','央视',5)]]
        expected=['央视A','央视B','卫视A','付费','港','地方']
        response=self.client.get('/admin/web/channel/')
        self.assertEqual(list(response.context['cl'].queryset.values_list('name',flat=True)),expected)
        self.assertEqual([x.text for x in BeautifulSoup(response.content,'html.parser').select('.channel-number')],['1','2','3','4','5','6'])
        self.assertEqual(list(self.client.get('/').context['channels'].values_list('name',flat=True)),expected)
        self.assertEqual(list(Channel.get_need_channels(Channel,'all')[0].values_list('name',flat=True)),expected)

    def test_cannot_drag_across_groups_or_after_group_change(self):
        a,b=self.channel('A'),self.channel('B',sort='卫视')
        signed=token(self.user,[a.pk,b.pk])
        response=self.client.post('/admin/channels/reorder/',json.dumps({'token':signed,'ids':[b.pk,a.pk]}),content_type='application/json')
        self.assertEqual(response.status_code,409)
        Channel.objects.filter(pk=a.pk).update(sort='数字付费')
        response=self.client.post('/admin/channels/reorder/',json.dumps({'token':signed,'ids':[a.pk,b.pk]}),content_type='application/json')
        self.assertEqual(response.status_code,409)

    def setUp(self):
        self.user=get_user_model().objects.create_superuser('audit-test',password='test-only')
        self.client.force_login(self.user)

    def channel(self, name, **extra):
        return Channel.objects.create(name=name,tvg_name=extra.pop('tvg_name',name),sort=extra.pop('sort','央视'),
            source='hks',channel_id='<hks:hks>',has_epg=1,ineed=1,**extra)

    def test_numbers_pagination_filter_empty_and_sort(self):
        rows=[self.channel(f'频道{i:02}') for i in range(53)]
        rows[1].delete()
        for url,start,count in [('/admin/web/channel/',1,50),('/admin/web/channel/?p=2',51,2),
                                ('/admin/web/channel/?all=',1,52),('/admin/web/channel/?q=频道52',1,1),
                                ('/admin/web/channel/?o=-2',1,50)]:
            response=self.client.get(url)
            soup=BeautifulSoup(response.content,'html.parser')
            numbers=[int(x.text) for x in soup.select('.channel-number')]
            self.assertEqual(numbers,list(range(start,start+count)),url)
        self.assertEqual(self.client.get('/admin/web/channel/?q=不存在').status_code,200)

    def test_renumber_preserves_programme_catalog_and_external_id(self):
        a,b=self.channel('A'),self.channel('B')
        day=timezone.localdate()
        Epg.objects.create(channel_id=str(a.pk),program_date=day,starttime=timezone.now(),endtime=timezone.now(),title='保留关联')
        row=Channel_list.objects.create(source='hks',out_channel_id='hks',out_name='A',inner_channel_id=a.pk)
        response=self.client.post('/admin/channels/reorder/',json.dumps({'token':token(self.user,[a.pk,b.pk]),'ids':[b.pk,a.pk]}),content_type='application/json')
        self.assertEqual(response.status_code,200)
        a.refresh_from_db(); row.refresh_from_db()
        self.assertEqual((a.tvg_id,Epg.objects.get().channel_id,row.inner_channel_id),('A',str(a.pk),a.pk))
        soup=BeautifulSoup(self.client.get('/admin/web/channel/').content,'html.parser')
        self.assertEqual([x.text for x in soup.select('.channel-number')],['1','2'])
        self.assertEqual(soup.select('.channel-number')[0].find_parent('a')['href'],f'/admin/web/channel/{b.pk}/change/')

    def test_inline_post_after_reorder_updates_correct_channel(self):
        a,b=self.channel('A'),self.channel('B')
        self.client.post('/admin/channels/reorder/',json.dumps({'token':token(self.user,[a.pk,b.pk]),'ids':[b.pk,a.pk]}),content_type='application/json')
        data={'form-TOTAL_FORMS':'2','form-INITIAL_FORMS':'2','form-MIN_NUM_FORMS':'0','form-MAX_NUM_FORMS':'1000','_save':'保存'}
        for index,channel in enumerate([a,b]):
            for key,value in {'id':channel.pk,'name':channel.name+'修改','tvg_id':channel.tvg_id,'tvg_name':channel.tvg_name,'sort':channel.sort,'source':channel.source,'has_epg':1,'ineed':1}.items():
                data[f'form-{index}-{key}']=value
        self.assertEqual(self.client.post('/admin/web/channel/',data).status_code,302)
        self.assertEqual(list(Channel.objects.order_by('sort_order').values_list('name',flat=True)),['B修改','A修改'])

    def test_public_same_name_channels_link_to_their_own_programmes(self):
        a=self.channel('第一个显示名',tvg_name='同名频道',tvg_id='ID-A')
        b=self.channel('第二个显示名',tvg_name='同名频道',tvg_id='ID-B')
        day=timezone.localdate()
        for channel in [a,b]:
            Epg.objects.create(channel_id=str(channel.pk),program_date=day,starttime=timezone.now(),endtime=timezone.now(),title=channel.tvg_id+'独有节目')
        soup=BeautifulSoup(self.client.get('/').content,'html.parser')
        links=soup.select('a.channel-name')
        for link,channel in zip(links,[a,b]):
            self.assertEqual(parse_qs(urlsplit(link['href']).query)['channel_id'],[str(channel.pk)])
            response=self.client.get(link['href'])
            self.assertContains(response,channel.tvg_id+'独有节目')
            next_url=response.context['tomorrow_url']
            self.assertEqual(parse_qs(urlsplit(next_url).query)['channel_id'],[str(channel.pk)])

    def test_web_bad_date_and_missing_date(self):
        channel=self.channel('日期测试')
        for date in ['20260231','garbage','20261122extra','2026-13-01']:
            self.assertEqual(self.client.get('/api/web/',{'ch':channel.tvg_name,'date':date}).status_code,400)
        self.assertEqual(self.client.get('/api/web/',{'ch':channel.tvg_name}).status_code,200)
        self.assertContains(self.client.get('/api/web/',{'ch':channel.tvg_name}),'港澳台 / 海外')
        self.assertEqual(self.client.get('/api/web/',{'channel_id':'invalid'}).status_code,400)
        for date in ['0001-01-01','9999-12-31']:
            self.assertEqual(self.client.get('/api/web/',{'ch':channel.tvg_name,'date':date}).status_code,200)

    def test_programmes_chronology_midnight_null_and_real_source(self):
        from .runtime_config import get_config
        config=get_config(); config.split_midnight=False; config.fill_programme_gaps=False; config.save()
        channel=self.channel('时间测试')
        day=timezone.localdate()
        def dt(hour,minute=0):
            return timezone.make_aware(datetime.datetime.combine(day,datetime.time(hour,minute)))
        for title,start,end in [('跨日节目',dt(23,30),dt(23,30)+datetime.timedelta(hours=1)),('早间节目',dt(8),dt(9)),('无结束节目',dt(12),None),('末档无结束',dt(23,55),None)]:
            Epg.objects.create(channel_id=str(channel.pk),program_date=day,starttime=start,endtime=end,title=title,descr=None,source='cctv')
        response=self.client.get('/api/web/',{'ch':channel.tvg_name})
        self.assertEqual([row['title'] for row in response.context['epgs']],['早间节目','无结束节目','跨日节目','末档无结束'])
        self.assertContains(response,'次日 00:30')
        self.assertContains(response,'未提供')
        self.assertNotContains(response,'None')
        self.assertEqual(response.context['source'],'央视')

    def test_public_order_and_category_counts_only_include_enabled(self):
        a=self.channel('A',sort='上海',sort_order=30)
        b=self.channel('B',sort='上海',sort_order=10)
        disabled=self.channel('隐藏',sort='上海')
        Channel.objects.filter(pk=disabled.pk).update(ineed=0)
        response=self.client.get('/',{'sort':'上海'})
        self.assertEqual(list(response.context['channels']),[b,a])
        self.assertEqual(response.context['visible_channel_count'],2)
        entry=next(x for x in response.context['local_categories'] if x['label']=='上海')
        self.assertTrue(entry['active']); self.assertEqual(entry['count'],2)
        self.assertEqual(self.client.get('/admin/web/channel/',{'sort__exact':'上海'}).context['local_categories'][0]['count'],3)


