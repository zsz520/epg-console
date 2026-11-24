# -*- coding:utf-8 -*-
import requests, datetime, os
from .chuanliu_defaults import DEFAULT_AUTHORIZATION

def authorization_headers():
    value = (os.getenv('CHUANLIU_AUTHORIZATION') or DEFAULT_AUTHORIZATION).strip()
    if not value:
        raise ValueError('川流授权未配置。')
    if not value.lower().startswith('bearer '):
        value = 'Bearer ' + value
    return {'Authorization': value, 'User-Agent': 'okhttp/3.10.0.7'}


def get_epgs_chuanliu(channel, channel_id, dt, func_arg):
    epgs = []
    msg = ""
    success = 1
    need_date = dt.strftime("%Y-%m-%d")
    url = (
        "http://epg.iqy.sc96655.com/v1/getPrograms?channel=%s&begin_time=%s 00:00:00&end_time=%s 23:59:59&partner=2"
        % (channel_id, need_date, need_date)
    )
    try:
        res = requests.get(url, headers=authorization_headers(), timeout=5)
        res.raise_for_status()
        res.encoding = "utf-8"
        ret_data = res.json()["ret_data"]
        if not isinstance(ret_data, list):
            raise ValueError('川流接口未返回节目列表，请检查 token 与接口状态。')
        n = 0  # 计数 节目表的第几个节目
        for j in ret_data:
            n += 1
            title = j["name"]
            channel_name = j["bd_name"]
            desc = j["desc"]
            starttime = j["begin_time"]  # 2023-02-18 15:42:00'
            endtime = j["end_time"]
            starttime = datetime.datetime.strptime(starttime, "%Y-%m-%d %H:%M:%S")
            endtime = datetime.datetime.strptime(endtime, "%Y-%m-%d %H:%M:%S")
            epg = {
                "channel_id": channel.id,
                "starttime": starttime,
                "endtime": endtime,
                "title": title,
                "desc": desc,
                "program_date": dt,
            }
            epgs.append(epg)
    except Exception as e:
        success = 0
        spidername = os.path.basename(__file__).split(".")[0]
        msg = "spider-%s- %s" % (spidername, e)
    ret = {
        "success": success,
        "epgs": epgs,
        "msg": msg,
        "last_program_date": dt,
        "ban": 0,
    }
    return ret


"""
获取所有频道信息
"""


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
    res.encoding = "utf-8"
    j = res.json()
    ret_data = j["ret_data"]
    if not isinstance(ret_data, list):
        raise ValueError('川流接口未返回频道列表，请检查 token 与接口状态。')
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
            "logo": "",
            "desc": "",
            "sort": sort,
        }
        channels.append(channel)
    return channels


def get_sorts_type():
    url = "http://epg.iqy.sc96655.com/v1/getChannelType?partner=2&terminal="
    res = requests.get(url, headers=authorization_headers(), timeout=10)
    ret_data = res.json()["ret_data"]
    n = 0
    for j in ret_data:
        if n == 0:
            n += 1
            continue
        key = j["pramKey"]
        sortname = j["pramValue"]
        print(key, sortname)
