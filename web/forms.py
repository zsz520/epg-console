from django import forms
from django.core.exceptions import ValidationError
from .models import Channel, Channel_list, RuntimeConfig
from .catalog import combine, parse_mapping, parse_mapping_entries, mapping_text, SOURCE_NAMES


class RuntimeConfigForm(forms.ModelForm):
    UNITS = [('1', '秒'), ('60', '分钟'), ('3600', '小时'), ('86400', '天')]
    interval_seconds = forms.IntegerField(label='节目单间隔数值', min_value=1, max_value=2592000,
        help_text='按所选单位计算，从上次全量任务完成后等待；最短 60 秒，最长 30 天。')
    epg_interval_unit = forms.ChoiceField(label='节目单间隔单位', choices=UNITS, required=False)
    catalog_interval_seconds = forms.IntegerField(label='频道列表间隔数值', min_value=1, max_value=2592000,
        help_text='从上次全量频道列表任务完成后等待；最短 60 秒，最长 30 天。')
    catalog_interval_unit = forms.ChoiceField(label='频道列表间隔单位', choices=UNITS, required=False)
    xmltv_interval_seconds = forms.IntegerField(label='XMLTV 间隔数值', min_value=1, max_value=2592000,
        help_text='从上次全量 XMLTV 任务完成后等待；最短 60 秒，最长 30 天。')
    xmltv_interval_unit = forms.ChoiceField(label='XMLTV 间隔单位', choices=UNITS, required=False)
    xmltv_schedule_times = forms.CharField(label='XMLTV 每天更新时间', required=False, max_length=1000,
        widget=forms.Textarea(attrs={'rows': 2, 'placeholder': '05:00,17:00'}))
    epg_schedule_times = forms.CharField(label='节目单每天抓取时间', required=False, max_length=1000,
        widget=forms.Textarea(attrs={'rows': 2, 'placeholder': '06:00,12:30,18:00'}))
    catalog_schedule_times = forms.CharField(label='频道列表每天抓取时间', required=False, max_length=1000,
        widget=forms.Textarea(attrs={'rows': 2, 'placeholder': '04:00,16:00'}))
    task_timeout_seconds = forms.IntegerField(label='节目单及其他任务超时（秒）', min_value=10, max_value=86400,
        help_text='默认 3600（60 分钟）；频道目录和独立 XMLTV 更新使用各自的整批任务超时。')
    source_timeout_seconds = forms.IntegerField(label='单个来源目录超时（秒）', min_value=5, max_value=3600,
        help_text='默认 120；对新开始的频道目录任务生效。')

    class Meta:
        model = RuntimeConfig
        fields = ('auto_crawl', 'epg_schedule_mode', 'interval_seconds', 'epg_schedule_times', 'crawl_on_start',
                  'auto_catalog', 'catalog_schedule_mode', 'catalog_interval_seconds', 'catalog_schedule_times', 'catalog_on_start',
                  'auto_xmltv', 'xmltv_schedule_mode', 'xmltv_interval_seconds', 'xmltv_schedule_times', 'xmltv_on_start',
                  'task_timeout_seconds', 'source_timeout_seconds')
        help_texts = {'auto_crawl': '关闭后仍可手动提交任务；正在执行的任务会继续。',
                      'crawl_on_start': '仅间隔模式、尚无自动调度记录时生效；每天定时模式按时间点执行。',
                      'auto_catalog': '按计划更新下方选定范围的来源目录；失败保留原目录，不会自动新增管理频道。新来源首次抓取由独立开关控制。',
                      'auto_xmltv': '独立计划只下载 XML/XML.GZ 来源并更新其频道目录与本地节目快照；节目入库与合并导出仍由节目单任务完成。',
                      'xmltv_on_start': '仅间隔模式首次启用生效；每天定时模式按配置时间执行。',
                      'catalog_on_start': '仅间隔模式首次启用生效；未勾选时先等待一个间隔。'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for prefix, seconds_key in [('epg', 'interval_seconds'), ('catalog', 'catalog_interval_seconds'), ('xmltv', 'xmltv_interval_seconds')]:
            self.fields[prefix + '_schedule_times'].help_text = '北京时间（24 小时制）；多个时间可用逗号、空格或换行分隔，每天最多 48 个。'
            if not self.is_bound:
                seconds = getattr(self.instance, seconds_key)
                unit = next((v for v in (86400, 3600, 60) if seconds % v == 0), 1)
                self.initial[seconds_key] = seconds // unit
                self.initial[prefix + '_interval_unit'] = str(unit)
            else:
                # Accept old clients posting raw seconds; preserve newly added
                # controls when an older partial form does not contain them.
                self.fields[seconds_key].required = seconds_key in self.data
                self.fields[prefix + '_schedule_mode'].required = False

    def clean(self):
        data = super().clean()
        from .scheduling import parse_times
        full = self.data.get('_settings_full') == '1'
        modern = self.data.get('_settings_version') == '2'
        for key in ('auto_catalog', 'catalog_on_start', 'epg_schedule_mode', 'catalog_schedule_mode',
                    'epg_schedule_times', 'catalog_schedule_times', 'auto_xmltv', 'xmltv_on_start',
                    'xmltv_schedule_mode', 'xmltv_schedule_times'):
            boolean = key in ('auto_catalog', 'catalog_on_start', 'auto_xmltv', 'xmltv_on_start')
            preserve = not full or not boolean or (key.startswith(('auto_xmltv', 'xmltv_')) and not modern)
            if key not in self.data and preserve:
                data[key] = getattr(self.instance, key)
        for prefix, seconds_key in [('epg', 'interval_seconds'), ('catalog', 'catalog_interval_seconds'), ('xmltv', 'xmltv_interval_seconds')]:
            value = data.get(seconds_key)
            if seconds_key not in self.data:
                data[seconds_key] = getattr(self.instance, seconds_key)
            elif value is not None:
                seconds = value * int(data.get(prefix + '_interval_unit') or '1')
                if not 60 <= seconds <= 2592000:
                    self.add_error(seconds_key, '间隔须为 60 秒至 30 天。')
                else:
                    data[seconds_key] = seconds
            key = prefix + '_schedule_times'
            try:
                times = parse_times(data.get(key) or '')
                if data.get(prefix + '_schedule_mode') == 'daily' and not times:
                    raise ValueError('每天定时至少需要一个时间点。')
                data[key] = ','.join(times)
            except ValueError as exc:
                self.add_error(key, str(exc))
        return data


class ChannelListForm(forms.ModelForm):
    """Edit classification and the primary mapped provider in the channel table."""
    class Meta:
        model = Channel
        fields = ('name', 'tvg_id', 'tvg_name', 'sort', 'source', 'has_epg', 'ineed', 'recrawl')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # An old page submitted after upgrade must retain its existing switch.
        key = self.add_prefix('recrawl')
        if self.is_bound and key not in self.data:
            self.data = self.data.copy()
            self.data[key] = str(self.instance.recrawl)
        self.fields['sort'] = forms.CharField(label='频道分类', max_length=50,
            widget=GroupInput(attrs={'placeholder': '选择或输入分组'}))
        try:
            mapped = parse_mapping(self.instance.channel_id or '')
        except ValidationError:
            # Older imports may contain incomplete IDs; keep the table accessible
            # so the channel can be opened and its mapping repaired.
            mapped = {self.instance.source: ''} if self.instance.source else {}
        self.mapped_sources = mapped
        choices = [(key, label) for key, label in SOURCE_NAMES.items() if key in mapped]
        self.fields['source'] = forms.ChoiceField(label='节目来源', choices=[('', '自动选择')] + choices, required=False)

    def clean_source(self):
        selected = self.cleaned_data.get('source')
        if selected:
            return selected
        return next((key for key in self.mapped_sources if key in SOURCE_NAMES), '')

    def _post_clean(self):
        # The changelist only saves changed rows. Unchanged legacy records must
        # not block a valid edit to another channel on the same page.
        if self.is_bound and not self.has_changed():
            return
        super()._post_clean()


class GroupInput(forms.TextInput):
    template_name = 'web/widgets/group_input.html'

    def get_context(self, name, value, attrs):
        context = super().get_context(name, value, attrs)
        context['groups'] = Channel.objects.exclude(sort='').order_by('sort').values_list('sort', flat=True).distinct()
        context['group_list_id'] = context['widget']['attrs'].get('id', name) + '-groups'
        context['widget']['attrs']['list'] = context['group_list_id']
        return context


class CatalogPicker(forms.Widget):
    template_name = 'web/widgets/catalog_picker.html'

    def value_from_datadict(self, data, files, name):
        return data.getlist(name) if hasattr(data, 'getlist') else data.get(name, [])

    def get_context(self, name, value, attrs):
        context = super().get_context(name, value, attrs)
        context['selected'] = Channel_list.objects.filter(pk__in=value or [])
        from .options import options
        context.update(options())
        from django.utils import timezone
        import datetime
        today=timezone.localdate()
        context.update(preview_date=today.isoformat(),preview_min=(today-datetime.timedelta(days=7)).isoformat(),preview_max=(today+datetime.timedelta(days=7)).isoformat())
        context['sources'] = SOURCE_NAMES.items()
        from .models import XmltvFeed
        import json
        context['xmltv_feeds_json'] = json.dumps(dict((str(pk), name) for pk, name in XmltvFeed.objects.values_list('pk', 'name')), ensure_ascii=False)
        context['original_mapping'] = self.attrs.get('data-original-mapping', '')
        context['channel_pk'] = self.attrs.get('data-channel-pk', '')
        context['search_name'] = self.attrs.get('data-search-name', '')
        return context

    class Media:
        js = ['console/source-mapping.js', 'console/catalog-picker.js', 'console/source-preview.js']


class ChannelForm(forms.ModelForm):
    sort_order = forms.IntegerField(label='排序', min_value=0, required=False,
        help_text='数值越小越靠前；新频道留空时排在最后。')

    def clean_sort_order(self):
        value = self.cleaned_data.get('sort_order')
        return self.instance.sort_order if value is None else value

    catalog_entries = forms.ModelMultipleChoiceField(
        label='从来源目录选择', queryset=Channel_list.objects.filter(is_alive=True),
        required=False, widget=CatalogPicker,
        help_text='可选择多个来源和不同 XMLTV 文件；按标签顺序抓取。相同脚本来源或同一 XMLTV 文件的不同频道 ID 需确认替换。')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['catalog_entries'].widget.attrs['data-original-mapping'] = self.instance.channel_id or ''
        self.fields['catalog_entries'].widget.attrs['data-channel-pk'] = self.instance.pk or ''
        self.fields['catalog_entries'].widget.attrs['data-search-name'] = (
            self.data.get('tvg_name', '') if self.is_bound else self.instance.tvg_name) or ''
        if not self.instance.pk:
            self.fields['has_epg'].initial = 1
            self.fields['ineed'].initial = 1

    class Meta:
        model = Channel
        fields = '__all__'
        widgets = {'channel_id': forms.Textarea(attrs={'rows': 3, 'cols': 70}),
                   'sort': GroupInput(attrs={'list': 'channel-group-options', 'placeholder': '选择已有分组，或输入新分组', 'autocomplete': 'off'})}

    class Media:
        js = ['console/channel-form.js']

    def clean(self):
        cleaned = super().clean()
        rows = list(cleaned.get('catalog_entries') or [])
        try:
            mapping = combine(parse_mapping_entries(cleaned.get('channel_id', '')), rows)
            cleaned['channel_id'] = mapping_text(mapping)
            if not cleaned.get('source') and mapping:
                cleaned['source'] = mapping[0][0]
        except ValidationError as exc:
            self.add_error('catalog_entries', exc)
        return cleaned


class CatalogImportForm(forms.Form):
    selected = forms.ModelMultipleChoiceField(queryset=Channel_list.objects.filter(is_alive=True), widget=forms.MultipleHiddenInput)
    target = forms.ModelChoiceField(label='合并到已有频道（可选）', queryset=Channel.objects.order_by('name'), required=False,
                                   help_text='留空则按网站频道名称匹配已有频道；没有匹配时新建频道。')
