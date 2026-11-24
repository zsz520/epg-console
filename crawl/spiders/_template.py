"""新增来源示例：复制为 example.py 后，按真实接口修改配置和两个解析函数。

下划线开头文件不注册；完成后保存为 data/sources/example.py，重启 web/crawler 自动注册。示例地址不可用。
接口示例数据格式见项目根目录《新增来源模板说明.md》。
"""
import datetime
from zoneinfo import ZoneInfo
import requests

SOURCE_ID = 'example'
SOURCE_NAME = '示例来源'
CHANNELS_URL = 'https://example.invalid/api/channels'
EPG_URL = 'https://example.invalid/api/programmes'
ZONE = ZoneInfo('Asia/Shanghai')
HEADERS = {'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'}


def fetch_json(url, params=None):
    if 'example.invalid' in url:
        raise ValueError('请先填写真实接口地址，并按接口格式修改解析函数。')
    try:
        response = requests.get(url, params=params, headers=HEADERS, timeout=(5, 15))
        if response.status_code != 200:
            raise ValueError(f'{SOURCE_NAME} HTTP {response.status_code}')
        return response.json()
    except requests.exceptions.JSONDecodeError:
        raise ValueError(f'{SOURCE_NAME} 返回的不是有效 JSON。') from None
    except requests.RequestException:
        # 请求异常可能带完整 URL；不要把访问凭据写入任务日志。
        raise ValueError(f'{SOURCE_NAME} 网络请求失败。') from None


def parse_channels(payload):
    """示例格式：{'data': [{'id': '001', 'name': '示例频道', 'category': '地方'}]}。"""
    rows = payload.get('data') if isinstance(payload, dict) else None
    if not isinstance(rows, list) or not rows:
        raise ValueError(f'{SOURCE_NAME} 没有返回有效频道目录，保留原目录。')
    channels = {}
    for row in rows:
        identifier = str(row['id']).strip() if row.get('id') is not None else ''
        name = str(row.get('name') or '').strip()
        if not identifier or not name:
            raise ValueError(f'{SOURCE_NAME} 频道信息不完整。')
        channels[identifier] = {
            'name': name,
            'id': [identifier],
            'source': SOURCE_ID,
            'sort': str(row.get('category') or '其他'),
            'url': '',
            'logo': str(row.get('logo') or ''),
            'desc': '',
        }
    return list(channels.values())


def parse_programmes(payload, channel, requested_date):
    """示例格式：data 列表包含 date、start_time、duration、title、desc。"""
    rows = payload.get('data') if isinstance(payload, dict) else None
    if not isinstance(rows, list) or not rows:
        raise ValueError(f'{SOURCE_NAME} 没有返回节目，保留原节目。')
    programmes = {}
    for row in rows:
        # 必须使用接口中的真实日期，不能把旧节目平移到请求日期。
        actual_date = datetime.date.fromisoformat(str(row['date']))
        if actual_date != requested_date:
            continue
        title = str(row.get('title') or '').strip()
        start = datetime.datetime.combine(
            actual_date, datetime.time.fromisoformat(str(row['start_time'])), ZONE)
        duration = int(row['duration'])  # 示例接口约定 duration 单位为秒。
        if not title or not 0 < duration <= 86400:
            raise ValueError(f'{SOURCE_NAME} 节目名称或时长无效。')
        end = start + datetime.timedelta(seconds=duration)
        programmes[start, end, title] = {
            'channel_id': channel.id,  # 内部数据库编号，不是网站频道 ID！
            'starttime': start,
            'endtime': end,
            'title': title,
            'desc': str(row.get('desc') or ''),
            'program_date': actual_date,
        }
    if not programmes:
        raise ValueError(f'{SOURCE_NAME} 没有请求日期的节目，保留原节目。')
    return sorted(programmes.values(), key=lambda item: item['starttime'])


def get_channels_example():
    # 如果真实接口分页，要取齐并验证所有分页后再返回。
    return parse_channels(fetch_json(CHANNELS_URL))


def get_epgs_example(channel, channel_id, dt, func_arg=0):
    dt = dt.date() if isinstance(dt, datetime.datetime) else dt
    try:
        # channel_id 是网站 ID；参数名和请求方法要按真实接口修改。
        payload = fetch_json(EPG_URL, params={'channelId': channel_id, 'date': dt.isoformat()})
        epgs = parse_programmes(payload, channel, dt)
        return {'success': 1, 'epgs': epgs, 'msg': '', 'last_program_date': dt, 'ban': 0}
    except Exception as exc:
        return {'success': 0, 'epgs': [], 'msg': f'{SOURCE_NAME}：{exc}',
                'last_program_date': dt, 'ban': 0}
