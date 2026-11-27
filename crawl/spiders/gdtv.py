# -*- coding:utf-8 -*-
from crawl.source_utils import source_http as requests, programme_result, catalogue_result, SHANGHAI
# 广东广播电视台-官网来源,2021-12-16添加到数据库，只有广东地区频道共10多个
import datetime, os
import xml.etree.ElementTree as ET
from utils.general import headers
# 官网 tvChannelDetail 页面使用的统一广东台标识，不按节目目录 ID 猜图片。
# CHANNEL_LOGO = 'https://image-touchtv.oss-cn-shenzhen.aliyuncs.com/sitecdn/static-2025/standalone/20250724/file/gd_logo.jpg'
CHANNEL_LOGO = 'https://img.gdtv.cn/image/202010/0.267579252589195161fbecf64d4705c1fOSS1603767828.png'


@programme_result('广东广电')
def get_epgs_gdtv(channel, channel_id, dt, func_arg):
    epgs = []
    msg = ""
    success = 1
    try:
        url = "http://epg.gdtv.cn/f/%s/%s.xml" % (channel_id, dt.strftime("%Y-%m-%d"))
        res = requests.get(url, headers=headers, timeout=8)
        res.encoding = "utf-8"
        root = ET.fromstring(res.content)
        identity = root.find('channel')
        if identity is None or str(identity.get('id')) != str(channel_id):
            raise ValueError('接口返回的频道 ID 与请求不符。')
        epgs_contents = root.findall('.//epglist/content')
        epgs = []
        for epga in epgs_contents:
            starttime = datetime.datetime.fromtimestamp(int(epga.attrib["time1"]), SHANGHAI)
            endtime = datetime.datetime.fromtimestamp(int(epga.attrib["time2"]), SHANGHAI)
            title = ''.join(epga.itertext()).strip()
            epg = {
                "channel_id": channel.id,
                "starttime": starttime,
                "endtime": endtime,
                "title": title,
                "desc": "",
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


@catalogue_result('广东广电')
def get_channels_gdtv():
    url = "http://epg.gdtv.cn/f/1.xml"
    res = requests.get(url, headers=headers)
    res.encoding = "utf-8"
    root = ET.fromstring(res.content)
    contents = root.findall('.//channel')
    channels = []
    for content in contents:
        id = content.attrib["id"]
        name = content.findtext('ctitle')
        cdate = content.findtext('cdate')
        channel = {
            "name": name,
            "id": [id],
            "url": "http://epg.gdtv.cn/",
            "source": "gdtv",
            "logo": CHANNEL_LOGO,
            "desc": "",
            "sort": "广东",
            "newestdate": cdate,
        }
        channels.append(channel)
    return channels
