# -*- coding:utf-8 -*-
from django.db import models, transaction
from django.conf import settings
from django.db.models import Q
from dateutil import tz
from utils.general import cht_to_chs, add_info_desc, add_info_title
import datetime
from django.utils import timezone

tz_sh = tz.gettz("Asia/Shanghai")


class RuntimeConfig(models.Model):
    last_daily_full_refresh = models.DateField(null=True, blank=True, editable=False)
    SCHEDULE_MODES = [('interval', '每隔一段时间'), ('daily', '每天指定时间点')]
    auto_catalog = models.BooleanField('自动抓取频道列表', default=False)
    catalog_on_start = models.BooleanField('频道列表首次启用立即抓取', default=False)
    epg_schedule_mode = models.CharField('节目单定时方式', max_length=10, choices=SCHEDULE_MODES, default='interval')
    epg_schedule_times = models.CharField('节目单每天抓取时间', max_length=1000, blank=True, default='06:00,18:00')
    catalog_schedule_mode = models.CharField('频道列表定时方式', max_length=10, choices=SCHEDULE_MODES, default='interval')
    catalog_schedule_times = models.CharField('频道列表每天抓取时间', max_length=1000, blank=True, default='04:00')
    catalog_interval_seconds = models.PositiveIntegerField('频道列表抓取间隔（秒）', default=86400)
    auto_xmltv = models.BooleanField('独立定时更新 XMLTV 来源', default=False)
    xmltv_on_start = models.BooleanField('XMLTV 首次启用立即更新', default=True)
    xmltv_schedule_mode = models.CharField('XMLTV 定时方式', max_length=10, choices=SCHEDULE_MODES, default='interval')
    xmltv_schedule_times = models.CharField('XMLTV 每天更新时间', max_length=1000, blank=True, default='05:00,17:00')
    xmltv_interval_seconds = models.PositiveIntegerField('XMLTV 更新间隔（秒）', default=21600)
    fill_programme_gaps = models.BooleanField('补齐缺失节目时段', default=True, help_text='用“精彩继续”补齐当天空档，与跨午夜开关独立。')
    split_midnight = models.BooleanField('按日分割跨午夜节目', default=True, help_text='统一处理所有来源；前一天显示到 23:59，次日从 00:00 继续。保留原始数据，可随时关闭。')
    options = models.JSONField(default=dict, blank=True)
    auto_crawl = models.BooleanField('自动抓取节目单', default=True)
    crawl_on_start = models.BooleanField('首次启动立即抓取', default=True)
    interval_seconds = models.PositiveIntegerField('抓取间隔（秒）', default=21600)
    task_timeout_seconds = models.PositiveIntegerField('单次任务超时（秒）', default=3600)
    source_timeout_seconds = models.PositiveIntegerField('单个来源目录超时（秒）', default=120)

    class Meta:
        verbose_name = '设置中心'
        verbose_name_plural = '设置中心'


class AutoScheduleState(models.Model):
    kind = models.CharField(max_length=20, primary_key=True)
    signature = models.TextField()
    next_run = models.DateTimeField()


class Channel(models.Model):
    sort_order = models.PositiveIntegerField('排序', default=0, db_index=True, help_text='数值越小越靠前；相同数值按 ID 排列。')
    from crawl.source_registry import get_source_choices
    source_choices = staticmethod(get_source_choices)
    need_get = [(1, "是"), (0, "否")]
    channel_id = models.CharField("频道来源网站ID", max_length=300, blank=True)
    tvg_name = models.CharField(
        "匹配用的tvg-name", null=False, max_length=100, db_index=True
    )
    name = models.CharField("频道显示名称", null=False, max_length=100, db_index=True)
    tvg_id = models.CharField('tvg-id', max_length=100, unique=True, null=True, blank=True,
                              help_text='XMLTV 对外频道 ID，可修改；留空时默认使用 tvg-name。同名频道请指定不同的 tvg-id。')
    sort = models.CharField(
        "频道分类", null=False, max_length=50, db_index=True
    )  # 1，2，3，4，5
    logo = models.CharField("台标地址", max_length=400, null=True, blank=True)
    last_program_date = models.DateField("最新节目日期", db_index=True, null=True, blank=True, default=datetime.date(2000, 1, 1))
    last_crawl_dt = models.DateTimeField("最近的采集日期", auto_now=True, null=True)
    create_dt = models.DateTimeField("创建日期", auto_now_add=True, null=True)
    descr = models.CharField("频道描述", max_length=500, null=True, blank=True)
    has_epg = models.IntegerField(
        "是否有节目表", choices=need_get, default=0, db_index=True
    )
    ineed = models.IntegerField(
        "是否需获取", choices=need_get, default=0, db_index=True
    )
    source = models.CharField(
        "节目来源", choices=get_source_choices, max_length=50, db_index=True, null=True, blank=True
    )
    recrawl = models.IntegerField(
        "需重新获取当天数据", choices=need_get, db_index=True, default=0,
        help_text='与总开关同时开启时，每轮重新获取当天已有节目；默认未来日期只补缺失，除非另开前 N 天重抓策略。需同时启用“是否有节目表”和“是否需获取”。保存不会立即抓取。'
    )
    patten = models.CharField("本频道正则", max_length=100, null=True, blank=True)
    remark = models.CharField("备注", max_length=500, null=True, blank=True)

    def __str__(self):
        return "-".join(
            [str(self.id), self.name, self.tvg_name, self.source if self.source else ""]
        )

    class Meta:
        verbose_name = "频道列表"
        verbose_name_plural = "频道列表"

    @property
    def xmltv_id(self):
        return self.tvg_id or str(self.pk)

    # 获取需要抓取的频道，如果需要重新抓取，则使用，recrawl = 1
    def get_crawl_channels(self, need_date, recrawl=0):
        from django.db.models import Exists, OuterRef, CharField
        from django.db.models.functions import Cast
        coverage=Epg.objects.filter(channel_id=Cast(OuterRef('pk'),output_field=CharField()),program_date=need_date)
        channels=self.objects.filter(ineed=1,has_epg=1).annotate(day_covered=Exists(coverage))
        from .options import options
        eligible=Q(day_covered=False)
        if options()['enable_recrawl']:
            if recrawl == 1 or need_date==timezone.localdate():eligible |= Q(recrawl=1)
        return channels.filter(eligible)
        # 获取指定名称的频道，用于测试,只返回获取到数据的第一个

    def get_spec_channel(self, name=0, id=0):
        if name:
            ret = self.objects.filter(tvg_name__icontains=name)[:1]
            if ret.count() == 0:
                ret = self.objects.filter(name__icontains=name)[:1]
        elif id:
            ret = self.objects.filter(id=id)
        else:
            ret = self.objects.filter(tvg_name="CCTV1")
        return ret

    # 获取某一频道的信息（严格匹配）
    def get_spec_channel_strict(self, name=0, id=0):
        if name:
            ret = self.objects.filter(tvg_name=name)
            if not ret.exists():
                ret = self.objects.filter(tvg_id=name)
        else:
            ret = self.objects.filter(id=id)
        return ret

    def get_need_channels(self, sorts):
        from .ordering import ordered_channels
        if sorts == "all":
            channels = self.objects.filter(ineed=1, has_epg=1)
        else:
            channels = self.objects.filter(sort__in=sorts, ineed=1, has_epg=1)
        channels = ordered_channels(channels)
        return [channels, channels.values_list("id")]

    def get_match_channels(self):
        channels = self.objects.filter(ineed=1, has_epg=1)
        return channels

    def clean(self):
        from .catalog import parse_mapping_entries, SOURCE_NAMES
        from django.core.exceptions import ValidationError
        import re
        if not self.tvg_id:
            self.tvg_id = (self.tvg_name or '').strip()
        if self.tvg_id:
            self.tvg_id = self.tvg_id.strip()
            if self.tvg_id == '9999':
                raise ValidationError({'tvg_id': '9999 为占位节目保留 ID，请使用其他值。'})
            if re.search(r'''[\s<>&"']''', self.tvg_id):
                raise ValidationError({'tvg_id': 'tvg-id 不能包含空白、尖括号、引号或 &。'})
            if Channel.objects.exclude(pk=self.pk).filter(tvg_id=self.tvg_id).exists():
                raise ValidationError({'tvg_id': '该 tvg-id 已被其他频道使用，请为同名频道指定不同的 tvg-id。'})
        mapping = parse_mapping_entries(self.channel_id)
        if not mapping:
            raise ValidationError({'channel_id': '请从来源目录选择频道，或填写来源 ID。'})
        if self.source not in SOURCE_NAMES:
            raise ValidationError({'source': '请选择当前支持的主来源。'})
        if not any(source == self.source for source, _ in mapping):
            raise ValidationError({'source': '主来源必须包含在频道来源 ID 中。'})
        from .catalog import mapping_text
        first = next(index for index, (source, _) in enumerate(mapping) if source == self.source)
        self.channel_id = mapping_text([mapping[first], *mapping[:first], *mapping[first+1:]])

    def save(self, *args, **kwargs):
        if not self.pk and not self.sort_order:
            from django.db.models import Max
            self.sort_order = (Channel.objects.aggregate(value=Max('sort_order'))['value'] or 0) + 10
        self.clean()
        super().save(*args, **kwargs)


class XmltvFeed(models.Model):
    name = models.CharField('来源名称', max_length=80)
    url = models.URLField('XML / XML.GZ 地址', max_length=2000)
    enabled = models.BooleanField('启用自动更新', default=True)
    snapshot = models.CharField(max_length=100, blank=True, editable=False)
    last_updated = models.DateTimeField('最近成功更新', null=True, blank=True, editable=False)
    last_error = models.CharField('最近错误', max_length=300, blank=True, editable=False)
    channel_count = models.PositiveIntegerField('频道数', default=0, editable=False)
    programme_count = models.PositiveIntegerField('节目数', default=0, editable=False)

    class Meta:
        verbose_name = 'XMLTV 来源'
        verbose_name_plural = 'XMLTV 来源'

    def __str__(self):
        return self.name

    def clean(self):
        from urllib.parse import urlsplit
        from django.core.exceptions import ValidationError
        parsed = urlsplit(self.url)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname:
            raise ValidationError({'url': '请填写 HTTP 或 HTTPS 文件地址。'})


class Epg(models.Model):
    end_inferred = models.BooleanField(default=False,editable=False)
    channel_id = models.CharField("频道ID", db_index=True, max_length=50)
    starttime = models.DateTimeField("开始时间", db_index=True)
    endtime = models.DateTimeField("结束时间", null=True)
    title = models.CharField("节目名称", max_length=200)
    descr = models.TextField("节目描述", null=True)
    program_date = models.DateField("节目所属于日期", db_index=True)
    crawl_dt = models.DateTimeField("采集时间", auto_now_add=True, null=True)
    source = models.CharField("节目来源", max_length=20, null=True)

    def __str__(self):
        return "%s %s %s" % (
            self.channel_id,
            self.starttime.astimezone(tz=tz_sh),
            self.title,
        )

    def to_dict(self):
        ret = {
            "channel_id": self.channel_id,
            "starttime": self.starttime,
            "endtime": self.endtime,
            "title": self.title,
            "descr": self.descr,
            "program_date": self.program_date,
            "source": self.source,
            'inferred_end':self.end_inferred,
        }
        return ret

    class Meta:
        verbose_name = "节目表"
        verbose_name_plural = "节目表"

    def save(self, *args, **kwargs):
        self.crawl_dt = timezone.now()
        super().save(*args, **kwargs)

    # 获取指定天数的EPG
    def get_epgs(self, channel_ids, need_program_date):
        from .runtime_config import get_config
        first=timezone.localdate()
        last=max(first,need_program_date)
        query=Q(program_date__gte=first,program_date__lte=last)
        if get_config().split_midnight:
            midnight=timezone.make_aware(datetime.datetime.combine(first,datetime.time.min))
            query |= Q(starttime__lt=midnight,endtime__gt=midnight)
            query |= Q(program_date=first-datetime.timedelta(days=1))
        return self.objects.filter(channel_id__in=channel_ids).filter(query).order_by('channel_id','starttime','pk')
    def test(self):
        return self.objects.filter(source="smg")

    def get_single_epg(self, channel, need_date):
        from .runtime_config import get_config
        from .options import options
        from .programmes import project
        from .text_processing import display_text
        config=get_config(); opts=options(config)
        query=Q(program_date=need_date)
        if config.split_midnight and need_date>datetime.date.min:
            midnight=timezone.make_aware(datetime.datetime.combine(need_date,datetime.time.min))
            query |= Q(starttime__lt=midnight,endtime__gt=midnight)
            query |= Q(program_date=need_date-datetime.timedelta(days=1))
        records=self.objects.filter(channel_id=str(channel.pk)).filter(query).order_by('starttime','pk')
        result=[]
        for row in project([epg.to_dict() for epg in records],split=config.split_midnight,date=need_date,fill=config.fill_programme_gaps,channel_ids=[channel.pk]):
            start,end=row['starttime'],row['endtime']
            row.update(end_missing=end is None,end_next_day=bool(end and end.date()>start.date()),
                starttime=start.strftime('%H:%M'),endtime=end.strftime('%H:%M') if end else '23:59',
                title=display_text(row.get('title'),opts)+('' if row.get('gap_filler') else opts['title_suffix']),
                descr=display_text(row.get('descr') or row.get('desc'),opts)+opts['description_suffix'])
            result.append(row)
        return result
    def save_to_dbs(self, ret):
        success = 1
        msg = ""
        querylist = []
        n = 0
        for epg in ret["epgs"]:
            try:
                n += 1
                descr = epg.get("desc") or epg.get("descr") or ""
                querye = Epg(
                    channel_id=epg["channel_id"],
                    starttime=epg["starttime"].astimezone(tz=tz_sh),
                    endtime=(
                        epg["endtime"].astimezone(tz=tz_sh) if epg["endtime"] else None
                    ),
                    title=epg["title"],
                    descr=descr,
                    program_date=epg["program_date"],
                    source=ret["source"],
                    end_inferred=bool(epg.get('inferred_end')),
                )
                querylist.append(querye)
            except Exception as e:
                success = 0
                msg = "web-models-Epg-save_to_dbs %s" % (e)
                continue
        try:
            ret = self.objects.bulk_create(querylist)
        except Exception as e:
            success = 0
            msg = "web-models-Epg-save_to_dbs bulk_create2： %s" % (e)
        return {"success": success, "data": ret, "msg": msg}

    # 删除某一频道的某一时间段的节目表_
    def del_channel_epgs(self, channel_id, program_date, last_program_date):
        if program_date == last_program_date:
            ret = self.objects.filter(
                channel_id=channel_id, program_date=program_date
            ).delete()
        else:
            ret = self.objects.filter(
                channel_id=channel_id,
                program_date__gte=program_date,
                program_date__lte=last_program_date,
            ).delete()
        return ret


class Crawl_log(models.Model):
    LOG_LEVELS = ((1, "信息"), (2, "错误警告"))
    dt = models.DateTimeField("日志时间", auto_now_add=True, db_index=True)
    msg = models.TextField("内容", max_length=1000)
    level = models.IntegerField(
        "日志级别", choices=LOG_LEVELS, default=1
    )  # 1 正常信息 2 错误

    def __str__(self):
        return "%s-%s-%s" % (
            self.dt.strftime("%Y-%m-%d %H:%M"),
            self.msg,
            "警告" if self.level == 2 else "信息",
        )

    def save(self, *args, **kwargs):
        self.msg = self.msg[:900] + "..."
        super().save(*args, **kwargs)

    class Meta:
        verbose_name = "抓取日志"
        verbose_name_plural = "抓取日志"


class Channel_list(models.Model):
    regions = models.JSONField('目录地区（相同结果可能为默认回落）', default=list, blank=True)
    is_alive_choice = ((0, "否"), (1, "是"))
    inner_channel_id = models.IntegerField("内部id", default=99999, db_index=True)
    out_channel_id = models.CharField("来源网站上的id", max_length=300)
    inner_name = models.CharField("内部tvg-name", max_length=100, blank=True)
    out_name = models.CharField("来源网站上的名称", max_length=160)
    normalized_name = models.CharField('合并匹配名称', max_length=160, db_index=True, blank=True)
    logo = models.CharField('台标', max_length=500, blank=True)
    category = models.CharField('分类', max_length=100, blank=True)
    source = models.CharField("来源网站", max_length=20)
    is_alive = models.IntegerField(
        "来源网站上是否仍然存在", choices=is_alive_choice, default=1
    )
    create_date = models.DateField("加入日期", auto_now_add=True)
    update_date = models.DateField("更新日期", default=datetime.date.today)

    # del_date = models.DateField('来源网站取消日期',auto_now_add=True)
    def __str__(self):
        return "%s-%s-%s" % (self.inner_channel_id, self.inner_name, self.out_name)

    class Meta:
        verbose_name = "频道来源整理表"
        verbose_name_plural = "频道来源整理表"
        constraints = [models.UniqueConstraint(fields=['source', 'out_channel_id'], name='catalog_source_id_unique')]

    def save_to_db(self, channels):
        from .catalog import refresh_catalog
        if not channels:
            return {'success': 0, 'msg': '来源返回空列表，保留已有目录。'}
        result = refresh_catalog(channels[0]['source'], channels)
        return {'success': 1, 'msg': str(result)}


class Task(models.Model):
    KINDS = [('backup','完整备份'),('restore','恢复备份'),('cleanup','清理日志与缓存'),('retry','补抓失败项'),('purge', '删除节目单'), ('catalog', '抓取频道目录'), ('epg', '抓取节目单'), ('xml', '重新生成 XML'), ('xmltv', '更新 XMLTV 来源')]
    STATES = [('queued', '排队中'), ('running', '运行中'), ('success', '已完成'),
              ('partial', '部分完成'), ('failed', '失败'), ('cancelled', '已取消')]
    kind = models.CharField('任务', max_length=20, choices=KINDS)
    source = models.CharField('来源', max_length=30, blank=True)
    status = models.CharField('状态', max_length=20, choices=STATES, default='queued', db_index=True)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, verbose_name='提交人')
    params = models.JSONField(default=dict, blank=True)
    details = models.JSONField(default=list, blank=True)
    summary = models.TextField('结果', blank=True)
    cancel_requested = models.BooleanField(default=False)
    created_at = models.DateTimeField('提交时间', auto_now_add=True)
    started_at = models.DateTimeField('开始时间', null=True, blank=True)
    finished_at = models.DateTimeField('结束时间', null=True, blank=True)

    class Meta:
        verbose_name = '后台任务'
        verbose_name_plural = '后台任务'

    def __str__(self):
        return f'#{self.pk} {self.get_kind_display()} · {self.get_status_display()}'


class CrawlFailure(models.Model):
    channel=models.ForeignKey(Channel,on_delete=models.CASCADE)
    program_date=models.DateField(db_index=True)
    reason=models.CharField(max_length=700,blank=True)
    attempts=models.PositiveIntegerField(default=0)
    auto_attempts=models.PositiveIntegerField(default=0)
    resolved=models.BooleanField(default=False,db_index=True)
    next_retry=models.DateTimeField(null=True,blank=True)
    last_failed=models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name='抓取失败项'
        verbose_name_plural='抓取失败项'
        constraints=[models.UniqueConstraint(fields=['channel','program_date'],name='unique_channel_failure_date')]

