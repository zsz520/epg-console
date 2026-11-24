import datetime
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

import requests
from bs4 import BeautifulSoup
from crawl.spiders import tvsou


class TvsouTests(TestCase):
    day = datetime.date(2026, 11, 24)
    channel = SimpleNamespace(id=7)

    def soup(self, rows='', date='2026年11月24日'):
        return BeautifulSoup(f'<a class="week_active">{date}</a><table class="layui-table">{rows}</table>', 'html.parser')

    def test_plain_and_linked_cells_seconds_and_duplicates(self):
        soup = self.soup('<tr><th>时间</th><th>节目</th></tr><tr><td>6：00</td><td><a>新闻</a><a>剧情</a></td></tr>'
                         '<tr><td><a>00:10:30</a></td><td>凌晨</td></tr><tr><td>6:00</td><td>新闻</td></tr>')
        epgs, _ = tvsou.parse_programmes(soup, self.channel, self.day)
        self.assertEqual([row['title'] for row in epgs], ['凌晨', '新闻'])
        self.assertEqual(epgs[0]['starttime'].second, 30)
        self.assertIsNone(epgs[-1]['endtime'])

    def test_bad_partial_rows_and_conflicts_reject_entire_day(self):
        valid = '<tr><td>06:00</td><td>新闻</td></tr>'
        for row in ('<tr><td>25:00</td><td>错误</td></tr>', '<tr><td>07:00</td><td></td></tr>',
                    '<tr><td>06:00</td><td>另一个节目</td></tr>', '<tr><td>07:00</td></tr>'):
            with self.subTest(row=row), self.assertRaisesRegex(ValueError, '节目解析失败'):
                tvsou.parse_programmes(self.soup(valid + row), self.channel, self.day)

    def test_empty_missing_table_and_mismatched_dates(self):
        with self.assertRaisesRegex(ValueError, '暂无有效节目'):
            tvsou.parse_programmes(self.soup('<tr><td colspan="3">暂无节目</td></tr>'), self.channel, self.day)
        with self.assertRaisesRegex(ValueError, '未找到节目表'):
            tvsou.parse_programmes(BeautifulSoup('<html>changed</html>', 'html.parser'), self.channel, self.day)
        with self.assertRaisesRegex(ValueError, '日期不符'):
            tvsou.parse_programmes(self.soup(date='2026年11月17日'), self.channel, self.day)

    def test_month_day_near_year_boundary(self):
        clock = Mock(wraps=datetime.datetime)
        clock.now.return_value = datetime.datetime(2027, 1, 1, tzinfo=datetime.timezone.utc)
        with patch.object(tvsou.datetime, 'datetime', clock):
            self.assertTrue(tvsou.validate_date(self.soup(date='周四 12月31日'), datetime.date(2026, 12, 31)))
            with self.assertRaisesRegex(ValueError, '日期不符'):
                tvsou.validate_date(self.soup(date='周四 12月31日'), datetime.date(2027, 12, 31))

    def test_request_errors_are_actionable_and_do_not_leak_urls(self):
        for error, message in [(requests.ConnectTimeout('secret-url'), '连接超时'), (requests.ReadTimeout(), '读取超时'),
                               (requests.exceptions.SSLError(), 'TLS'), (requests.exceptions.ProxyError(), '代理'),
                               (requests.ConnectionError(), 'DNS')]:
            with self.subTest(error=error), patch.object(tvsou.requests, 'get', side_effect=error):
                result = tvsou.get_epgs_tvsou(self.channel, 'b3666b9d', self.day)
            self.assertEqual(result['success'], 0)
            self.assertEqual(result['epgs'], [])
            self.assertIn(message, result['msg'])
            self.assertNotIn('secret-url', result['msg'])

    def test_http_status_challenge_and_redirect(self):
        for status, text, url, expected, ban in [(404, '', tvsou.HOST, '404', 0), (429, '', tvsou.HOST, '429', 1),
                (503, '', tvsou.HOST, '503', 0), (200, '<h1>安全验证</h1>', tvsou.HOST, '访问验证', 1),
                (200, '', tvsou.HOST, '空页面', 0), (200, 'login', 'https://example.com/', '重定向', 0)]:
            response = Mock(status_code=status, text=text, url=url)
            response.__enter__ = Mock(return_value=response)
            response.__exit__ = Mock(return_value=False)
            with self.subTest(status=status, expected=expected), patch.object(tvsou.requests, 'get', return_value=response) as get:
                result = tvsou.get_epgs_tvsou(self.channel, 'b3666b9d', self.day)
            self.assertIn(expected, result['msg'])
            self.assertEqual(result['ban'], ban)
            self.assertEqual(get.call_args.kwargs['timeout'], tvsou.TIMEOUT)

    def test_id_compatibility_and_invalid_input_avoids_network(self):
        with patch.object(tvsou, 'fetch_page', return_value=self.soup('<tr><td>06:00</td><td>新闻</td></tr>')) as fetch:
            result = tvsou.get_epgs_tvsou(self.channel, 'b3666b9d#weishi', self.day)
        self.assertEqual(result['success'], 1)
        fetch.assert_called_once_with('/epg/b3666b9d/w2')
        with patch.object(tvsou, 'fetch_page') as fetch:
            self.assertIn('ID 无效', tvsou.get_epgs_tvsou(self.channel, '../wrong', self.day)['msg'])
            fetch.assert_not_called()

    def test_catalog_group_pairing_and_duplicates(self):
        soup = BeautifulSoup('<div class="pd_list"><div class="pd_tit">央视</div><div class="pd_con"><ul>'
            '<li><a href="/epg/b3666b9d/">CCTV1</a></li><li><a href="/epg/b3666b9d/">CCTV1</a></li>'
            '<li><a href="https://example.com/epg/foreign/">外站</a></li></ul></div>'
            '<div class="pd_tit">北京</div><div class="pd_con"><ul><li><a href="/epg/beijing/">北京频道</a></li></ul></div></div>', 'html.parser')
        with patch.object(tvsou, 'fetch_page', return_value=soup):
            channels = tvsou.get_channels_tvsou()
        self.assertEqual(len(channels), 2)
        self.assertEqual(channels[0]['sort'], '央视')
        self.assertEqual(channels[1]['sort'], '北京')
        for html in ('<html>changed</html>', '<div class="pd_list"><div class="pd_tit">央视</div></div>'):
            with patch.object(tvsou, 'fetch_page', return_value=BeautifulSoup(html, 'html.parser')):
                with self.assertRaisesRegex(ValueError, '搜视频道目录获取失败'):
                    tvsou.get_channels_tvsou()
