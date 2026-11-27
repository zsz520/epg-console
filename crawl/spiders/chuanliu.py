# -*- coding:utf-8 -*-
from crawl.source_utils import source_http as requests, programme_result, catalogue_result, SHANGHAI, SourceError
import datetime, json, os, re
import hashlib
import uuid
# 默认授权集中在本文件；仍可通过 CHUANLIU_AUTHORIZATION 环境变量覆盖。
DEFAULT_AUTHORIZATION = 'Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiI5ODQwODlhNjc1OGU0ZjJlOTViMjk4NWM4YjA1MDNmYiIsImNvbXBhbnkiOiJxaXlpIiwibmFtZSI6InRlcm1pbmFsIn0.1gDPpBcHJIE8dLiq7UekUlPWMtJOYymI8zoIYlsVgc4'

def authorization_headers():
    value = (os.getenv('CHUANLIU_AUTHORIZATION') or DEFAULT_AUTHORIZATION).strip()
    if not value:
        raise ValueError('川流授权未配置。')
    if not value.lower().startswith('bearer '):
        value = 'Bearer ' + value
    return {'Authorization': value, 'User-Agent': 'okhttp/3.10.0.7'}


def read_payload(response):
    try:
        try:
            payload = json.loads(response.content)
        except UnicodeDecodeError:
            payload = json.loads(response.content.decode('gb18030'))
    except (ValueError, UnicodeError):
        raise SourceError('接口未返回有效 JSON，可能返回错误或验证页面。') from None
    if not isinstance(payload, dict):
        raise SourceError('接口返回结构异常，未覆盖原数据。')
    code = payload.get('ret_status')
    if code not in (None, 0, '0'):
        message = str(payload.get('ret_msg') or '接口业务错误。')[:200]
        auth = bool(re.search(r'token|auth|登录|鉴权|授权|认证|过期', message, re.I))
        raise SourceError(f'接口业务错误 {code}：{message}', ban=int(auth))
    if 'ret_data' not in payload:
        raise SourceError('接口缺少 ret_data 字段，返回结构异常。')
    return payload


def parse_programmes_chuanliu(payload, channel, channel_id, day):
    rows = payload.get('ret_data')
    if rows is None or rows == []:
        raise SourceError(f'频道 {channel_id} 在 {day} 暂无节目：接口请求成功，但未提供该日期节目。')
    if not isinstance(rows, list):
        raise SourceError('接口节目数据不是列表，未覆盖原数据。')
    records = {}
    zero = duplicates = repaired = 0
    for number, item in enumerate(rows, 1):
        if not isinstance(item, dict):
            raise SourceError(f'第 {number} 条节目结构无效。')
        if item.get('channel') is not None and str(item['channel']) != str(channel_id):
            raise SourceError('接口返回了其他频道的节目，未写入。')
        try:
            start = datetime.datetime.strptime(item['begin_time'], '%Y-%m-%d %H:%M:%S').replace(tzinfo=SHANGHAI)
        except (KeyError, TypeError, ValueError):
            raise SourceError(f'第 {number} 条节目开始时间无效。') from None
        try:
            stop = datetime.datetime.strptime(item.get('end_time'), '%Y-%m-%d %H:%M:%S').replace(tzinfo=SHANGHAI)
        except (TypeError, ValueError):
            stop = None
        # The provider emits duplicate event markers whose start equals stop.
        # Remove these before common inference turns them into ambiguous rows.
        if stop == start:
            zero += 1
            continue
        if stop is not None and stop < start:
            stop = None
            repaired += 1
        title = str(item.get('name') or '').strip()
        if not title:
            raise SourceError(f'第 {number} 条节目名称为空。')
        row = {'channel_id': channel.id, 'starttime': start, 'endtime': stop,
            'title': title, 'desc': str(item.get('desc') or ''), 'program_date': start.date()}
        previous = records.get(start)
        if previous:
            if previous['title'] != title or (previous['endtime'] and stop and previous['endtime'] != stop):
                raise SourceError(f'频道 {channel_id} 在 {start:%Y-%m-%d %H:%M:%S} 有多条不同的有效节目，无法确定，保留原节目。')
            # Same programme: retain the explicit valid stop and richer description.
            previous['endtime'] = previous['endtime'] or stop
            if len(row['desc']) > len(previous['desc']):
                previous['desc'] = row['desc']
            duplicates += 1
        else:
            records[start] = row
    result = sorted(records.values(), key=lambda row: row['starttime'])
    if not result:
        raise SourceError(f'频道 {channel_id} 在 {day} 只有零时长标记，没有有效节目。')
    notes = [f'获取 {len(result)} 条节目。']
    if zero:
        notes.append(f'已跳过 {zero} 条零时长记录。')
    if duplicates:
        notes.append(f'已合并 {duplicates} 条同节目重复记录。')
    if repaired:
        notes.append(f'{repaired} 条结束时间异常，交由公共逻辑推断。')
    return result, ''.join(notes)


@programme_result('川流 TV')
def get_epgs_chuanliu(channel, channel_id, dt, func_arg):
    if not re.fullmatch(r'\d+', str(channel_id)):
        raise SourceError('频道 ID 应为单个数字 ID，例如 3342。')
    need_date = dt.strftime("%Y-%m-%d")
    url = (
        "http://epg.iqy.sc96655.com/v1/getPrograms?channel=%s&begin_time=%s 00:00:00&end_time=%s 23:59:59&partner=2"
        % (channel_id, need_date, need_date)
    )
    res = requests.get(url, headers=authorization_headers(), timeout=(8, 15))
    epgs, message = parse_programmes_chuanliu(read_payload(res), channel, channel_id, dt)
    return {'success': 1, 'epgs': epgs, 'msg': message, 'last_program_date': dt, 'ban': 0}


"""
获取所有频道信息
"""


@catalogue_result('川流 TV')
def get_channels_chuanliu():
    channels = []
    sorts = {
        "1008": "教育频道",
        "1010": "4K超高清",
        "1002": "央视",
        "1003": "卫视",
        "1004": "本地频道",
        "1014": "试播频道",
        "1012": "休闲生活",
        "1013": "8K超高清",
    }
    url = "http://epg.iqy.sc96655.com/v1/getChannels?partner=2&terminal=&definition=&citycode=&adcode=&charge_type=&channel_type="
    res = requests.get(url, headers=authorization_headers(), timeout=10)
    res.raise_for_status()
    j = read_payload(res)
    ret_data = j["ret_data"]
    if not isinstance(ret_data, list):
        raise ValueError('接口请求成功，但没有提供有效频道目录。')
    logos=channel_logos_chuanliu()
    for c in ret_data:
        name = c["bd_name"]
        name2 = c["name"]
        name = name if len(name) > 0 else name2
        id = c["id"]
        sort_type = c["channelType"]
        sort = sorts[sort_type] if sort_type in sorts else "其他"
        channel = {
            "name": name,
            "id": [id],
            "url": "",  # url,
            "source": "chuanliu",
            "logo": c.get('logo') or c.get('icon_url') or logos.get(str(id),''),
            "desc": "",
            "sort": sort,
        }
        channels.append(channel)
    return channels


def channel_logos_chuanliu():
    """3.1.1 APK 的独立推荐配置接口；按 ch_id 对应，缺失时不猜台标。"""
    params={'open_token':'','card_count_limit':'1000','device_version':'scni_apk'}
    app_key='9bbOVW7r8VqvCfk7'
    signature=''.join(key+'='+value+'|' for key,value in sorted(params.items()))+app_key
    params['sign']=hashlib.md5(signature.encode()).hexdigest()
    try:
        response=requests.post('https://scn-integration-public.ptqy.gitv.tv/public/api/config/live_recommend_card',
            data=params,headers={'X-Request-Id':str(uuid.uuid4())},timeout=(5,10))
        data=response.json()
        if data.get('status',{}).get('code')!='C00000':
            raise ValueError('配置接口未返回成功状态。')
        rows=data.get('data',{}).get('config',{}).get('live_recommend_card',[])
        return {str(row['ch_id']):row['icon_url'] for row in rows
                if row.get('ch_id') is not None and str(row.get('icon_url','')).startswith(('https://','http://'))}
    except (ValueError,TypeError,KeyError,requests.exceptions.RequestException):
        print('川流 TV：台标配置暂不可用，保留频道目录和已有台标。')
        return {}


def get_sorts_type():
    url = "http://epg.iqy.sc96655.com/v1/getChannelType?partner=2&terminal="
    res = requests.get(url, headers=authorization_headers(), timeout=10)
    ret_data = read_payload(res)["ret_data"]
    n = 0
    for j in ret_data:
        if n == 0:
            n += 1
            continue
        key = j["pramKey"]
        sortname = j["pramValue"]
        print(key, sortname)
