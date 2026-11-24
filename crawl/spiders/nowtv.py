# -*- coding:utf-8 -*-
from crawl.source_utils import source_http as requests, programme_result, catalogue_result, SHANGHAI
import re, datetime, json, os
from utils.general import headers

# urllib3 2.x uses the platform TLS defaults and no longer exposes DEFAULT_CIPHERS.
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 6.1; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/79.0.3945.79 Safari/537.36",
    "X-Requested-With": "XMLHttpRequest",
    "Content-Type": "application/x-www-form-urlencoded",
    "Referer": "https://now-tv.now.com/epg/",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept": "application/json, text/javascript, */*",
    "Pragma": "no-cache",
    "Accept-Encoding": "gzip, deflate",
    "Proxy-Connection": "keep-alive",
}


def request_now(path):
    errors = []
    for host in ('https://now-tv.now.com', 'https://nowtv.now.com'):
        try:
            return requests.get(host + path, headers=headers, timeout=(8, 15))
        except ValueError as exc:
            errors.append(str(exc))
    raise ValueError('两个官方域名均访问失败：' + '；'.join(errors))


@programme_result('NOW TV')
def get_epgs_nowtv(channel, channel_id, dt, func_arg):
    if not re.fullmatch(r'G\d{2}-\d+', str(channel_id)):
        raise ValueError('频道 ID 格式应为 G05-541。')
    epgs = []
    msg = ""
    success = 1
    path = "/gw-epg/epg/zh_tw/%s/prf0/resp-genre/ch_%s.json" % (
        dt.strftime("%Y%m%d"),
        channel_id[:3],
    )  # d=0表示当天，至少能获取最近七天数据
    try:
        res = request_now(path)
        res.encoding = "utf-8"
        j = res.json()
        chs = j["data"]["chProgram"]  # 很多频道的这一天的节目表
        for ch in chs:
            if (
                "ids" == ch or ch != channel_id[4:]
            ):  # 一个记录，记录显示的所有ID；只有ID和需要获取的ID相同才处理。
                continue
            nowtvid = "%s-%s" % (channel_id[:3], ch.strip())  # 拼出nowtv的id
            for channelepg in chs[ch]:
                starttime = datetime.datetime.fromtimestamp(channelepg["start"] / 1000, SHANGHAI)
                endtime = datetime.datetime.fromtimestamp(channelepg["end"] / 1000, SHANGHAI)
                title = channelepg["name"]
                desc = channelepg.get("synopsis") or ""

                epg = {
                    "channel_id": channel.id,
                    "starttime": starttime,
                    "endtime": endtime,
                    "title": title,
                    "desc": desc,
                    "program_date": starttime.date(),
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


# 获取所有最新频道列表
@catalogue_result('NOW TV')
def get_channels_nowtv():
    url = "https://now-tv.now.com/gw-epg/epg/channelMapping.zh-TW.js"
    res = request_now('/gw-epg/epg/channelMapping.zh-TW.js')
    res.encoding = "utf-8"
    reinfo = re.search(r'\bvar\s+ChannelMapping\s*=\s*', res.text)
    if not reinfo:
        raise ValueError('未找到频道映射，网站可能已改版。')
    cs, _ = json.JSONDecoder().raw_decode(res.text[reinfo.end():].lstrip())
    channels = []
    for c in cs:
        if "name" not in cs[c]:
            continue
        if not cs[c].get("genreKeys"):
            continue
        id1 = cs[c]["genreKeys"][0]
        id2 = c
        channel_id = "%s-%s" % (id1, id2)
        name = cs[c]["name"]
        channel = {
            "name": name,
            "id": [channel_id],
            "url": url,
            "source": "nowtv",
            "logo": "",
            "desc": "",
            "sort": "海外",
        }
        channels.append(channel)
    return channels
