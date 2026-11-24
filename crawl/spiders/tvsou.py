# -*- coding: utf-8 -*-
from crawl.source_utils import programme_result, catalogue_result, SHANGHAI
"""搜视目录和节目表；重试、跨日与缺失补全由公共流程处理。"""
import datetime
import re
from urllib.parse import urljoin, urlsplit

import requests
from bs4 import BeautifulSoup
from utils.general import headers

HOST = 'https://www.tvsou.com'
TIMEOUT = (8, 20)  # 连接、读取超时，秒


class TvsouError(ValueError):
    def __init__(self, message, ban=0):
        super().__init__(message)
        self.ban = ban


def fetch_page(path):
    try:
        with requests.get(HOST + path, headers=headers, timeout=TIMEOUT) as response:
            status = response.status_code
            if status == 404:
                raise TvsouError('HTTP 404：频道或节目页不存在，请检查来源 ID。')
            if status in (403, 429):
                raise TvsouError(f'HTTP {status}：网站拒绝访问或限制请求频率，请稍后重试。', ban=1)
            if status >= 400:
                raise TvsouError(f'HTTP {status}：源站响应异常。')
            if urlsplit(response.url).hostname not in ('www.tvsou.com', 'tvsou.com'):
                raise TvsouError('请求被重定向至其他网站，未读取节目数据。')
            response.encoding = 'utf-8'
            text = response.text
    except requests.exceptions.ConnectTimeout:
        raise TvsouError('连接超时：无法连接搜视网站，请检查网络。') from None
    except requests.exceptions.ReadTimeout:
        raise TvsouError('读取超时：源站未在限定时间内返回完整页面。') from None
    except requests.exceptions.Timeout:
        raise TvsouError('请求超时：请稍后重试。') from None
    except requests.exceptions.SSLError:
        raise TvsouError('TLS 证书校验失败：请检查设备时间和证书环境。') from None
    except requests.exceptions.ProxyError:
        raise TvsouError('代理连接失败：请检查容器代理设置。') from None
    except requests.exceptions.ConnectionError:
        raise TvsouError('网络连接失败：请检查 DNS、路由和源站连通性。') from None
    except requests.exceptions.RequestException as exc:
        raise TvsouError(f'网络请求异常（{type(exc).__name__}）。') from None
    if not text.strip():
        raise TvsouError('源站返回空页面。')
    soup = BeautifulSoup(text, 'html.parser')
    if not soup.select_one('table.layui-table, div.pd_list'):
        visible = soup.get_text(' ', strip=True).lower()
        if any(word in visible for word in ('访问过于频繁', '安全验证', '人机验证', '验证码', 'captcha', 'access denied')):
            raise TvsouError('网站返回访问验证页面，暂时无法抓取，请稍后重试。', ban=1)
    return soup


def normalize_id(value):
    value = str(value or '').strip().split('#', 1)[0]
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', value):
        raise TvsouError('来源 ID 无效：请填写频道 ID（例如 b3666b9d），不要填写网址或 <来源:ID>。')
    return value


def validate_date(soup, day):
    # w1..w7 不是任意日期接口，校验高亮日期避免误用另一周的节目。
    active = soup.select_one('a.week_active')
    if active is None:
        return False
    text = active.get_text(' ', strip=True)
    full = re.search(r'(20\d{2})[年./-](\d{1,2})[月./-](\d{1,2})', text)
    if full:
        try:
            actual = datetime.date(*map(int, full.groups()))
        except ValueError:
            raise TvsouError('页面日期格式异常，未读取节目数据。') from None
    else:
        match = re.search(r'(\d{1,2})\s*[月./-]\s*(\d{1,2})', text)
        if not match:
            raise TvsouError('页面已选日期无法识别，可能发生改版。')
        today = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).date()
        candidates = []
        for year in (today.year - 1, today.year, today.year + 1):
            try:
                candidates.append(datetime.date(year, *map(int, match.groups())))
            except ValueError:
                pass
        if not candidates:
            raise TvsouError('页面日期格式异常，未读取节目数据。')
        actual = min(candidates, key=lambda item: abs((item - today).days))
    if actual != day:
        raise TvsouError(f'日期不符：请求 {day:%Y-%m-%d}，页面为 {actual:%Y-%m-%d}；网站只提供对应周的节目。')
    return True


def parse_programmes(soup, channel, day):
    table = soup.select_one('table.layui-table')
    if table is None:
        raise TvsouError('未找到节目表：频道 ID 可能失效，或网站页面结构已变化。')
    date_checked = validate_date(soup, day)
    epgs, seen, errors = [], {}, []
    for number, row in enumerate(table.select('tr'), 1):
        cells = row.find_all('td', recursive=False)
        if not cells:
            continue
        if len(cells) == 1 and any(word in cells[0].get_text() for word in ('暂无', '没有节目', '未提供')):
            continue
        if len(cells) < 2:
            errors.append(f'第 {number} 行缺少节目名称或时间')
            continue
        clock = cells[0].get_text(' ', strip=True).replace('：', ':')
        match = re.fullmatch(r'([01]?\d|2[0-3]):([0-5]\d)(?::([0-5]\d))?', clock)
        title = (cells[1].find('a') or cells[1]).get_text(' ', strip=True)
        if not match or not title:
            errors.append(f'第 {number} 行' + ('时间格式无效' if not match else '节目名称为空'))
            continue
        hour, minute, second = (int(item or 0) for item in match.groups())
        start = datetime.datetime.combine(day, datetime.time(hour, minute, second))
        if start in seen:
            if seen[start] != title:
                errors.append(f'第 {number} 行同一时间存在不同节目')
            continue
        seen[start] = title
        epgs.append({'channel_id': channel.id, 'starttime': start, 'endtime': None,
                     'title': title, 'desc': '', 'program_date': day})
    if errors:
        raise TvsouError(f'节目解析失败（{len(errors)} 处）：' + '；'.join(errors[:3]) + '。为避免覆盖完整节目，本次不返回残缺数据。')
    if not epgs:
        raise TvsouError('该日期暂无有效节目，可能尚未发布；未将空结果标记为成功。')
    epgs.sort(key=lambda item: item['starttime'])
    message = f'获取 {len(epgs)} 条节目。'
    if not date_checked:
        message += '页面未提供已选日期标记，无法核验页面日期。'
    return epgs, message


def get_desc_tvsou(url):
    # 沿用原行为：不额外逐节目请求详情，避免大量请求和频率限制。
    return ''


@programme_result('搜视')
def get_epgs_tvsou(channel, channel_id_, dt, func_arg=0):
    if isinstance(dt, datetime.datetime):
        dt = dt.date()
    result = {'success': 0, 'epgs': [], 'msg': '', 'last_program_date': dt, 'ban': 0}
    try:
        channel_id = normalize_id(channel_id_)
        if not isinstance(dt, datetime.date):
            raise TvsouError('请求日期无效，请使用有效的日期对象。')
        result['epgs'], result['msg'] = parse_programmes(fetch_page(f'/epg/{channel_id}/w{dt.isoweekday()}'), channel, dt)
        result['success'] = 1
    except TvsouError as exc:
        result.update(msg=f'搜视：{exc}', ban=exc.ban)
    except Exception as exc:
        result['msg'] = f'搜视：处理节目时发生异常（{type(exc).__name__}），请检查来源脚本。'
    return result


@catalogue_result('搜视')
def get_channels_tvsou():
    try:
        soup = fetch_page('/epg/difang/')
        groups = soup.select('div.pd_list > .pd_tit')
        if not groups:
            raise TvsouError('未找到频道分类目录，网站可能已改版。')
        channels, seen, group_count = [], set(), 0
        for title in groups:
            content = title.find_next_sibling()
            lists = content.select('ul') if content and 'pd_con' in content.get('class', []) else []
            if not lists:
                raise TvsouError('频道目录分类结构不完整，保留原目录。')
            category = title.get_text(' ', strip=True)
            group_count += 1
            for listing in lists:
                for link in listing.select('li a[href]'):
                    parts = urlsplit(urljoin(HOST, link['href'].strip()))
                    match = re.fullmatch(r'/epg/([A-Za-z0-9_-]+)/?', parts.path)
                    if parts.hostname not in ('www.tvsou.com', 'tvsou.com') or not match:
                        continue
                    name = link.get_text(' ', strip=True)
                    if not name:
                        raise TvsouError('频道目录存在空名称，保留原目录。')
                    channel_id = match[1]
                    if (category, channel_id) in seen:
                        continue
                    seen.add((category, channel_id))
                    channels.append({'name': name, 'id': [channel_id], 'url': HOST + f'/epg/{channel_id}/',
                                     'source': 'tvsou', 'logo': '', 'desc': '', 'sort': category})
        if not channels:
            raise TvsouError('目录中没有有效频道，请检查页面结构，保留原目录。')
        print(f'搜视：共有 {group_count} 个分类，{len(channels)} 个频道。')
        return channels
    except TvsouError as exc:
        raise ValueError(f'搜视频道目录获取失败：{exc}') from None
