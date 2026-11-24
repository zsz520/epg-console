from urllib.parse import urlencode, urlsplit, parse_qs
from django.contrib.auth import get_user_model
from django.test import TestCase
from .models import Channel


class ChannelNavigationTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_superuser('navigation-test', password='test-only'))

    def channel(self, name, category='央视'):
        return Channel.objects.create(name=name, tvg_name=name, sort=category, source='hks', channel_id='<hks:hks>')

    def url(self, channel, filters=''):
        return f'/admin/web/channel/{channel.pk}/change/' + ('?' + urlencode({'_changelist_filters': filters}) if filters else '')

    def form_data(self, response, **changes):
        data = {key: value for key, value in response.context['adminform'].form.initial.items() if value is not None}
        return {**data, **changes}

    def test_neighbours_follow_actual_rows_and_boundaries(self):
        first = self.channel('甲')
        removed = self.channel('将删除')
        last = self.channel('乙')
        removed.delete()
        navigation = self.client.get(self.url(first)).context['channel_navigation']
        self.assertIsNone(navigation['previous'])
        self.assertEqual(navigation['next']['url'], self.url(last))
        navigation = self.client.get(self.url(last)).context['channel_navigation']
        self.assertEqual(navigation['previous']['url'], self.url(first))
        self.assertIsNone(navigation['next'])

    def test_filter_search_and_order_are_preserved(self):
        a, b, c = [self.channel(letter + '测试') for letter in 'ABC']
        self.channel('D测试', '卫视')
        self.channel('E其他')
        filters = urlencode({'sort__exact': '央视', 'q': '测试', 'o': '-2'})
        navigation = self.client.get(self.url(b, filters)).context['channel_navigation']
        self.assertEqual((navigation['position'], navigation['total']), (2, 3))
        self.assertEqual(urlsplit(navigation['previous']['url']).path, self.url(c))
        self.assertEqual(urlsplit(navigation['next']['url']).path, self.url(a))
        self.assertEqual(parse_qs(urlsplit(navigation['next']['url']).query)['_changelist_filters'], [filters])

    def test_save_and_switch_uses_neighbour_before_group_change(self):
        first, second = self.channel('A'), self.channel('B')
        filters = urlencode({'sort__exact': '央视'})
        url = self.url(first, filters)
        response = self.client.get(url)
        data = self.form_data(response, sort='新分组', _channel_direction='next')
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(urlsplit(response.url).path, self.url(second))
        first.refresh_from_db()
        self.assertEqual(first.sort, '新分组')

    def test_invalid_save_stays_on_form_and_cannot_redirect_externally(self):
        first = self.channel('A')
        self.channel('B')
        url = self.url(first)
        response = self.client.get(url)
        data = self.form_data(response, name='', _channel_direction='next')
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-has-errors="true"')
        first.refresh_from_db()
        self.assertEqual(first.name, 'A')
        response = self.client.post(url, self.form_data(self.client.get(url), _channel_direction='https://example.test'))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith('/admin/'))
