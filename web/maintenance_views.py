import datetime
import re
import sqlite3
import zipfile
from pathlib import Path
from django.conf import settings
from django.contrib import messages
from django.core import signing
from django.core.exceptions import PermissionDenied
from django.http import FileResponse,Http404
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.utils import timezone
from .models import CrawlFailure
from .tasks import enqueue


def center(site,request):
    site.require(request,'web.view_runtimeconfig')
    from .maintenance import candidates
    from .backups import stage_restore,confirm_restore
    from .failures import eligible
    from .options import options
    context={**site.each_context(request),'title':'维护中心'}
    if request.method=='POST':
        action=request.POST.get('action')
        try:
            if action=='retry':
                site.require(request,'web.change_channel','web.add_task')
                ids=request.POST.getlist('failures')
                rows=eligible() if request.POST.get('all')=='1' else eligible().filter(pk__in=[int(value) for value in ids])
                selected=list(rows.values_list('pk',flat=True)[:10000])
                if not selected:raise ValueError('没有可补抓的选中项；过去日期和停用频道不参与。')
                task,_=enqueue('retry',user=request.user,params={'failure_ids':selected})
                return redirect('admin:operation-detail',pk=task.pk)
            if not request.user.is_superuser:raise PermissionDenied
            if action=='backup':
                task,_=enqueue('backup',user=request.user)
                return redirect('admin:operation-detail',pk=task.pk)
            if action=='restore-preview':
                upload=request.FILES.get('archive')
                if not upload:raise ValueError('请选择本程序导出的完整备份 ZIP。')
                summary,token=stage_restore(upload,request.user)
                context.update(restore_plan=summary,restore_token=token)
            elif action=='restore-apply':
                if request.POST.get('confirmed')!='on':raise ValueError('请确认恢复范围。')
                params=confirm_restore(request.POST.get('token',''),request.user)
                task,_=enqueue('restore',user=request.user,params=params)
                messages.success(request,'恢复任务已提交；单容器将自动重启，之后使用备份中的账号重新登录。')
                return redirect('admin:operation-detail',pk=task.pk)
            elif action=='cleanup-preview':
                plan=candidates();cutoff=(timezone.now()-datetime.timedelta(days=options()['cleanup_log_days'])).isoformat()
                token=signing.dumps({'plan':plan,'cutoff':cutoff,'user':request.user.pk},salt='epg-cleanup',compress=True)
                context.update(cleanup_plan=plan[:100],cleanup_count=len(plan),cleanup_size=sum(p['size'] for p in plan),cleanup_token=token)
            elif action=='cleanup-apply':
                if request.POST.get('confirmed')!='on':raise ValueError('请确认清理范围。')
                plan=signing.loads(request.POST.get('token',''),salt='epg-cleanup',max_age=900)
                if plan['user']!=request.user.pk:raise ValueError('清理确认无效。')
                task,_=enqueue('cleanup',user=request.user,params={'plan':plan['plan'],'cutoff':plan['cutoff']})
                return redirect('admin:operation-detail',pk=task.pk)
        except (ValueError,signing.BadSignature,zipfile.BadZipFile,sqlite3.Error,OSError,KeyError,RuntimeError) as exc:
            context['error']=str(exc)[:500] or '操作失败，请重新预览。'
    backups=[]
    if request.user.is_superuser:
        directory=Path(settings.DATA_DIR)/'backups'
        for p in sorted(directory.glob('epg-full-*.zip'),key=lambda p:p.stat().st_mtime,reverse=True)[:100]:
            if not p.is_symlink() and p.is_file():backups.append({'name':p.name,'size':p.stat().st_size})
    context.update(backups=backups,failures=eligible().select_related('channel').order_by('-last_failed')[:200],
        failure_count=eligible().count(),expired_count=CrawlFailure.objects.filter(resolved=False,program_date__lt=timezone.localdate()).count(),
        restore_failed=(Path(settings.DATA_DIR)/'restore-failed.json').exists())
    return TemplateResponse(request,'console/maintenance.html',context)


def download(request,name):
    if not request.user.is_superuser:raise PermissionDenied
    if not re.fullmatch(r'epg-full-\d{8}-\d{6}-[a-f0-9]{8}\.zip',name):raise Http404
    root=(Path(settings.DATA_DIR)/'backups').resolve();p=root/name
    if p.is_symlink() or not p.is_file() or p.resolve().parent!=root:raise Http404
    response=FileResponse(p.open('rb'),as_attachment=True,filename=name)
    response['Cache-Control']='no-store'
    return response
