from bs4 import BeautifulSoup
from django.contrib.auth import get_user_model
from django.test import TestCase
from .models import Channel
from .runtime_config import get_config
from .options import options
from .settings_form import SettingsForm


class CategoryPicker2115Tests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_superuser('categories',password='test-only'))
        Channel.objects.create(name='广东台',tvg_name='广东台',sort='广东',source='cctv',channel_id='<cctv:1>')
        cfg=get_config();cfg.options['local_categories']=['上海','北京','历史分组'];cfg.save()

    def data(self,**updates):
        cfg=get_config();data={key:getattr(cfg,key) for key in SettingsForm.Meta.fields}
        data.update(options(cfg));data['include_channels']='\n'.join(data['include_channels'])
        return {**data,'_settings_full':'1',**updates}

    def test_all_category_fields_use_checkboxes_and_preserve_previous_values(self):
        response=self.client.get('/admin/settings/')
        soup=BeautifulSoup(response.content,'html.parser')
        roots=soup.select('.category-picker')
        self.assertEqual(len(roots),4)
        for root in roots:
            self.assertTrue(root.select_one('.category-search'))
            self.assertTrue(root.select_one('.category-select-visible'))
            self.assertTrue(root.select_one('.category-clear'))
        checked={tag['value'] for tag in soup.select('input[name="local_categories"][checked]')}
        self.assertTrue(all(not tag.has_attr('aria-label') for tag in soup.select('.category-option input')))
        self.assertEqual(checked,{'上海','北京','历史分组'})
        self.assertIsNone(soup.select_one('select[name="local_categories"]'))

    def test_multi_selection_and_clear_all_save_independently(self):
        data=self.data(local_categories=['上海','广东'],cc_categories=['央视'],overseas_categories=[],exclude_categories=['北京'])
        self.assertEqual(self.client.post('/admin/settings/',data).status_code,302)
        cfg=get_config()
        self.assertEqual(cfg.options['local_categories'],['上海','广东'])
        self.assertEqual(cfg.options['cc_categories'],['央视'])
        self.assertEqual(cfg.options['overseas_categories'],[])
        self.assertEqual(cfg.options['exclude_categories'],['北京'])
        self.assertEqual(self.client.post('/admin/settings/',self.data(local_categories=[])).status_code,302)
        self.assertEqual(get_config().options['local_categories'],[])
