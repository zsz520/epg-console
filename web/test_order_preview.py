import json
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase, Client
from .models import Channel
from .channel_order import token


class OrderPreviewTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_superuser('order-test',password='test-only')
        self.client.force_login(self.user)
        self.rows=[Channel.objects.create(name=name,tvg_name=name,sort='央视',source='hks',channel_id='<hks:hks>',has_epg=1,ineed=1) for name in 'ABC']

    def post_order(self, before, after, signed=None):
        return self.client.post('/admin/channels/reorder/',json.dumps({'token':signed or token(self.user,before),'ids':after}),content_type='application/json')

    def test_reorder_visible_slots_and_homepage(self):
        a,b,c=[row.pk for row in self.rows]
        response=self.post_order([a,c],[c,a])
        self.assertEqual(response.status_code,200)
        self.assertEqual(list(Channel.objects.order_by('sort_order','pk').values_list('pk',flat=True)),[c,b,a])
        self.assertEqual(list(self.client.get('/').context['channels'].values_list('pk',flat=True)),[c,b,a])
        nav=self.client.get(f'/admin/web/channel/{c}/change/').context['channel_navigation']
        self.assertEqual(nav['next']['name'],'B')

    def test_rejects_stale_tampered_or_extra_ids(self):
        ids=[row.pk for row in self.rows]
        signed=token(self.user,ids)
        self.assertEqual(self.post_order(ids,list(reversed(ids)),signed).status_code,200)
        self.assertEqual(self.post_order(ids,ids,signed).status_code,409)
        self.assertEqual(self.post_order(ids,[ids[0]]*3).status_code,409)
        self.assertEqual(self.post_order(ids,ids,'tampered').status_code,409)
        self.assertEqual(self.client.get('/admin/channels/reorder/').status_code,405)

    def test_toolbar_and_navigation(self):
        response=self.client.get('/admin/web/channel/')
        self.assertContains(response,'保存顺序')
        self.assertNotContains(response,'column-sort_order')
        self.assertNotIn('sort_order',response.context['cl'].formset.forms[0].fields)
        self.assertContains(response,'data-enabled="true"')
        self.assertContains(self.client.get('/admin/web/channel/?o=2'),'data-enabled="false"')
        apps=response.context['available_apps']
        web=next(app for app in apps if app['app_label']=='web')
        self.assertEqual(web['models'][-1]['object_name'],'XmltvFeed')
        self.assertNotContains(response,'class="console-nav"')
        self.assertContains(response,'查看站点')

    @patch('web.preview.preview')
    def test_custom_id_preview_without_catalog_or_saving(self, preview):
        preview.return_value={'status':'success','count':1,'programmes':[]}
        before=list(Channel.objects.values())
        response=self.client.post('/admin/sources/preview/',json.dumps({'source':'hks','custom_id':'12','name':'隐藏台'}),content_type='application/json')
        self.assertEqual(response.status_code,200)
        preview.assert_called_once_with('hks','12','隐藏台',force=False)
        self.assertEqual(before,list(Channel.objects.values()))
        for source,custom_id in [('unknown','12'),('hks','<hks:12>'),('hks','')]:
            self.assertEqual(self.client.post('/admin/sources/preview/',json.dumps({'source':source,'custom_id':custom_id}),content_type='application/json').status_code,400)

    def test_readonly_cannot_reorder_or_preview_custom_ids(self):
        user=get_user_model().objects.create_user('readonly-order',is_staff=True)
        user.user_permissions.set(Permission.objects.filter(codename__in=['view_channel','view_channel_list']))
        self.client.force_login(user)
        self.assertEqual(self.post_order([self.rows[0].pk],[self.rows[0].pk]).status_code,403)
        self.assertEqual(self.client.post('/admin/sources/preview/',json.dumps({'source':'hks','custom_id':'12'}),content_type='application/json').status_code,403)
