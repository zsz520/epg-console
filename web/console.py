import datetime
import json
from pathlib import Path
from django.conf import settings
from django.contrib import messages
from django.contrib.admin import AdminSite
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Count, Q
from django.http import JsonResponse, HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import path
from django.utils import timezone
from .catalog import SOURCE_NAMES, import_catalog, name_key
from crawl.source_registry import registry as source_registry
from .models import Channel, Channel_list, Epg, Task
from .tasks import enqueue


class ConsoleSite(AdminSite):
    site_header = 'EPG 控制台'
    site_title = 'EPG 控制台'
    index_title = '总览'
    index_template = 'console/dashboard.html'

    def get_app_list(self, request, app_label=None):
        apps = super().get_app_list(request, app_label)
        if request.user.is_active and request.user.is_staff and not any(a['app_label']=='web' for a in apps):
            apps.append({'name':'EPG 管理','app_label':'web','app_url':'/admin/','has_module_perms':True,'models':[]})
        for app in apps:
            if app['app_label'] == 'web' and request.user.has_perm('web.view_task'):
                from django.urls import reverse
                position = next((i + 1 for i, model in enumerate(app['models']) if model['object_name'] == 'Task'), 0)
                app['models'].insert(position, {'name': '任务中心', 'object_name': 'TaskWorkspace',
                    'perms': {'view': True}, 'admin_url': reverse('admin:operations'),
                    'add_url': None, 'view_only': True})
            if app['app_label'] == 'web':
                app['models'].insert(3,{'name':'来源环境','object_name':'SourceEnvironmentWorkspace','perms':{'view':True},
                    'admin_url':'/admin/source-environments/','add_url':None,'view_only':True})
                if request.user.has_perm('web.view_runtimeconfig'):
                    app['models'].insert(2,{'name':'设置中心','object_name':'SettingsWorkspace','perms':{'view':True},
                        'admin_url':'/admin/settings/','add_url':None,'view_only':True})
                    app['models'].insert(3,{'name':'维护中心','object_name':'MaintenanceWorkspace','perms':{'view':True},
                        'admin_url':'/admin/maintenance/','add_url':None,'view_only':True})
                app['models'].sort(key=lambda model: model['object_name'] == 'XmltvFeed')
        return [app for app in apps if app['models']]

    def get_urls(self):
        from django.views.decorators.csrf import csrf_protect
        return [
            path('source-environments/',csrf_protect(self.source_environment),name='source-environments'),
            path('source-environments/<str:source_id>/',csrf_protect(self.source_environment),name='source-environment-edit'),
            path('source-environments/<str:source_id>/test/',csrf_protect(self.source_environment_test),name='source-environment-test'),
            path('maintenance/',self.admin_view(self.maintenance),name='maintenance'),
            path('maintenance/backups/<str:name>/',self.admin_view(self.backup_download),name='backup-download'),
            path('channels/transfer/',self.admin_view(self.channel_transfer),name='channel-transfer'),
            path('channels/export/<str:kind>/',self.admin_view(self.channel_export),name='channel-export'),
            path('programmes/purge/',self.admin_view(self.programme_purge),name='programme-purge'),
            path('operations/', self.admin_view(self.operations), name='operations'),
            path('settings/', self.admin_view(self.settings_view), name='settings-center'),
            path('operations/start/', self.admin_view(self.start), name='operation-start'),
            path('operations/<int:pk>/', self.admin_view(self.task_detail), name='operation-detail'),
            path('operations/<int:pk>/cancel/', self.admin_view(self.cancel), name='operation-cancel'),
            path('catalog/', self.admin_view(self.catalog), name='catalog'),
            path('catalog/search/', self.admin_view(self.catalog_search), name='catalog-search'),
            path('sources/preview/', self.admin_view(self.source_preview), name='source-preview'),
            path('channels/<int:pk>/quick/', self.admin_view(self.quick_channel), name='quick-channel'),
            path('channels/reorder/', self.admin_view(self.reorder_channels), name='channel-reorder'),
        ] + super().get_urls()

    def source_environment(self,request,source_id=None):
        from .source_environment_views import center
        return center(self,request,source_id)

    def source_environment_test(self,request,source_id):
        from .source_environment_views import test
        return test(self,request,source_id)

    def require(self, request, *permissions):
        if not request.user.has_perms(permissions):
            raise PermissionDenied

    def maintenance(self,request):
        from .maintenance_views import center
        return center(self,request)

    def backup_download(self,request,name):
        from .maintenance_views import download
        return download(request,name)

    def channel_transfer(self,request):
        from .data_views import transfer
        return transfer(self,request)

    def channel_export(self,request,kind):
        from .data_views import export
        return export(self,request,kind)

    def programme_purge(self,request):
        from .data_views import purge
        return purge(self,request)

    def each_context(self, request):
        from .options import options
        opts=options()
        return {**super().each_context(request),'site_header':opts['admin_name'],'site_title':opts['admin_name']}

    def settings_view(self, request):
        self.require(request,'web.view_runtimeconfig')
        from .settings_form import SettingsForm
        from .runtime_config import get_config
        from .scheduling import summaries
        from .options import options
        config=get_config()
        previous=options(config)
        previous_projection=(config.split_midnight,config.fill_programme_gaps)
        form=SettingsForm(request.POST if request.method=='POST' else None,instance=config)
        if request.method=='POST':
            self.require(request,'web.change_runtimeconfig')
            if form.is_valid():
                saved=form.save()
                current=options(saved)
                export_keys={'all_filename','cc_filename','local_filename','overseas_filename','cc_categories','local_categories','overseas_categories',
                    'exclude_categories','include_channels','export_days','title_suffix','description_suffix','traditional_to_simplified',
                    'export_display_name','export_include_logos','export_include_placeholder'}
                unordered={'cc_categories','local_categories','overseas_categories','exclude_categories','include_channels'}
                changed=previous_projection!=(saved.split_midnight,saved.fill_programme_gaps) or any(
                    set(previous[key])!=set(current[key]) if key in unordered else previous[key]!=current[key] for key in export_keys)
                message='设置已保存；网页立即应用，新任务使用最新参数。'
                if changed:
                    if request.user.has_perm('web.add_task'):
                        task,_=enqueue('xml',user=request.user,params={'settings_update':timezone.now().isoformat()})
                        message+=f' XML 更新任务 #{task.pk} 已提交。'
                    else:
                        message+=' 导出设置已改变，请由有任务权限的管理员重新生成 XML。'
                messages.success(request,message)
                return redirect('admin:settings-center')
        return TemplateResponse(request,'console/settings.html',{**self.each_context(request),'title':'设置中心',
            'settings_form':form,'settings_sections':form.sections(),'schedule_summaries':summaries()})

    def reorder_channels(self, request):
        if request.method != 'POST':
            return HttpResponseNotAllowed(['POST'])
        self.require(request, 'web.change_channel')
        import json
        from django.core.signing import BadSignature
        from django.db import OperationalError
        from .channel_order import reorder
        try:
            data = json.loads(request.body)
            signed = reorder(request.user, data['token'], data['ids'])
        except (ValueError, KeyError, TypeError, BadSignature) as exc:
            message = str(exc) if isinstance(exc, ValueError) and not isinstance(exc, json.JSONDecodeError) else '排序凭证无效或已过期，请刷新页面。'
            return JsonResponse({'error': message}, status=409)
        except OperationalError:
            return JsonResponse({'error':'数据库正在处理其他修改，请稍后重试。'}, status=409)
        return JsonResponse({'token': signed, 'message':'频道顺序已保存。'})

    def index(self, request, extra_context=None):
        self.require(request, 'web.view_channel')
        heartbeat = Path(settings.DATA_DIR) / 'worker-heartbeat'
        worker_alive = heartbeat.exists() and timezone.now().timestamp() - heartbeat.stat().st_mtime < 30
        today = timezone.localdate()
        context = {
            'channel_count': Channel.objects.count(),
            'enabled_count': Channel.objects.filter(ineed=1, has_epg=1).count(),
            'catalog_count': Channel_list.objects.filter(is_alive=True).count(),
            'programme_count': Epg.objects.filter(program_date__gte=today).count(),
            'tasks': Task.objects.order_by('-pk')[:6], 'worker_alive': worker_alive,
            'sources': SOURCE_NAMES.items(),
        }
        return super().index(request, extra_context={**context, **(extra_context or {})})

    def operations(self, request):
        self.require(request, 'web.view_task')
        from .forms import RuntimeConfigForm
        from .runtime_config import get_config
        config = get_config()
        form = RuntimeConfigForm(request.POST if request.method == 'POST' else None, instance=config)
        if request.method == 'POST':
            self.require(request, 'web.change_runtimeconfig')
            if form.is_valid():
                form.save()
                messages.success(request, '任务设置已保存，无需重启容器；超时设置对新开始的任务生效。')
                return redirect('admin:operations')
        return TemplateResponse(request, 'console/operations.html', {
            **self.each_context(request), 'title': '任务中心', 'opts': Task._meta, 'tasks': Task.objects.order_by('-pk')[:100],
            'sources': SOURCE_NAMES.items(),
            'settings_form': form,
            'source_errors': source_registry()[1],
        })

    def start(self, request):
        if request.method != 'POST':
            return HttpResponseNotAllowed(['POST'])
        kind = request.POST.get('kind')
        if kind not in ('catalog', 'epg', 'xml', 'xmltv'):
            return JsonResponse({'error': '无效的任务类型'}, status=400)
        self.require(request, 'web.add_task', 'web.change_xmltvfeed' if kind == 'xmltv' else ('web.change_channel_list' if kind == 'catalog' else 'web.change_channel'))
        source = request.POST.get('source', '') if kind == 'catalog' else ''
        if source and source not in SOURCE_NAMES:
            return JsonResponse({'error': '无效的来源'}, status=400)
        params = {}
        if kind == 'catalog' and (request.POST.get('source_picker') == '1' or 'sources' in request.POST):
            selected = request.POST.getlist('sources')
            if len(selected) != len(set(selected)) or any(key not in SOURCE_NAMES for key in selected):
                return JsonResponse({'error': '来源选择无效，请刷新页面后重试。'}, status=400)
            order = request.POST.get('source_order', '')
            if order:
                try:
                    ordered = json.loads(order)
                    if not isinstance(ordered, list) or any(not isinstance(key, str) for key in ordered) or len(ordered) != len(selected) or set(ordered) != set(selected):
                        raise ValueError
                    selected = ordered
                except (ValueError, TypeError):
                    return JsonResponse({'error': '来源顺序无效，请重新选择来源。'}, status=400)
            source = ''
            params = {'sources': selected} if selected else {'all_sources': True}
        task, created = enqueue(kind, source, request.user, params=params)
        messages.success(request, f'任务 #{task.pk} 已加入队列。' if created else f'同类任务 #{task.pk} 已在队列或运行中。')
        return redirect('admin:operation-detail', pk=task.pk)

    def task_detail(self, request, pk):
        self.require(request, 'web.view_task')
        task = get_object_or_404(Task, pk=pk)
        from .options import options
        log_bytes=options()['task_log_bytes']
        log = ''
        try:
            with open(Path(settings.DATA_DIR) / 'tasks' / f'{task.pk}.log', 'rb') as stream:
                stream.seek(0, 2)
                stream.seek(max(0, stream.tell() - log_bytes))
                log = stream.read().decode('utf-8', errors='replace')
        except FileNotFoundError:
            log = '等待工作进程执行…' if task.status=='queued' else ('任务运行中，暂未写入日志。' if task.status=='running' else '任务已结束；日志文件不存在或已按保留策略清理。')
        except OSError:
            log = '无法读取任务日志，请检查 data/tasks 的目录权限或磁盘状态。'
        if request.GET.get('format') == 'json':
            response=JsonResponse({'status': task.status, 'label': task.get_status_display(), 'summary': task.summary, 'log': log, 'details': task.details})
            response['Cache-Control']='no-store'
            return response
        return TemplateResponse(request, 'console/task_detail.html', {
            **self.each_context(request), 'title': f'任务 #{task.pk}', 'task': task, 'log': log, 'log_kib':(log_bytes+1023)//1024,
            'task_poll_seconds':options()['task_poll_seconds'],
        })

    def cancel(self, request, pk):
        if request.method != 'POST':
            return HttpResponseNotAllowed(['POST'])
        self.require(request, 'web.change_task')
        from django.db import transaction
        with transaction.atomic():
            task=get_object_or_404(Task,pk=pk)
            now=timezone.now()
            cancelled=Task.objects.filter(pk=pk, status='queued').update(status='cancelled', finished_at=now, summary='已取消')
            if cancelled:
                from .scheduling import completed
                completed(task,now)
            Task.objects.filter(pk=pk, status='running').update(cancel_requested=True)
        return redirect('admin:operation-detail', pk=pk)

    def catalog_search(self, request):
        from .options import options
        self.require(request, 'web.view_channel_list')
        query = request.GET.get('q', '').strip()[:100]
        rows = Channel_list.objects.filter(is_alive=True)
        if query:
            from .catalog import search_catalog
            rows = search_catalog(rows,query)
        if request.GET.get('source'):
            rows = rows.filter(source=request.GET['source'])
        from .xmltv import source_name
        return JsonResponse({'results': [
            {'id': r.pk, 'name': r.out_name, 'source': r.source, 'source_name': source_name(r.out_channel_id) if r.source == 'xmltv' else SOURCE_NAMES.get(r.source, r.source),
             'identifier': r.out_channel_id, 'category': r.category, 'logo': r.logo}
            for r in rows.order_by('out_name', 'source')[:options()['catalog_search_limit']]
        ]})

    def source_preview(self, request):
        if request.method != 'POST':
            return HttpResponseNotAllowed(['POST'])
        self.require(request, 'web.view_channel', 'web.view_channel_list')
        import json
        from .catalog import parse_mapping_entries
        from .preview import preview, resolve_date
        try:
            data = json.loads(request.body)
            if not isinstance(data, dict):
                raise ValueError
            resolve_date(data.get('date'))
            if data.get('custom_id') is not None:
                self.require(request, 'web.change_channel')
                import re
                source, identifier = data.get('source'), data['custom_id']
                if not isinstance(identifier, str) or not identifier.strip() or len(identifier) > 285 or re.search(r'[<>\r\n]', identifier):
                    raise ValueError
                identifier = identifier.strip()
                name = str(data.get('name') or '自定义频道')[:100]
            elif data.get('catalog_id'):
                row = get_object_or_404(Channel_list, pk=int(data['catalog_id']), is_alive=True)
                source, identifier, name = row.source, row.out_channel_id, row.out_name
            else:
                channel = get_object_or_404(Channel, pk=int(data['channel_id']))
                source = data.get('source')
                matches = [value for key, value in parse_mapping_entries(channel.channel_id) if key == source]
                identifier = data.get('identifier') or (matches[0] if matches else None)
                name = channel.name
                if not identifier or identifier not in matches:
                    raise ValueError
            if source not in SOURCE_NAMES:
                raise ValueError
        except (ValueError, TypeError, KeyError, ValidationError):
            return JsonResponse({'error': '请从已配置来源或来源目录中选择有效频道。'}, status=400)
        kwargs={'date':data['date']} if data.get('date') is not None else {}
        result = preview(source, identifier, name, force=data.get('force') is True, **kwargs)
        return JsonResponse({**result, 'source': source, 'source_name': SOURCE_NAMES[source], 'identifier': identifier})

    def quick_channel(self, request, pk):
        self.require(request, 'web.change_channel')
        from django.db import transaction, OperationalError
        from .quick_edit import QuickChannelForm, revision, FIELDS
        saved, status = False, 200
        channel = get_object_or_404(Channel, pk=pk)
        current_revision = revision(channel)
        form = QuickChannelForm(instance=channel)
        if request.method == 'POST':
            try:
                with transaction.atomic():
                    channel = get_object_or_404(Channel.objects.select_for_update(), pk=pk)
                    current_revision = revision(channel)
                    if request.POST.get('_revision') != current_revision:
                        form = QuickChannelForm({key: getattr(channel, key) for key in FIELDS}, instance=channel)
                        form.is_valid()
                        form.add_error(None, '频道已在其他页面修改，已载入最新值，请重新编辑后保存。')
                        status = 409
                    else:
                        form = QuickChannelForm(request.POST, instance=channel)
                        if form.is_valid():
                            channel = form.save()
                            form.cleaned_data['catalog_entries'].update(inner_channel_id=channel.pk, inner_name=channel.tvg_name)
                            if form.has_changed() and request.user.has_perm('web.add_task'):
                                transaction.on_commit(lambda: enqueue('xml', user=request.user,
                                    params={'channel_id': channel.pk, 'revision': revision(channel)}))
                            saved = True
            except OperationalError:
                form = QuickChannelForm(request.POST, instance=channel)
                form.is_valid()
                form.add_error(None, '数据库正在处理其他修改，请稍后重试。')
                status = 409
        elif request.method != 'GET':
            return HttpResponseNotAllowed(['GET', 'POST'])
        response = TemplateResponse(request, 'console/quick_channel.html', {'form': form, 'channel': channel,
            'revision': current_revision, 'saved': saved,
            'can_delete': request.user.has_perm('web.delete_channel')}, status=status)
        response['X-Frame-Options'] = 'SAMEORIGIN'
        return response

    def catalog(self, request):
        self.require(request, 'web.view_channel_list')
        from django.core.paginator import Paginator
        from .forms import CatalogImportForm
        query = request.GET.get('q', '').strip()[:100]
        source = request.GET.get('source', '')
        rows = Channel_list.objects.filter(is_alive=True)
        if query:
            from .catalog import search_catalog
            rows = search_catalog(rows,query)
        if source:
            rows = rows.filter(source=source)
        error = ''
        if request.method == 'POST':
            self.require(request, 'web.add_channel', 'web.change_channel')
            form = CatalogImportForm(request.POST)
            if form.is_valid():
                selected = list(form.cleaned_data['selected'])
                if len(selected) > 500:
                    error = '一次最多选择 500 个条目。'
                else:
                    try:
                        result = import_catalog([r.pk for r in selected], form.cleaned_data['target'].pk if form.cleaned_data['target'] else None)
                        messages.success(request, f"已新建 {result['created']} 个频道，合并更新 {result['updated']} 个频道。")
                        return redirect('admin:web_channel_changelist')
                    except ValidationError as exc:
                        error = '；'.join(exc.messages)
            else:
                error = '请选择有效的来源频道和目标频道。'
        page = Paginator(rows.order_by('normalized_name', 'source'), 100).get_page(request.GET.get('page'))
        return TemplateResponse(request, 'console/catalog.html', {
            **self.each_context(request), 'title': '来源频道目录', 'page': page, 'query': query,
            'source': source, 'sources': SOURCE_NAMES.items(), 'error': error,
            'channels': Channel.objects.order_by('name'),
            'selected_ids': set(request.POST.getlist('selected')),
        })


console_site = ConsoleSite(name='admin')
