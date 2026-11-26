from django import forms
from .forms import RuntimeConfigForm
from .options import options
from .models import Channel


# key, label, min/max or text marker, section
SPECS=[
 ('catalog_bootstrap_new_sources','新来源首次自动抓取目录','bool',None,'频道列表自动抓取'),
 ('catalog_retry_failed_on_restart','重启后重试首次目录抓取失败的来源','bool',None,'频道列表自动抓取'),
 ('catalog_refresh_on_restart','每次重启刷新频道目录','bool',None,'频道列表自动抓取'),
 ('catalog_sources','自动刷新来源范围','sources',None,'频道列表自动抓取'),
 ('catalog_retries','单个来源目录最大尝试次数',1,5,'频道列表自动抓取'),
 ('catalog_retry_delay_seconds','目录失败重试等待（秒）',0,60,'频道列表自动抓取'),
 ('catalog_task_timeout_seconds','频道目录整批任务超时（秒）',10,86400,'频道列表自动抓取'),
 ('xmltv_with_epg','抓取节目单时同时更新 XMLTV','bool',None,'XMLTV 来源抓取'),
 ('xmltv_with_catalog','抓取频道目录时同时更新 XMLTV','bool',None,'XMLTV 来源抓取'),
 ('xmltv_task_timeout_seconds','XMLTV 整批更新任务超时（秒）',10,86400,'XMLTV 来源抓取'),
 ('xmltv_source_timeout_seconds','单个 XMLTV 文件更新超时（秒）',5,3600,'XMLTV 来源抓取'),
 ('auto_retry_failures','自动补抓失败项','bool',None,'失败补抓'),
 ('failure_retry_minutes','失败后等待多少分钟补抓',1,1440,'失败补抓'),
 ('failure_retry_rounds','每项最多自动补抓轮数',1,10,'失败补抓'),
 ('quality_check','启用节目完整性保护','bool',None,'节目完整性'),
 ('quality_min_percent','新节目至少为原条数的百分比',1,100,'节目完整性'),
 ('quality_min_old_count','至少有多少条旧节目才进行检查',1,1000,'节目完整性'),
 ('quality_gap_minutes','允许的最长空缺分钟（0 不检查）',0,1440,'节目完整性'),
 ('auto_backup','每天自动完整备份','bool',None,'备份与清理'),
 ('backup_hour','每日备份时间（北京时间整点）',0,23,'备份与清理'),
 ('backup_keep','自动清理时保留多少份备份',1,100,'备份与清理'),
 ('auto_cleanup','每天自动清理旧日志与缓存','bool',None,'备份与清理'),
 ('cleanup_log_days','任务日志和抓取日志保留天数',1,365,'备份与清理'),
 ('cleanup_cache_days','节目预览缓存保留天数',1,30,'备份与清理'),
 ('daily_first_full_refresh','每天首次抓取时重抓全部节目','bool',None,'抓取与历史'),
 ('enable_recrawl','启用已有节目重新获取（总开关）','bool',None,'抓取与历史'),
 ('traditional_to_simplified','节目标题、简介繁体转简体','bool',None,'节目与导出'),
 ('crawl_days','抓取未来天数',1,31,'抓取与历史'),('export_days','导出天数（含今天）',1,31,'抓取与历史'),
 ('recrawl_days','重新抓取天数',1,31,'抓取与历史'),('retries','单来源最大尝试次数',1,10,'抓取与历史'),
 ('retry_delay_seconds','失败重试等待（秒）',0,30,'抓取与历史'),
 ('refresh_existing','每轮重抓前 N 天已存在节目','bool',None,'抓取与历史'),
 ('fetch_previous_day','分割模式下补抓缺失的前一日跨日衔接','bool',None,'抓取与历史'),
 ('fallback','失败后尝试其他已配置来源','bool',None,'抓取与历史'),
 ('enable_retention','启用历史节目自动清理','bool',None,'抓取与历史'),('retain_days','历史节目保留天数',1,3650,'抓取与历史'),
 ('title_suffix','节目标题附加文字','text',200,'节目与导出'),('description_suffix','节目描述附加文字','text',1000,'节目与导出'),
 ('all_filename','总表导出文件名','file',None,'节目与导出'),
 ('cc_categories','央视/卫视导出分组','categories',None,'节目与导出'),('cc_filename','央视/卫视导出文件名','file',None,'节目与导出'),
 ('local_categories','地方频道导出分组','categories',None,'节目与导出'),('local_filename','地方导出文件名','file',None,'节目与导出'),
 ('overseas_categories','港澳台/海外导出分组','categories',None,'节目与导出'),('overseas_filename','港澳台/海外导出文件名','file',None,'节目与导出'),
 ('exclude_categories','XML 排除分组','categories',None,'节目与导出'),('include_channels','排除分组中仍保留的频道名（每行一个）','lines',None,'节目与导出'),
 ('preview_timeout','单次节目预览超时（秒）',5,90,'节目预览'),('preview_cache_seconds','成功预览缓存（秒，0 不缓存）',0,3600,'节目预览'),
 ('preview_error_cache_seconds','失败预览缓存（秒，0 不缓存）',0,300,'节目预览'),('preview_concurrency','服务端最多同时预览数',1,3,'节目预览'),
 ('preview_rows','每个来源最多显示节目数',10,5000,'节目预览'),('preview_previous_day','分割模式预览时补查前一天衔接节目','bool',None,'节目预览'),
 ('xmltv_download_mib','XMLTV 下载上限（MiB）',1,1024,'XMLTV 来源抓取'),('xmltv_expanded_mib','XMLTV 展开上限（MiB）',1,4096,'XMLTV 来源抓取'),
 ('xmltv_max_programmes','XMLTV 节目条数上限',100,10000000,'XMLTV 来源抓取'),('xmltv_download_seconds','XMLTV 文件下载总时限（秒）',5,3600,'XMLTV 来源抓取'),
 ('xmltv_connect_seconds','XMLTV 连接超时（秒）',1,120,'XMLTV 来源抓取'),('xmltv_read_seconds','XMLTV 读取超时（秒）',1,300,'XMLTV 来源抓取'),
 ('xmltv_snapshot_keep','每个文件保留的成功快照数',1,10,'XMLTV 来源抓取'),('xmltv_snapshot_grace_seconds','旧快照清理等待（秒）',60,86400,'XMLTV 来源抓取'),
 ('site_name','网站名称','text',80,'网页与后台'),('admin_name','后台名称','text',80,'网页与后台'),
 ('public_base_url','站点公开地址（可留空）','url',2000,'站点侧栏与接口'),
 ('diyp_default_channel','DIYP 默认频道','channel',100,'站点侧栏与接口'),
 ('public_page_size','首页每页频道数（0 显示全部）',0,500,'网页与后台'),
 ('public_show_downloads','显示节目单下载卡片','bool',None,'站点侧栏与接口'),
 ('public_download_title','下载卡片标题','text',100,'站点侧栏与接口'),
 ('public_xml_label','XML 下载名称','text',100,'站点侧栏与接口'),
 ('public_gzip_label','XML.GZ 下载名称','text',100,'站点侧栏与接口'),
 ('public_download_note','下载卡片说明','paragraph',1000,'站点侧栏与接口'),
 ('public_show_help','显示接口与使用说明','bool',None,'站点侧栏与接口'),
 ('public_help_title','接口说明标题','text',100,'站点侧栏与接口'),
 ('public_help_open','默认展开接口说明','bool',None,'站点侧栏与接口'),
 ('public_help_text','接口使用说明文案','paragraph',2000,'站点侧栏与接口'),
 ('public_show_stats','显示数据概况卡片','bool',None,'站点侧栏与接口'),
 ('public_stats_title','数据概况标题','text',100,'站点侧栏与接口'),
 ('public_show_channel_count','显示频道总数','bool',None,'站点侧栏与接口'),
 ('public_channel_label','频道总数名称','text',100,'站点侧栏与接口'),
 ('public_show_enabled_count','显示已启用频道数','bool',None,'站点侧栏与接口'),
 ('public_enabled_label','已启用频道数名称','text',100,'站点侧栏与接口'),
 ('public_show_programme_count','显示节目记录数','bool',None,'站点侧栏与接口'),
 ('public_programme_label','节目记录数名称','text',100,'站点侧栏与接口'),
 ('public_stats_note','数据概况说明文案','paragraph',1000,'站点侧栏与接口'),
 ('list_per_page','后台频道每页条数',10,500,'网页与后台'),('catalog_search_limit','每次目录搜索返回条数',10,200,'网页与后台'),
 ('task_log_bytes','任务页面读取日志末尾字节数',1000,200000,'网页与后台'),
]


class CategoryPicker(forms.CheckboxSelectMultiple):
    template_name='web/widgets/category_picker.html'

    def __init__(self,attrs=None,choices=(),group_label='分组'):
        self.group_label=group_label
        super().__init__(attrs,choices)

    def get_context(self,name,value,attrs):
        context=super().get_context(name,value,attrs)
        context['group_label']=self.group_label
        return context


class DefaultChannelInput(forms.TextInput):
    template_name='web/widgets/default_channel_input.html'

    def get_context(self,name,value,attrs):
        context=super().get_context(name,value,attrs)
        list_id=context['widget']['attrs'].get('id',name)+'-channels'
        context['widget']['attrs']['list']=list_id
        context['channel_list_id']=list_id
        context['channel_choices']=[{'value':identity or tvg_name,'label':label,'tvg_name':tvg_name}
            for identity,tvg_name,label in Channel.objects.order_by('name','pk').values_list('tvg_id','tvg_name','name')]
        return context


class SettingsForm(RuntimeConfigForm):
    class Meta(RuntimeConfigForm.Meta):
        fields=RuntimeConfigForm.Meta.fields+('split_midnight','fill_programme_gaps')

    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.opts=options(self.instance)
        groups=set(Channel.objects.values_list('sort',flat=True))
        for key,_,kind,_,_ in SPECS:
            if kind=='categories': groups.update(self.opts[key])
        for key,label,kind,maximum,_ in SPECS:
            if kind=='bool': field=forms.BooleanField(label=label,required=False)
            elif kind=='channel': field=forms.CharField(label=label,required=False,max_length=maximum,widget=DefaultChannelInput())
            elif kind=='url': field=forms.URLField(label=label,required=False,max_length=maximum,assume_scheme='https')
            elif kind=='sources':
                from crawl.source_registry import get_source_names
                names={key:value for key,value in get_source_names().items() if key!='xmltv'}
                for source in self.opts[key]: names.setdefault(source,source+'（未加载）')
                field=forms.MultipleChoiceField(label=label,choices=list(names.items()),required=False,widget=CategoryPicker(group_label='来源'))
            elif kind=='categories': field=forms.MultipleChoiceField(label=label,choices=[(v,v) for v in sorted(groups) if v],required=False,widget=CategoryPicker(group_label=label))
            elif kind in ('text','file','lines','paragraph'):
                field=forms.CharField(label=label,required=False,max_length=maximum or 10000,
                    widget=forms.Textarea(attrs={'rows':3}) if kind in ('lines','paragraph') else forms.TextInput())
            else: field=forms.IntegerField(label=label,required=False,min_value=kind,max_value=maximum)
            field.initial='\n'.join(self.opts[key]) if kind=='lines' else self.opts[key]
            self.fields[key]=field
        self.fields['recrawl_days'].help_text='配合“每轮重抓”或原重新抓取模式使用；不超过抓取天数。'
        self.fields['daily_first_full_refresh'].help_text='默认关闭。开启后，每天首轮整批抓取刷新所有启用频道在抓取天数范围内的已有节目；仅该轮独立覆盖总重抓开关和频道当天重抓开关。停用频道不抓取。轮次完成后当天不再全量触发，重启不重置；手动选中频道和预览不计入首轮。'
        self.fields['daily_first_full_refresh'].help_text+=f' 最近完成日期：{self.instance.last_daily_full_refresh or "尚未执行"}。'
        self.fields['enable_recrawl'].help_text='常规抓取必须与频道自己的开关同时开启；总开关不会覆盖关闭的频道。关闭时常规整批任务只补缺失日期。每日首次全量重抓是独立开关；手动“抓取选中频道”仍可执行。'
        self.fields['refresh_existing'].help_text='仅总开关和频道自己的开关都开启时，扩展到前 N 天已有节目。'
        self.fields['traditional_to_simplified'].help_text='作用于网页、预览和导出；新抓取保留原文。历史上已转换入库的文字需重新抓取才能恢复繁体。不会改动频道名称、tvg-id 或来源 ID。'
        self.fields['retries'].help_text='包含首次请求；主来源和每个备用来源统一使用此次数。'
        self.fields['retry_delay_seconds'].help_text='同一来源连续尝试之间等待；默认 1 秒，0 表示立即重试。'
        self.fields['retain_days'].help_text='默认不清理；只有启用上方自动清理开关才删除过期节目。'
        self.fields['xmltv_download_seconds'].help_text='仅限制下载阶段；单个 XMLTV 来源的下载、解压与索引整体受下方单文件超时限制。'
        self.fields['catalog_bootstrap_new_sources'].help_text='默认开启。重启后发现 data/sources 中没有目录的新来源，自动排队抓取；独立于定时总开关和来源范围，不会自动新增实际频道。'
        self.fields['catalog_retry_failed_on_restart'].help_text='配合新来源首次抓取；开启后，首次抓取失败的来源下次重启再尝试，已有目录的来源不重复抓取。'
        self.fields['catalog_refresh_on_restart'].help_text='需要开启自动抓取频道列表。每次重启刷新选定范围；已有目录抓取任务排队或执行时，不重复提交整批任务。'
        self.fields['catalog_sources'].help_text='定时与每次重启刷新使用此范围；不选表示所有脚本来源。新来源首次抓取、手动指定来源不受此范围限制；XMLTV 由独立跟随开关控制。'
        self.fields['catalog_retries'].help_text='含首次尝试；空目录、超时或执行失败时按此次数重试，失败保留原目录。'
        self.fields['source_timeout_seconds'].help_text='仅限制单个脚本来源每次目录抓取；XMLTV 使用独立超时。'
        self.fields['xmltv_with_epg'].help_text='保留旧版默认行为。关闭后节目单任务读取已有 XMLTV 快照；请通过独立计划或手动更新保持快照最新。'
        self.fields['xmltv_with_catalog'].help_text='只对整批频道目录任务生效；手动指定 XMLTV 更新仍可执行。'
        self.fields['xmltv_source_timeout_seconds'].help_text='含下载、解压和索引；与脚本来源目录超时分开。失败保留最近成功快照。'
        self.fields['public_base_url'].help_text='留空按当前访问的协议、域名和端口显示完整地址。反向代理需固定外网地址时填写 http(s)://域名:端口，不含路径。'
        self.fields['diyp_default_channel'].help_text='可选择已有频道，或填写 tvg-name / tvg-id；缺少 ch 时使用，默认 CCTV1。缺少 date 时使用北京时间当天，不会触发远程抓取。'
        self.fields['public_show_channel_count'].help_text='后台已配置的频道总数，包含停用频道；不随前台分类或搜索变化。'
        self.fields['public_page_size'].help_text='默认每页 100 个，减少大量频道时的页面加载；分页保留分类、搜索和后台排序。0 表示不分页。'
        self.fields['public_show_enabled_count'].help_text='当前前台已启用、可展示的频道数，不随分类或搜索变化。'
        self.fields['all_filename'].help_text='默认 all.xml；同时生成同名 .gz 文件。保存后自动提交 XML 生成任务，首页下载地址随之更新；/epg/all.xml 和 /epg/all.xml.gz 仍可使用。'
        self.fields['split_midnight'].help_text='所有来源统一处理；次日 00:00 至首档开始使用前一日末档名称，避免重叠。缺失时段以“精彩继续”补齐，日尾 23:59:59。原始数据保留。'
        self.fields['auto_retry_failures'].help_text='只补抓今天及配置抓取范围内的失败频道/日期；不抓过去日期，不改变来源 ID 顺序。正常整批抓取仍独立执行。保存后对新到期项生效。'
        self.fields['failure_retry_rounds'].help_text='补抓轮数不含原始整批抓取；每轮内部仍使用单来源尝试次数。成功后自动清除失败状态。'
        self.fields['quality_check'].help_text='默认关闭。检查有足够旧节目的日期，数量骤减或空缺过长时保留旧节目并记录失败项。判断在入库前执行，原始节目参与比较。'
        self.fields['auto_backup'].help_text='包含数据库、账号、设置、自定义来源、XMLTV 快照和导出文件。通过后台串行任务执行；保存后按下一个时间点执行。'
        self.fields['auto_cleanup'].help_text='默认关闭。每日清理已完成任务的旧日志、过期预览缓存和超出保留份数的备份；不删频道、节目、自定义来源、XMLTV 快照。可先到维护中心预览。'
        advanced={'catalog_retry_delay_seconds','retry_delay_seconds','quality_min_percent','quality_min_old_count','quality_gap_minutes','backup_hour','backup_keep','cleanup_log_days','cleanup_cache_days','xmltv_download_mib','xmltv_expanded_mib','xmltv_max_programmes','xmltv_download_seconds','xmltv_connect_seconds','xmltv_read_seconds','xmltv_snapshot_keep','xmltv_snapshot_grace_seconds','task_log_bytes','preview_error_cache_seconds','preview_rows','preview_concurrency'}
        for key,field in self.fields.items():field.is_advanced=key in advanced

    def clean(self):
        data=super().clean()
        full=self.data.get('_settings_full')=='1'
        modern=self.data.get('_settings_version') in ('2','3')
        public_modern=self.data.get('_settings_version')=='3'
        if not full and 'split_midnight' not in self.data:
            data['split_midnight']=self.instance.split_midnight
        if not full and 'fill_programme_gaps' not in self.data:
            data['fill_programme_gaps']=self.instance.fill_programme_gaps
        for key,_,kind,_,_ in SPECS:
            new_option=key.startswith('catalog_') or key in ('xmltv_with_epg','xmltv_with_catalog')
            preserve_new=(new_option and not modern) or (key.startswith('public_') and not public_modern)
            if key not in self.data and (not full or kind not in ('bool','categories','lines','sources') or preserve_new):
                data[key]=self.opts[key]
            if isinstance(kind,int) and data.get(key) is None:
                data[key]=self.opts[key]
            if kind=='lines' and isinstance(data.get(key),str):
                data[key]=[line.strip() for line in data[key].splitlines() if line.strip()]
        for key in ('site_name','admin_name'):
            if not data.get(key): self.add_error(key,'名称不能为空。')
        if not data.get('diyp_default_channel'):
            self.add_error('diyp_default_channel','默认频道不能为空。')
        if data.get('public_base_url'):
            from .site_context import public_base
            try:
                data['public_base_url']=public_base(data['public_base_url'])
            except ValueError:
                self.add_error('public_base_url','填写 http(s)://域名:端口，不包含账号、路径、查询参数或片段。')
        if data.get('export_days',1)>data.get('crawl_days',31): self.add_error('export_days','导出天数不能超过抓取天数。')
        if data.get('recrawl_days',1)>data.get('crawl_days',31): self.add_error('recrawl_days','重新抓取天数不能超过抓取天数。')
        if data.get('xmltv_expanded_mib',4096)<data.get('xmltv_download_mib',1): self.add_error('xmltv_expanded_mib','展开上限不能小于下载上限。')
        if data.get('source_timeout_seconds',0)>data.get('catalog_task_timeout_seconds',86400): self.add_error('source_timeout_seconds','单来源目录超时不能超过频道目录整批任务超时。')
        if data.get('xmltv_source_timeout_seconds',0)>data.get('xmltv_task_timeout_seconds',86400): self.add_error('xmltv_source_timeout_seconds','单文件超时不能超过 XMLTV 整批任务超时。')
        import re
        files=[]
        for key in ('all_filename','cc_filename','local_filename','overseas_filename'):
            value=data.get(key,'')
            if not re.fullmatch(r'[A-Za-z0-9_-]+\.xml',value): self.add_error(key,'使用不含路径的英文、数字、下划线或横线文件名，扩展名 .xml。')
            files.append(value.casefold())
            if key!='all_filename' and value.casefold()=='all.xml': self.add_error(key,'all.xml 保留为总表兼容地址，请使用其他名称。')
        if len(set(files))!=4: self.add_error(None,'导出文件名不能重复（不区分大小写）。')
        return data

    def save(self,commit=True):
        instance=super().save(commit=False)
        instance.options={key:self.cleaned_data[key] for key,*_ in SPECS}
        if commit: instance.save()
        return instance

    def sections(self):
        sections=[('节目单自动抓取', ('auto_crawl','epg_schedule_mode','interval_seconds','epg_interval_unit','epg_schedule_times','crawl_on_start')),
                  ('频道列表自动抓取', ('auto_catalog','catalog_schedule_mode','catalog_interval_seconds','catalog_interval_unit','catalog_schedule_times','catalog_on_start','source_timeout_seconds')),
                  ('XMLTV 来源抓取', ('auto_xmltv','xmltv_schedule_mode','xmltv_interval_seconds','xmltv_interval_unit','xmltv_schedule_times','xmltv_on_start')),
                  ('任务超时与统一处理', ('task_timeout_seconds','split_midnight','fill_programme_gaps'))]
        result=[]
        for title,fields in sections:
            names=list(fields)+[key for key,_,_,_,group in SPECS if group==title]
            result.append({'title':title,'fields':[self[key] for key in names]})
        for section in dict.fromkeys(spec[-1] for spec in SPECS):
            if section in {title for title,_ in sections}: continue
            result.append({'title':section,'fields':[self[key] for key,_,_,_,group in SPECS if group==section]})
        return result
