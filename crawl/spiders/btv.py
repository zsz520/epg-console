# -*- coding:utf-8 -*-
from crawl.source_utils import source_http as requests, programme_result, catalogue_result, SHANGHAI
# 北京电视台官方来源 10 个频道
# 2022-11-03官方更改接口
# https://dynamic.rbc.cn/bvradio_app/service/LIVE?functionName=getCurrentChannel&channelId=135&curdate=2022-11-01
# http://jiemudan.brtv.org.cn/index.html?channel=TvCh1602660467213184  地址
# http://www.brtv.org.cn/mobileinf/rest/cctv/videolivelist/dayWeb?json={'id':'TvCh1602660467213184','day':'2021-12-15'}  接口
from bs4 import BeautifulSoup as bs
import datetime, os
from utils.general import headers


SHANGHAI = datetime.timezone(datetime.timedelta(hours=8))


def parse_programmes_btv(payload, channel, channel_id, day):
    info = payload.get('channel') if isinstance(payload, dict) else None
    if not isinstance(info, dict) or not isinstance(info.get('programes'), list):
        raise ValueError('接口未返回有效节目列表，可能发生改版。')
    if str(info.get('id', '')) != str(channel_id):
        raise ValueError('接口返回的频道 ID 与请求不符。')
    if not info['programes']:
        raise ValueError(f'频道 {channel_id} 在 {day:%Y-%m-%d} 暂无节目，源站可能尚未发布。')
    epgs, zero_rows, invalid_ends = [], 0, 0
    for index, programme in enumerate(info['programes'], 1):
        if not isinstance(programme, dict):
            raise ValueError(f'第 {index} 条节目格式无效。')
        title = str(programme.get('name') or '').strip()
        if not title:
            raise ValueError(f'第 {index} 条节目名称为空。')
        def clock(value):
            value = str(value or '').strip().replace('：', ':')
            for fmt in ('%H:%M', '%H:%M:%S'):
                try:
                    return datetime.datetime.strptime(value, fmt).time()
                except ValueError:
                    pass
            raise ValueError('无效时间')
        try:
            start = datetime.datetime.combine(day, clock(programme.get('startTime')), SHANGHAI)
        except ValueError:
            raise ValueError(f'第 {index} 条节目开始时间无效。') from None
        stop = None
        try:
            stop = datetime.datetime.combine(day, clock(programme.get('endTime')), SHANGHAI)
        except ValueError:
            invalid_ends += 1
        else:
            if stop == start:
                # 接次日/结束等零时长标记不是一档有效节目，不能占据后续空档。
                zero_rows += 1
                continue
            if stop < start:
                tomorrow = stop + datetime.timedelta(days=1)
                if start.hour >= 18 and tomorrow - start <= datetime.timedelta(hours=12):
                    # 接口只给时分，补回正常晚间节目结束时间的真实日期；不在来源内分段。
                    stop = tomorrow
                else:
                    stop = None
                    invalid_ends += 1
        epgs.append({'channel_id': channel.id, 'starttime': start, 'endtime': stop,
                     'title': title, 'desc': '', 'program_date': day})
    if not epgs:
        raise ValueError(f'没有有效节目（跳过 {zero_rows} 条零时长记录）。')
    message = f'获取 {len(epgs)} 条节目。'
    if zero_rows:
        message += f'已跳过 {zero_rows} 条零时长记录（含接次日/结束等标记）。'
    if invalid_ends:
        message += f'{invalid_ends} 条节目结束时间无效或缺失，交由公共逻辑推断。'
    return epgs, message


@programme_result('北京电视台')
def get_epgs_btv(channel, channel_id, dt, func_arg=0):
    if isinstance(dt, datetime.datetime):
        dt = dt.date()
    result = {'success': 0, 'epgs': [], 'msg': '', 'last_program_date': dt, 'ban': 0}
    try:
        if not isinstance(dt, datetime.date):
            raise ValueError('请求日期无效。')
        if not str(channel_id).isdigit():
            raise ValueError('频道 ID 无效，请填写官网数字频道 ID。')
        with requests.get('https://dynamic.rbc.cn/bvradio_app/service/LIVE',
                          params={'functionName': 'getCurrentChannel', 'channelId': channel_id, 'curdate': dt.strftime('%Y-%m-%d')},
                          headers=headers, timeout=(8, 20)) as response:
            if response.status_code in (403, 429):
                result['ban'] = 1
            if response.status_code >= 400:
                raise ValueError(f'HTTP {response.status_code}：源站请求失败。')
            response.encoding = 'utf-8'
            try:
                payload = response.json()
            except ValueError:
                raise ValueError('接口未返回有效 JSON，可能返回了错误或验证页面。') from None
        result['epgs'], result['msg'] = parse_programmes_btv(payload, channel, channel_id, dt)
        result['success'] = 1
    except requests.exceptions.Timeout:
        result['msg'] = '北京卫视：请求超时，请检查网络或稍后重试。'
    except requests.exceptions.RequestException as exc:
        result['msg'] = f'北京卫视：网络请求失败（{type(exc).__name__}）。'
    except ValueError as exc:
        result['msg'] = f'北京卫视：{exc}'
    except Exception as exc:
        result['msg'] = f'北京卫视：节目解析异常（{type(exc).__name__}）。'
    return result


@catalogue_result('北京电视台')
def get_channels_btv():
    channels = []
    url = "https://www.brtv.org.cn/gbdsb.shtml"
    res = requests.get(url, headers=headers)
    res.encoding = "utf-8"
    soup = bs(res.text, "html.parser")
    lis = soup.select("div.conWrapper > div.templateBox > ul > li")
    for li in lis:
        name = li.div.text.replace("\n", "").strip()
        id = li.attrs["channelid"]
        channel = {
            "name": name,
            "id": [id],
            "url": "https://www.brtv.org.cn/gbdsb.shtml",  # url,
            "source": "btv",
            "logo": "",
            "desc": "",
            "sort": "北京",
        }
        channels.append(channel)
    infos = soup.select("div.introductionWrapper")
    n = 0
    for info in infos:
        desc = (
            info.text.strip()
            .replace(" ", "")
            .replace("\t", "")
            .replace("\r", "")
            .replace("\n\n", "\n")
            .replace("\n\n", "\n")
        )
        if "青年频道" in desc:
            continue
        if n >= len(channels):
            break
        channels[n]["desc"] = desc
        n += 1
    return channels
