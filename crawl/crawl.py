# -*- coding:utf-8 -*-
import os, django, sys, datetime, platform
from django.utils import timezone
from utils.general import argvs_get, in_exclude_channel
from utils.aboutdb import log
from .spiders import epg_func
from dateutil import tz

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "epg.settings")
django.setup()
from web.models import Channel, Epg
from utils.general import (
    crawl_info,
    xmlinfo,
    dirs,
    add_info_title,
    add_info_desc,
    noepg,
)

recrawl, cname, crawl_dt, save_to_db = argvs_get(sys.argv)

tz_sh = tz.gettz("Asia/Shanghai")
# 抓取入口


def main():
    from web.failures import failed as record_failure
    from web.options import apply_legacy
    opts=apply_legacy()
    from web.runtime_config import get_config
    config=get_config()
    # Fix the task's date window even if a long crawl crosses midnight.
    run_date=timezone.localdate()
    daily_full=bool(opts['daily_first_full_refresh'] and save_to_db and not cname
        and not os.getenv('EPG_CHANNEL_IDS') and config.last_daily_full_refresh!=run_date)
    if daily_full:
        log(f'{run_date} 每日首次全量重抓：本轮刷新全部启用频道及配置的抓取日期范围。',level=2)
    # log_start = 'crawl-crawl-main '
    log_start = ""
    max_crawl_days = crawl_info["max_crawl_days"]
    recrawl_days = crawl_info["recrawl_days"]
    epgs_no = 0  # 记录最大的节目单条数
    # 循环获取每天的数据
    for d in range(max_crawl_days):
        ban_channels = []  # 被BAN掉的频道
        dt = (
            run_date + datetime.timedelta(days=d)
            if not cname
            else crawl_dt
        )
        # 单独使用命令获取某一个频道的信息
        if cname:
            channels = Channel.get_spec_channel(Channel, name=cname)
            max_crawl_days = 1
        else:
            if recrawl and d < recrawl_days:
                recrawl1 = 1
            else:
                recrawl1 = 0
            channels = Channel.get_crawl_channels(Channel, dt, recrawl=recrawl1)
        if opts['enable_recrawl'] and opts['refresh_existing'] and d<recrawl_days:
            channels=Channel.get_crawl_channels(Channel,dt,recrawl=1)
        if daily_full:
            channels=Channel.objects.filter(ineed=1,has_epg=1)
        if os.getenv('EPG_CHANNEL_IDS'):
            channels = Channel.objects.filter(pk__in=[int(x) for x in os.environ['EPG_CHANNEL_IDS'].split(',')], ineed=1, has_epg=1)
        channel_num = 0  # 已经采集的频道数量（成功与不成功都有）
        failed_channels = []  # 获取失败的频道
        success_num = 0  # 获取成功的频道
        channel_queryset_no = 0
        pending_channels = list(channels)
        channel_no = channels.count()  # 所有需要采集的数量
        log(
            "%s\t第%s天 共有：%s 个频道节目表需要获取"
            % (dt.strftime("%Y-%m-%d"), d + 1, channel_no)
        )
        # 循环获取每一频道的数据
        while True:
            if channel_queryset_no >= len(pending_channels):
                break
            channel = pending_channels[channel_queryset_no]
            channel_queryset_no += 1
            channel_num += 1
            if channel_num > max(1, channel_no) * 2:
                log('来源持续拒绝请求，结束本轮，稍后重试。', level=2)
                break
            msg1 = "%s/%s %s-%s" % (channel_num, channel_no, channel.id, channel.name)
            try:
                ret = get_epg(channel, dt, earliest_date=run_date) if daily_full else get_epg(channel, dt)
            except Exception as exc:
                log(f'{channel.name} 来源异常：{exc}', level=2)
                ret = {'success': 0, 'epgs': [], 'msg': str(exc), 'ban': 0, 'source': channel.source}
            if "ban" not in ret:
                ret.update({"ban": 0})
            if ret["ban"] == 1:  # 被BAN掉处理方式
                msg2 = "IP已经被BAN，先采集下一频道%s" % ret["msg"]
                msg5 = ""
                if channel.pk not in ban_channels:
                    ban_channels.append(channel.pk)
                    pending_channels.append(channel)
                    msg5 = "稍后在本轮末尾再尝试一次，"
                else:
                    failed_channels.append(f'{channel.pk} {channel.name}')
                    if save_to_db and not cname:record_failure(channel,dt,ret.get('msg') or '来源持续拒绝请求。')
                    msg5 = "第二次仍被拒绝，结束此频道，"
                log("%s,%s,%s,%s" % (log_start, msg1, msg2, msg5))
                continue
            elif (
                ret["ban"] == 0 and channel.pk in ban_channels
            ):  # 已经获取到的就从BAN列表中删除掉
                ban_channels.remove(channel.pk)
            msg2 = "共获取节目数量:%s,%s" % (len(ret["epgs"]), ret["msg"])
            # 如果只是获取某一频道测试，则显示此epgs信息
            if cname:
                msg1 = "测试---%s-%s" % (msg1, channel.source)
                for ep in ret["epgs"]:
                    print("%s\t%s\t%s" % (ep["starttime"], ep["title"], ep["endtime"]))
            if ret['epgs']:
                try:
                    if save_to_db:
                        from web.ingest import save_programmes
                        saved_count = save_programmes(channel, ret,
                            date_range=(run_date,run_date+datetime.timedelta(days=max_crawl_days-1))) if daily_full else save_programmes(channel, ret)
                        log(f'{msg1} 来源 {ret["source"]}：已保存 {saved_count} 条节目。')
                    else:
                        log(f'{msg1}：测试模式，未保存。')
                    success_num += 1
                except Exception as exc:
                    failed_channels.append(f'{channel.id} {channel.name}')
                    if save_to_db and not cname:record_failure(channel,dt,exc)
                    log(f'{msg1} 保存失败，已保留原数据：{exc}', level=2)
            else:
                failed_channels.append(f'{channel.id} {channel.name}')
                if save_to_db and not cname:record_failure(channel,dt,ret.get('msg') or '所有来源均未返回节目。')
            if not cname:  # 单独获取某一频道信息时，不剔除。
                channels = channels.exclude(id=channel.id)  # 将本条记录从记录列表中剔除
        if (
            cname
        ):  # 如果测试则只获取需要测试的那天数据,并不再做下面的生成xml及html等工作
            return
        msgn1 = (
            "，获取失败的为：%s" % (",".join(failed_channels))
            if len(failed_channels) > 0
            else ""
        )
        msg_failed = (
            ",%s失败" % (channel_no - success_num)
            if channel_no - success_num > 0
            else ""
        )
        msgn = "第 %s 天，共有%s/%s成功%s%s" % (
            d + 1,
            success_num,
            channel_no,
            msg_failed,
            msgn1,
        )
        log(msgn)
        # 生成xml文件
    for s in xmlinfo:
        xmlgen_ret = gen_xml(s)
        epgs_no1, epgsdir = xmlgen_ret[0], xmlgen_ret[1]
        epgs_no = epgs_no1 if epgs_no1 > epgs_no else epgs_no
        import gzip, shutil
        with open(epgsdir, 'rb') as src, gzip.open(epgsdir + '.gz.tmp', 'wb') as dst:
            shutil.copyfileobj(src, dst)
        os.replace(epgsdir + '.gz.tmp', epgsdir + '.gz')
        log('压缩文件已生成：' + epgsdir + '.gz')
        # 生成html主页
    # 生成test.m3u
    channels = Channel.get_need_channels(Channel, "all")[0]
    gen_test_m3u_ret = gen_test_m3u(channels, dirs["testm3u_dir"])
    if gen_test_m3u_ret[0]:
        log(gen_test_m3u_ret[1])
    if opts['enable_retention']:
        cutoff=timezone.localdate()-datetime.timedelta(days=opts['retain_days'])
        Epg.objects.filter(program_date__lt=cutoff).delete()
    if daily_full:
        from web.models import RuntimeConfig
        RuntimeConfig.objects.filter(pk=config.pk).update(last_daily_full_refresh=run_date)
        log(f'{run_date} 每日首次全量重抓轮次已完成；今日后续任务恢复常规抓取规则。',level=2)


# 获取节目表的核心程序，包含重试，换源
def get_epg(channel, dt, func_arg=0, earliest_date=None):
    from .spiders import epg_funcs
    from web.options import options
    import time
    opts=options()
    from web.catalog import parse_mapping_entries
    from django.core.exceptions import ValidationError
    try:
        mappings=[(source,identifier) for source,identifier in parse_mapping_entries(channel.channel_id) if source in epg_funcs]
    except ValidationError as exc:
        return {'success':0,'epgs':[],'msg':str(exc),'ban':0,'source':channel.source}
    primary=channel.source
    first=next((index for index,(source,_) in enumerate(mappings) if source==primary),None)
    sources=[mappings[first]] if first is not None else []
    if opts['fallback']:
        sources.extend(pair for index,pair in enumerate(mappings) if index!=first)
    if not sources:
        return {'success':0,'epgs':[],'msg':'主来源未配置或未注册，且没有可用备用来源。','ban':0,'source':primary}
    ret={'success':0,'epgs':[],'msg':'没有可用节目。','ban':0,'source':primary}
    for source,identifier in sources:
        for attempt in range(1,opts['retries']+1):
            ret=epg_func(channel,identifier,dt,func_arg=func_arg,source=source)
            ret={**ret,'source':source,'source_identifier':identifier}
            if ret.get('epgs') or ret.get('ban'):
                break
            log(f'{channel.pk}-{channel.name}-{source} ({identifier}) 第 {attempt}/{opts["retries"]} 次获取失败：{ret.get("msg","")}',level=2)
            if attempt<opts['retries'] and opts['retry_delay_seconds']:
                time.sleep(opts['retry_delay_seconds'])
        if ret.get('epgs'):
            if opts['quality_check']:
                try:
                    from web.programmes import prepare
                    from web.quality import check
                    requested=[row for row in prepare(ret['epgs'],strict=True) if row['program_date']==dt]
                    if not requested:raise ValueError('来源没有返回所需日期的节目。')
                    check(channel,requested)
                except Exception as exc:
                    ret={**ret,'success':0,'epgs':[],'ban':0,'msg':str(exc)}
                    log(f'{channel.name}-{source} 完整性检查未通过，继续按 ID 顺序尝试备用来源。',level=2)
                    continue
            try:
                from web.programmes import add_previous_context
                if earliest_date is None or dt>earliest_date:
                    ret=add_previous_context(channel,ret,dt,identifier,source)
            except Exception as exc:
                log(f'{channel.name} 前一日衔接未补充：{type(exc).__name__}',level=2)
            return ret
    return ret


# 生成某个分类的XML(所有、央视及卫视、地方、海外）
def gen_xml(sort):
    from web.options import apply_legacy
    from web.xml_output import text as xml_text, attribute as xml_attribute
    from web.programmes import project
    from types import SimpleNamespace
    opts=apply_legacy()
    add_info_title,add_info_desc=opts['title_suffix'],opts['description_suffix']
    if cname:  # 测试时不生成
        return 0
    xmlhead = '<?xml version="1.0" encoding="UTF-8"?><tv generator-info-name="EPG Console">'
    xmlbottom = "</tv>"
    get_days = crawl_info["gen_xml_days"]
    xmldir = "%s/%s" % (dirs["share"], xmlinfo[sort]["basename"])
    tz = " +0800"
    need_date = timezone.localdate() + datetime.timedelta(days=get_days-1)
    channels = Channel.get_need_channels(Channel, xmlinfo[sort]["sortname"])
    from django.db.models import Q
    visible=channels[0].exclude(Q(sort__in=opts['exclude_categories']) & ~Q(name__in=opts['include_channels']) & ~Q(tvg_name__in=opts['include_channels']))
    channels=[visible,visible.values_list('pk')]
    epgs = Epg.get_epgs(Epg, channels[1], need_date)
    log(
        "crawl-gen_xml共有：%s 个频道,%s条信息需要生成"
        % (channels[1].count(), epgs.count())
    )
    f = open(xmldir + '.tmp', "w", encoding="utf-8")
    f.write(xmlhead)
    xml_ids = {str(channel.pk): channel.xmltv_id for channel in channels[0]}
    for channel in channels[0]:
        if (
            channel.sort in in_exclude_channel["xml"]
            and channel.name not in in_exclude_channel["include_xml"]
            and channel.tvg_name not in in_exclude_channel["include_xml"]
        ):
            continue
        c = '<channel id=%s><display-name lang="zh">%s</display-name>' % (
            xml_attribute(channel.xmltv_id),
            xml_text(channel.name if opts['export_display_name']=='name' else channel.tvg_name),
        )
        if opts['export_include_logos'] and channel.logo:
            from urllib.parse import urlsplit
            try:
                logo=urlsplit(channel.logo)
                if logo.scheme in ('http','https') and logo.hostname:
                    c += '<icon src=%s/>' % xml_attribute(channel.logo)
            except ValueError:
                pass
        f.write(c+'</channel>')
    noepg_channel = (
        '<channel id="9999"><display-name lang="zh">noepg</display-name></channel>'
    )
    if opts['export_include_placeholder']:
        f.write(noepg_channel)
    projected=project([epg.to_dict() for epg in epgs],channel_ids=list(xml_ids),date_range=(timezone.localdate(),need_date))
    projected=[row for row in projected if timezone.localdate()<=row['program_date']<=need_date]
    for row in projected:
        epg=SimpleNamespace(**row)
        if epg.endtime is None:
            epg.endtime=timezone.make_aware(datetime.datetime.combine(epg.program_date,datetime.time(23,59,59)))
        start = epg.starttime.astimezone(tz=tz_sh).strftime("%Y%m%d%H%M%S") + tz
        end = epg.endtime.astimezone(tz=tz_sh).strftime("%Y%m%d%H%M%S") + tz
        id = xml_attribute(xml_ids.get(str(epg.channel_id), str(epg.channel_id)))
        from web.text_processing import display_text
        title = display_text(epg.title,opts) + ('' if row.get('gap_filler') else add_info_title)
        title = xml_text(title)
        desc = display_text(epg.descr,opts) + add_info_desc
        desc = xml_text(desc)
        programinfo = (
            """<programme start="%s" stop="%s" channel=%s><title lang="zh">%s</title><desc lang="zh">%s</desc></programme>"""
            % (start, end, id, title, desc)
        )
        f.write(programinfo)
    for x in range(get_days if opts['export_include_placeholder'] else 0):
        noepg_program_day = noepg(
            "noepg",
            "9999",
            (timezone.localdate() + datetime.timedelta(days=x)),
        )
        f.write(noepg_program_day)
    f.write(xmlbottom)
    f.close()
    os.replace(xmldir + '.tmp', xmldir)
    log(
        "crawl-gen_xml 已经生成:%s 分类xml文件：%s"
        % (xmlinfo[sort]["sortname"], xmldir)
    )
    return [len(projected), xmldir]


def gen_test_m3u(channels, test_dir):
    with open(test_dir, "w", encoding="utf-8", errors="ignore") as f:
        f.write("#EXTM3U\n")
        n = 0  # 计数，用于随机填写节目源
        for channel in channels:
            n += 1
            logo = channel.logo
            tvgname = channel.tvg_name
            name = channel.name
            grouptitle = channel.sort
            channel_id = channel.xmltv_id
            if grouptitle in ["香港", "澳门", "海外"]:
                grouptitle = "港澳及国外"
            if grouptitle not in ["央视", "卫视", "港澳及国外", "台湾", "数字付费"]:
                grouptitle = "地方台"

            line = (
                '#EXTINF:-1 tvg-logo="%s" tvg-id="%s" tvg-name="%s" group-title="%s",%s\n'
                % (logo, channel_id, tvgname, grouptitle, name)
            )
            line1 = "http://192.168.1.1/%s.ts\n" % (n)
            f.write(line)
            f.write(line1)
    msg = "生成%s个频道的测试m3u文件：%s" % (channels.count(), dirs["testm3u_dir"])
    return [1, msg]

