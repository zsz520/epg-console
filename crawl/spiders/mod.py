"""中华电信 MOD 官方频道目录与按日节目表。"""
import datetime
import re
from bs4 import BeautifulSoup
from crawl.source_utils import source_http as requests, programme_result, catalogue_result, SHANGHAI
from utils.general import headers

HOST = 'https://mod.cht.com.tw'
EPG_URL = 'https://modp.cht.com.tw/modinfo/epginfob.php'


def parse_programmes_mod(text, channel, day):
    soup = BeautifulSoup(text, 'html.parser')
    records = []
    for section in soup.select('.epgdays'):
        heading = section.find('div', class_='item', recursive=False)
        match = re.search(r'\d{4}-\d{2}-\d{2}', heading.get_text() if heading else '')
        if not match:
            raise ValueError('节目页缺少日期，未将其他日期节目写入。')
        actual_day = datetime.date.fromisoformat(match[0])
        if actual_day < day:
            continue
        for clock in section.select('.time'):
            title = clock.parent.select_one('.channelTitle')
            if title is None:
                raise ValueError('节目行缺少时间或标题。')
            value = datetime.datetime.strptime(clock.get_text(strip=True), '%H:%M').time()
            start = datetime.datetime.combine(actual_day, value, tzinfo=SHANGHAI)
            records.append({'channel_id': channel.id, 'starttime': start, 'endtime': None,
                'title': title.get_text(' ', strip=True), 'desc': '', 'program_date': actual_day})
    return records


@programme_result('中华电信 MOD')
def get_epgs_mod(channel, channel_id, dt, func_arg=None):
    if not re.fullmatch(r'\d+', str(channel_id)):
        raise ValueError('频道 ID 应为数字，例如 006。')
    response = requests.get(EPG_URL, params={'id': int(channel_id)}, headers=headers)
    # Read the labelled dates actually published; do not relabel today's list.
    records = parse_programmes_mod(response.text, channel, dt)
    return {'success': 1, 'epgs': records, 'msg': '', 'ban': 0, 'last_program_date': dt}


@catalogue_result('中华电信 MOD')
def get_channels_mod():
    url = HOST + '/頻道TV/全部.do'
    response = requests.get(url, headers=headers)
    soup = BeautifulSoup(response.text, 'html.parser')
    channels = []
    for anchor in soup.select('h3 a[onclick]'):
        match = re.search(r"linkToPlayProgram\(\s*['\"](\d+)['\"]", anchor['onclick'])
        if not match:
            continue
        identifier = str(int(match[1])).zfill(3)
        if anchor.get_text(strip=True) == '頻道總表':
            continue
        channels.append({'name': anchor.get_text(' ', strip=True), 'id': [identifier],
            'url': HOST + '/program.do?id=' + str(int(identifier)), 'source': 'mod',
            'logo': '', 'desc': '', 'sort': '海外'})
    return channels
