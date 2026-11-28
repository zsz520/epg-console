import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from django import forms
from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import JsonResponse,HttpResponseNotAllowed
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.contrib import messages
from crawl.source_registry import discover
from .models import SourceEnvironment
from .source_environment import parse_proxies,parse_extra,mask_proxy,source_targets


class EnvironmentForm(forms.Form):
    proxies=forms.CharField(label='代理列表（按行排序）',required=False,max_length=16000,strip=False,
        widget=forms.Textarea(attrs={'rows':6,'spellcheck':'false','autocomplete':'off'}))
    extra_env=forms.CharField(label='其他环境变量（KEY=value）',required=False,max_length=16000,strip=False,
        widget=forms.Textarea(attrs={'rows':6,'spellcheck':'false','autocomplete':'off'}))
    revision=forms.CharField(required=False,widget=forms.HiddenInput)

    def clean_proxies(self):
        try:return '\n'.join(parse_proxies(self.cleaned_data['proxies']))
        except ValueError as exc:raise forms.ValidationError(str(exc)) from None

    def clean_extra_env(self):
        try:return parse_extra(self.cleaned_data['extra_env'])
        except ValueError as exc:raise forms.ValidationError(str(exc)) from None


def staff(request):
    if not request.user.is_authenticated or not request.user.is_active or not (request.user.is_staff or request.user.is_superuser):
        raise PermissionDenied


def limited(source,action):
    directory=Path(settings.DATA_DIR)/'source-environment';directory.mkdir(mode=0o700,exist_ok=True)
    path=directory/(source+'-'+action+'.rate')
    with path.open('a+') as stream:
        os.chmod(path,0o600)
        fcntl.flock(stream,fcntl.LOCK_EX)
        stream.seek(0)
        try:times=json.load(stream)
        except (ValueError,TypeError):times=[]
        now=time.time()
        times=[stamp for stamp in times if isinstance(stamp,(int,float)) and now-60<stamp<=now]
        if len(times)>=10:return True
        times.append(now);stream.seek(0);stream.truncate();json.dump(times,stream)
    return False


def container_proxies(source):
    prefix=source.upper()
    keys=[prefix+'_PROXIES',prefix+'_PROXY']
    if source=='hnyst':keys+=['HN0734_PROXIES','HN0734_PROXY','HNYD_PROXY']
    keys+=['ALL_PROXY','HTTPS_PROXY','HTTP_PROXY','all_proxy','https_proxy','http_proxy']
    for key in keys:
        raw=(os.getenv(key) or '').strip()
        if raw:
            import re
            return [p if '://' in p else 'socks5h://'+p for p in re.split(r'[,;\s]+',raw) if p],key
    return [],''


def response_private(response):
    response['Cache-Control']='no-store'
    response['Referrer-Policy']='no-referrer'
    return response


def center(site,request,source_id=None):
    staff(request)
    sources,errors=discover()
    if source_id and source_id not in sources:
        return response_private(JsonResponse({'error':'来源未注册，请检查来源文件。'},status=404))
    if not source_id:
        if request.method!='GET':return HttpResponseNotAllowed(['GET'])
        saved={row.source_id:row for row in SourceEnvironment.objects.all()}
        rows=[]
        for key,source in sources.items():
            row=saved.get(key)
            configured=row.proxies.splitlines() if row and row.proxies else []
            inherited,variable=container_proxies(key)
            rows.append({'id':key,'name':source.name,
                'proxies':[mask_proxy(p) for p in configured or inherited],
                'origin':'后台' if configured else ('容器 · '+variable if inherited else '未配置'),
                'updated_at':row.updated_at if row else None,
                'updated_by':row.updated_by if row else None,
                'extra_origin':'后台' if row and row.extra_env else '容器 / 来源默认值'})
        return response_private(TemplateResponse(request,'console/source_environments.html',{
            **site.each_context(request),'title':'来源环境','rows':rows,'source_errors':errors}))
    source=sources[source_id]
    saved=SourceEnvironment.objects.filter(source_id=source_id).first()
    initial={'proxies':saved.proxies if saved else '',
             'extra_env':'\n'.join(k+'='+v for k,v in saved.extra_env.items()) if saved else '',
             'revision':saved.updated_at.isoformat() if saved else ''}
    if request.method not in ('GET','POST'):return HttpResponseNotAllowed(['GET','POST'])
    if request.method=='POST' and limited(source_id,'save'):
        return response_private(JsonResponse({'error':'本来源每分钟最多保存或提交 10 次，请稍后重试。'},status=429))
    form=EnvironmentForm(request.POST if request.method=='POST' else None,initial=initial)
    if request.method=='POST' and form.is_valid():
        with transaction.atomic():
            current=SourceEnvironment.objects.select_for_update().filter(source_id=source_id).first()
            actual=current.updated_at.isoformat() if current else ''
            if actual!=form.cleaned_data['revision']:
                form.add_error(None,'配置已被其他页面更新，请刷新后重新编辑。')
            else:
                SourceEnvironment.objects.update_or_create(source_id=source_id,defaults={
                    'proxies':form.cleaned_data['proxies'],'extra_env':form.cleaned_data['extra_env'],
                    'updated_by':request.user})
        if not form.errors:
            if request.POST.get('action')=='crawl':
                from .tasks import enqueue
                kind='xmltv' if source_id=='xmltv' else 'catalog'
                task,_=enqueue(kind,source=source_id,user=request.user,params={'environment_crawl':True})
                messages.success(request,f'配置已保存，任务 #{task.pk} 已提交；目录完成后抓取已配置频道的节目。')
            else:messages.success(request,'配置已保存；下一次来源调用使用新配置，无需重启。')
            return redirect('admin:source-environment-edit',source_id=source_id)
    inherited,variable=container_proxies(source_id)
    return response_private(TemplateResponse(request,'console/source_environment_edit.html',{
        **site.each_context(request),'title':'编辑来源环境','source':source,'form':form,
        'inherited':[mask_proxy(p) for p in inherited],'inherited_variable':variable,
        'health_targets':source_targets(source),
        'test_proxies':[{'index':i,'label':mask_proxy(p)} for i,p in enumerate((saved.proxies.splitlines() if saved and saved.proxies else inherited))]}))


def test(site,request,source_id):
    staff(request)
    if request.method!='POST':return HttpResponseNotAllowed(['POST'])
    sources,_=discover()
    if source_id not in sources:return JsonResponse({'error':'来源未注册。'},status=404)
    if limited(source_id,'test'):return JsonResponse({'error':'每来源每分钟最多测试 10 次。'},status=429)
    try:
        data=json.loads(request.body)
        index=data.get('index')
        if index is not None and (type(index) is not int or index<0 or index>=20):
            raise ValueError
    except (ValueError,TypeError,AttributeError):
        return JsonResponse({'error':'测试参数无效。'},status=400)
    try:
        process=subprocess.run([sys.executable,'-m','web.source_environment_test_runner',source_id],
            input=json.dumps({'index':index}),capture_output=True,text=True,timeout=55)
        result=json.loads(process.stdout) if process.returncode==0 else {'error':'测试进程异常，请检查来源和网络。'}
    except (subprocess.TimeoutExpired,ValueError):result={'error':'测试超时，代理未在限定时间内响应。'}
    return response_private(JsonResponse(result))
