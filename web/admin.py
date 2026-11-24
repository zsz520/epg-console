from django.contrib import admin, messages
from django.db.models import Count
from django.urls import reverse
from django.utils.html import format_html
from .models import Channel, Epg, Crawl_log, Channel_list, Task, XmltvFeed
from .console import console_site
from .forms import ChannelForm, ChannelListForm
from .tasks import enqueue
from .ordering import CHANNEL_ORDERING
from django.contrib.auth.models import User, Group
from django.contrib.auth.admin import UserAdmin, GroupAdmin


@admin.register(Channel, site=console_site)
class ChannelAdmin(admin.ModelAdmin):
    form = ChannelForm
    change_form_template = 'admin/web/channel/change_form.html'
    list_display = ('display_number', 'name', 'tvg_id', 'tvg_name', 'sort', 'source', 'source_count', 'has_epg', 'ineed', 'recrawl', 'last_program_date', 'drag_handle', 'quick_edit')
    list_display_links = ('display_number',)
    list_editable = ('name', 'tvg_id', 'tvg_name', 'sort', 'source', 'has_epg', 'ineed', 'recrawl')
    change_list_template = 'admin/web/channel/change_list.html'
    list_filter = ('ineed', 'has_epg', 'source', 'sort')
    search_fields = ('name', 'tvg_id', 'tvg_name', 'channel_id')
    list_per_page = 50
    ordering = CHANNEL_ORDERING
    save_on_top = True
    fieldsets = (
        ('频道信息', {'fields': ('name', 'tvg_id', 'tvg_name', 'sort', 'sort_order', 'logo')}),
        ('来源与匹配', {'fields': ('catalog_entries', 'source', 'channel_id'),
                   'description': '先从目录选择来源频道，再填写名称和匹配名。主来源留空时自动选用第一个来源。'}),
        ('抓取设置', {'fields': ('has_epg', 'ineed', 'recrawl', 'last_program_date')}),
        ('补充信息', {'fields': ('descr', 'remark', 'patten'), 'classes': ('collapse',)}),
    )
    actions = ('crawl_selected', 'enable_selected', 'disable_selected')

    def get_list_per_page(self):
        from .options import options
        return options()['list_per_page']

    @admin.display(description='序号', ordering='sort_order')
    def display_number(self, obj):
        return format_html('<span class="channel-number">{}</span>', getattr(obj, '_list_number', '—'))

    @admin.display(description='拖动')
    def drag_handle(self, obj):
        return format_html('<button type="button" class="channel-drag-handle" data-channel-id="{}" data-group="{}" aria-label="拖动排序：{}" title="按住拖动调整组内顺序，也可用上下方向键">⠿</button>', obj.pk, obj.sort, obj.name)

    def changelist_view(self, request, extra_context=None):
        response = super().changelist_view(request, extra_context)
        if hasattr(response, 'context_data') and 'cl' in response.context_data:
            from .channel_order import token
            cl = response.context_data['cl']
            offset = 0 if cl.show_all else (cl.page_num - 1) * cl.list_per_page
            for number, obj in enumerate(cl.result_list, offset + 1):
                obj._list_number = number
            ids = [c.pk for c in cl.result_list]
            response.context_data['channel_order'] = {'ids': ids, 'token': token(request.user, ids), 'offset': offset}
            response.context_data['can_reorder'] = self.has_change_permission(request) and not request.GET.get('o')
            params = request.GET.copy()
            params.pop('o', None)
            response.context_data['manual_order_url'] = '?' + params.urlencode()
        return response

    @admin.display(description='快捷操作')
    def quick_edit(self, obj):
        return format_html('<button type="button" class="channel-quick-edit" data-quick-url="{}" data-channel-name="{}">编辑 / 补全</button>',
            reverse('admin:quick-channel', args=[obj.pk]), obj.name)

    def channel_navigation(self, request, object_id):
        from copy import copy
        from urllib.parse import urlencode
        from django.http import QueryDict
        from django.contrib.admin.options import IncorrectLookupParameters
        filters = request.GET.get('_changelist_filters', '')
        list_request = copy(request)
        list_request.method = 'GET'
        list_request.GET = QueryDict(filters)
        navigation = {'previous': None, 'next': None, 'position': None, 'total': 0}
        try:
            changelist = self.get_changelist_instance(list_request)
            rows = list(changelist.queryset.values_list('pk', 'name'))
        except IncorrectLookupParameters:
            return navigation
        navigation['total'] = len(rows)
        position = next((i for i, row in enumerate(rows) if str(row[0]) == str(object_id)), None)
        if position is None:
            return navigation
        navigation['position'] = position + 1
        for direction, index in [('previous', position - 1), ('next', position + 1)]:
            if 0 <= index < len(rows):
                pk, name = rows[index]
                url = reverse('admin:web_channel_change', args=[pk])
                if filters:
                    url += '?' + urlencode({'_changelist_filters': filters})
                navigation[direction] = {'url': url, 'name': name}
        return navigation

    def change_view(self, request, object_id, form_url='', extra_context=None):
        navigation = self.channel_navigation(request, object_id)
        # Capture neighbours before a save can change the current filter/order.
        request._channel_navigation = navigation
        return super().change_view(request, object_id, form_url,
            {**(extra_context or {}), 'channel_navigation': navigation})

    def response_change(self, request, obj):
        response = super().response_change(request, obj)
        direction = request.POST.get('_channel_direction')
        navigation = getattr(request, '_channel_navigation', {})
        neighbour = navigation.get(direction) if direction in ('previous', 'next') else None
        if neighbour:
            from django.http import HttpResponseRedirect
            return HttpResponseRedirect(neighbour['url'])
        return response

    def get_changelist_form(self, request, **kwargs):
        return ChannelListForm

    def get_queryset(self, request):
        from .categories import filter_category
        return filter_category(super().get_queryset(request), request.GET.get('category', ''))

    def lookup_allowed(self, lookup, value, request=None):
        return lookup == 'category' or super().lookup_allowed(lookup, value, request)

    def get_changelist(self, request, **kwargs):
        from django.contrib.admin.views.main import ChangeList
        class CategoryChangeList(ChangeList):
            def __init__(self,*args,**kwargs):
                # list_per_page is passed positionally by Django ModelAdmin.
                values=list(args)
                values[8]=self_admin.get_list_per_page()
                super().__init__(*values,**kwargs)
            def get_filters_params(self, params=None):
                values = super().get_filters_params(params)
                values.pop('category', None)
                return values
        self_admin=self
        return CategoryChangeList

    @admin.display(description='来源数')
    def source_count(self, obj):
        return obj.channel_id.count('<')

    @admin.action(description='抓取选中频道的节目单', permissions=['change'])
    def crawl_selected(self, request, queryset):
        if not request.user.has_perm('web.add_task'):
            self.message_user(request, '没有提交任务的权限。', messages.ERROR)
            return
        task, _ = enqueue('epg', user=request.user, params={'channel_ids': list(queryset.values_list('pk', flat=True))})
        self.message_user(request, format_html('已提交任务 <a href="{}">#{}</a>。', reverse('admin:operation-detail', args=[task.pk]), task.pk))

    @admin.action(description='启用选中频道', permissions=['change'])
    def enable_selected(self, request, queryset):
        queryset.update(ineed=1, has_epg=1)

    @admin.action(description='停用选中频道', permissions=['change'])
    def disable_selected(self, request, queryset):
        queryset.update(ineed=0)

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        if form.cleaned_data.get('catalog_entries') is not None:
            form.cleaned_data['catalog_entries'].update(inner_channel_id=form.instance.pk, inner_name=form.instance.tvg_name)

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        exported_fields = {'name', 'tvg_id', 'tvg_name', 'sort', 'logo', 'has_epg', 'ineed', 'source', 'channel_id'}
        if (not change or exported_fields.intersection(form.changed_data)) and request.user.has_perm('web.add_task'):
            from django.db import transaction
            from .quick_edit import revision
            transaction.on_commit(lambda: enqueue('xml', user=request.user, params={'channel_id': obj.pk, 'revision': revision(obj)}))
            self.message_user(request, '频道已保存，XML 更新任务已加入队列。')

    def _queue_deleted_channels_export(self, request):
        if request.user.has_perm('web.add_task'):
            from django.db import transaction
            transaction.on_commit(lambda: enqueue('xml', user=request.user))

    def delete_model(self, request, obj):
        super().delete_model(request, obj)
        self._queue_deleted_channels_export(request)

    def delete_queryset(self, request, queryset):
        super().delete_queryset(request, queryset)
        self._queue_deleted_channels_export(request)


@admin.register(XmltvFeed, site=console_site)
class XmltvFeedAdmin(admin.ModelAdmin):
    list_display = ('name', 'enabled', 'channel_count', 'programme_count', 'last_updated', 'last_error')
    list_filter = ('enabled',)
    search_fields = ('name',)
    readonly_fields = ('channel_count', 'programme_count', 'last_updated', 'last_error')
    fields = ('name', 'url', 'enabled', 'channel_count', 'programme_count', 'last_updated', 'last_error')
    actions = ('refresh_selected',)

    def delete_model(self, request, obj):
        Channel_list.objects.filter(source='xmltv', out_channel_id__startswith=f'{obj.pk}:').update(is_alive=False)
        super().delete_model(request, obj)

    def delete_queryset(self, request, queryset):
        for pk in queryset.values_list('pk', flat=True):
            Channel_list.objects.filter(source='xmltv', out_channel_id__startswith=f'{pk}:').update(is_alive=False)
        super().delete_queryset(request, queryset)

    def save_model(self, request, obj, form, change):
        from django.db import transaction
        super().save_model(request, obj, form, change)
        if not obj.enabled:
            Channel_list.objects.filter(source='xmltv', out_channel_id__startswith=f'{obj.pk}:').update(is_alive=False)
        elif request.user.has_perm('web.add_task'):
            transaction.on_commit(lambda: enqueue('xmltv', user=request.user, params={'feed_ids': [obj.pk]}))
            self.message_user(request, '来源已保存，更新文件与频道目录任务已加入队列；完成后在频道编辑中选择 XMLTV 文件来源。')

    @admin.action(description='更新选中 XMLTV 文件与频道目录', permissions=['change'])
    def refresh_selected(self, request, queryset):
        if not request.user.has_perm('web.add_task'):
            self.message_user(request, '没有提交任务的权限。', messages.ERROR)
            return
        ids = list(queryset.filter(enabled=True).values_list('pk', flat=True))
        if not ids:
            self.message_user(request, '请选择已启用的 XMLTV 来源。', messages.WARNING)
            return
        task, _ = enqueue('xmltv', user=request.user, params={'feed_ids': ids})
        self.message_user(request, format_html('已提交 XMLTV 更新任务 <a href="{}">#{}</a>。', reverse('admin:operation-detail', args=[task.pk]), task.pk))


@admin.register(Channel_list, site=console_site)
class ChannelListAdmin(admin.ModelAdmin):
    list_display = ('out_name', 'source', 'out_channel_id', 'category', 'is_alive', 'inner_name', 'update_date')
    list_filter = ('source', 'is_alive', 'category')
    search_fields = ('out_name', 'out_channel_id', 'normalized_name')
    list_per_page = 50
    readonly_fields = ('normalized_name','regions')


@admin.register(Epg, site=console_site)
class EpgAdmin(admin.ModelAdmin):
    change_list_template='admin/web/epg/change_list.html'
    list_display = ('channel_id', 'title', 'program_date', 'starttime', 'endtime', 'source')
    list_filter = ('source', 'program_date')
    date_hierarchy = 'program_date'
    search_fields = ('title', 'channel_id')
    list_per_page = 50

    def get_list_display(self,request):
        from .options import options
        from .text_processing import display_text
        opts=options()
        @admin.display(description='节目名称',ordering='title')
        def program_title(obj):
            return display_text(obj.title,opts)
        return ('channel_id',program_title,'program_date','starttime','endtime','source')


@admin.register(Crawl_log, site=console_site)
class CrawlLogAdmin(admin.ModelAdmin):
    list_display = ('dt', 'level', 'msg')
    list_filter = ('level',)
    search_fields = ('msg',)
    ordering = ('-pk',)


@admin.register(Task, site=console_site)
class TaskAdmin(admin.ModelAdmin):
    list_display = ('id', 'kind', 'source', 'status', 'summary', 'created_at', 'finished_at')
    list_filter = ('kind', 'status')
    ordering = ('-pk',)
    readonly_fields = [f.name for f in Task._meta.fields]


    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


console_site.register(User, UserAdmin)
console_site.register(Group, GroupAdmin)

