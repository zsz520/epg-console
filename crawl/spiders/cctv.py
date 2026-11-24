# -*- coding:utf-8 -*-
# CCTV官方
import requests, re, datetime, json, os
from utils.general import headers
from bs4 import BeautifulSoup as bs


SHANGHAI = datetime.timezone(datetime.timedelta(hours=8))


def parse_programmes_cctv(payload, channel, channel_id, day):
    data = payload.get('data') if isinstance(payload, dict) else None
    info = data.get(channel_id) if isinstance(data, dict) else None
    programmes = info.get('list') if isinstance(info, dict) else None
    if not isinstance(programmes, list):
        raise ValueError('接口未返回该频道的节目列表，请检查来源 ID。')
    if not programmes:
        raise ValueError('该日期暂无节目，可能尚未发布。')
    epgs, missing_ends, invalid_ends = [], 0, 0
    for index, programme in enumerate(programmes, 1):
        if not isinstance(programme, dict):
            raise ValueError(f'第 {index} 条节目格式无效。')
        title = str(programme.get('title') or '').strip()
        if not title:
            raise ValueError(f'第 {index} 条节目标题为空。')
        try:
            start = datetime.datetime.fromtimestamp(float(programme['startTime']), SHANGHAI)
        except (KeyError, TypeError, ValueError, OverflowError, OSError):
            raise ValueError(f'第 {index} 条节目开始时间无效。') from None
        raw_end = programme.get('endTime')
        stop = None
        if raw_end not in (None, '', 0, '0'):
            try:
                stop = datetime.datetime.fromtimestamp(float(raw_end), SHANGHAI)
            except (TypeError, ValueError, OverflowError, OSError):
                invalid_ends += 1
            else:
                if stop <= start:
                    stop = None
                    invalid_ends += 1
        else:
            missing_ends += 1
        epgs.append({'channel_id': channel.id, 'starttime': start, 'endtime': stop,
                     'title': title, 'desc': '', 'program_date': start.date()})
    if not any(row['program_date'] == day for row in epgs):
        raise ValueError('接口返回的节目日期与请求日期不符，未采用这些数据。')
    message = f'获取 {len(epgs)} 条节目。'
    if invalid_ends:
        message += f'源站 {invalid_ends} 条节目结束时间无效（含结束不晚于开始），已按未提供结束时间处理，由公共逻辑推断。'
    if missing_ends:
        message += f'{missing_ends} 条节目未提供结束时间。'
    return epgs, message


def get_epgs_cctv(channel, channel_id, dt, func_arg=0):
    if isinstance(dt, datetime.datetime):
        dt = dt.date()
    result = {'success': 0, 'epgs': [], 'msg': '', 'last_program_date': dt, 'ban': 0}
    try:
        if not isinstance(dt, datetime.date):
            raise ValueError('请求日期无效。')
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', str(channel_id)):
            raise ValueError('频道来源 ID 无效，请填写 cctv4k 等频道 ID。')
        with requests.get('https://api.cntv.cn/epg/getEpgInfoByChannelNew',
                          params={'c': channel_id, 'serviceId': 'tvcctv', 'd': dt.strftime('%Y%m%d'), 't': 'jsonp', 'cb': 'set'},
                          headers=headers, timeout=(8, 20)) as response:
            if response.status_code in (403, 429):
                result['ban'] = 1
            if response.status_code >= 400:
                raise ValueError(f'HTTP {response.status_code}：源站请求失败。')
            response.encoding = 'utf-8'
            text = response.text.strip()
        match = re.fullmatch(r'set\s*\((.*)\)\s*;?', text, re.S)
        try:
            payload = json.loads(match[1] if match else text)
        except (ValueError, TypeError):
            raise ValueError('接口返回的内容不是有效 JSON/JSONP。') from None
        result['epgs'], result['msg'] = parse_programmes_cctv(payload, channel, channel_id, dt)
        result['success'] = 1
    except requests.exceptions.Timeout:
        result['msg'] = '央视：请求超时，请检查网络或稍后重试。'
    except requests.exceptions.RequestException as exc:
        result['msg'] = f'央视：网络请求失败（{type(exc).__name__}）。'
    except ValueError as exc:
        result['msg'] = f'央视：{exc}'
    except Exception as exc:
        result['msg'] = f'央视：节目解析异常（{type(exc).__name__}）。'
    return result


def get_channels_cctv():
    channels = []
    host = "https://tv.cctv.com"
    url = "%s/epg/index.shtml" % host
    res = requests.get(url, headers=headers)
    res.encoding = "utf-8"
    soup = bs(res.text, "html.parser")
    lis = soup.select("div.channel_con > div > ul > li")
    need_date = datetime.datetime.now().strftime("%Y%m%d")
    for li in lis:
        id = li.select("img")[0].attrs["title"].strip()
        logo = "http://" + li.select("img")[0].attrs["src"].strip()
        url_info = (
            "http://api.cntv.cn/epg/getEpgInfoByChannelNew?c=%s&serviceId=tvcctv&d=%s&t=jsonp&cb=set"
            % (id, need_date)
        )
        res = requests.get(url_info, headers=headers, timeout=5)
        res.encoding = "utf-8"
        research = re.search('"channelName":"(.+?)".+?"lvUrl":"(.+?)"', res.text)
        name = research.group(1)
        url = research.group(2)
        channel = {
            "name": name,
            "id": [id],
            "url": url,
            "source": "cctv",
            "logo": logo,
            "desc": "",
            "sort": "央视",
        }
        channels.append(channel)
    return channels
