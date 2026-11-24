from django.contrib import messages
from django.core import signing
from django.http import Http404
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.db import IntegrityError,OperationalError,transaction
from .tasks import enqueue


def transfer(site,request):
    site.require(request,'web.view_channel','web.add_channel','web.change_channel')
    from .channel_transfer import ImportForm,parse_file,stage,apply
    form=ImportForm(request.POST or None,request.FILES or None)
    context={**site.each_context(request),'title':'频道导入 / 导出','form':form}
    if request.method=='POST':
        try:
            if request.POST.get('action')=='apply':
                if request.POST.get('confirmed')!='on':raise ValueError('请先确认导入预览。')
                with transaction.atomic():
                    created,updated=apply(request.POST.get('token',''),request.user)
                    if request.user.has_perm('web.add_task'):enqueue('xml',user=request.user,params={'channel_import':True})
                messages.success(request,f'导入完成：新增 {created} 个，更新 {updated} 个；未删除任何频道。')
                return redirect('admin:web_channel_changelist')
            if form.is_valid():
                rows,token=stage(parse_file(form.cleaned_data['file']),form.cleaned_data['mode'],request.user)
                context.update(rows=rows[:100],total=len(rows),created=sum(row['pk'] is None for row in rows),
                               updated=sum(row['pk'] is not None for row in rows),token=token)
        except (ValueError,signing.BadSignature) as exc:context['error']=str(exc)
        except (IntegrityError,OperationalError):context['error']='数据库正在处理其他修改或存在导入冲突，请重新上传预览。'
    return TemplateResponse(request,'console/channel_transfer.html',context)


def export(site,request,kind):
    site.require(request,'web.view_channel')
    from .channel_transfer import export_response
    try:return export_response(kind)
    except ValueError:raise Http404


def purge(site,request):
    site.require(request,'web.view_epg','web.delete_epg','web.add_task')
    from .programme_management import PurgeForm,preview,confirm
    form=PurgeForm(request.POST or None)
    context={**site.each_context(request),'title':'删除节目单','form':form}
    if request.method=='POST':
        try:
            if request.POST.get('action')=='apply':
                if request.POST.get('confirmed')!='on':raise ValueError('请先勾选确认删除范围。')
                params=confirm(request.POST.get('token',''),request.user)
                if not params['count']:raise ValueError('所选范围没有节目，无需删除。')
                task,_=enqueue('purge',user=request.user,params=params)
                messages.success(request,f'删除任务 #{task.pk} 已排队。按预览范围删除，之后更新导出文件。')
                return redirect('admin:operation-detail',pk=task.pk)
            if form.is_valid():
                params,token=preview(form.cleaned_data,request.user)
                context.update(plan=params,token=token)
        except (ValueError,signing.BadSignature) as exc:context['error']=str(exc)
    return TemplateResponse(request,'console/programme_purge.html',context)
