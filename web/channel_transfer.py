"""Portable channel configuration only: strict preview, atomic merge, no deletion."""
import csv
import hashlib
import io
import json
import secrets
import time
from pathlib import Path
from django import forms
from django.conf import settings
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import HttpResponse
from .models import Channel
from .ordering import ordered_channels
from .catalog import parse_mapping

FIELDS=('tvg_id','name','tvg_name','sort','sort_order','logo','source','channel_id','has_epg','ineed','recrawl','descr','remark','patten')
HEADERS=('id','original_tvg_id')+FIELDS+('_escaped',)
MAX_BYTES=5*1024*1024
MAX_ROWS=10000


class ImportForm(forms.Form):
    file=forms.FileField(label='频道文件（JSON / CSV，最多 5 MiB）')
    mode=forms.ChoiceField(label='导入方式',choices=[('merge','按 tvg-id 合并：存在则更新，不存在则新增（迁移推荐）'),
                       ('local','按本机 ID 更新：用于修改 tvg-id，仅更新已有频道')])


def export_response(kind):
    rows=[{'id':row.pk,'original_tvg_id':row.tvg_id,**{key:getattr(row,key) for key in FIELDS}}
          for row in ordered_channels(Channel.objects.all())]
    if kind=='json':
        data=json.dumps({'format':'epg-console-channels','version':1,'channels':rows},ensure_ascii=False,indent=2)
        response=HttpResponse(data,content_type='application/json; charset=utf-8')
    elif kind=='csv':
        buffer=io.StringIO(newline='');writer=csv.DictWriter(buffer,fieldnames=HEADERS);writer.writeheader()
        for row in rows:
            escaped=[]
            for key,value in row.items():
                if isinstance(value,str) and value.lstrip().startswith(('=','+','-','@')):
                    row[key]="'"+value;escaped.append(key)
            row['_escaped']=json.dumps(escaped);writer.writerow(row)
        response=HttpResponse('\ufeff'+buffer.getvalue(),content_type='text/csv; charset=utf-8')
    else:raise ValueError('仅支持 JSON 或 CSV。')
    response['Content-Disposition']=f'attachment; filename="channels.{kind}"'
    response['Cache-Control']='no-store'
    return response


def parse_file(upload):
    if upload.size>MAX_BYTES:raise ValueError('文件超过 5 MiB。')
    try:text=upload.read(MAX_BYTES+1).decode('utf-8-sig')
    except UnicodeDecodeError:raise ValueError('请保存为 UTF-8 JSON 或 CSV 文件。') from None
    if len(text.encode('utf-8'))>MAX_BYTES:raise ValueError('文件超过 5 MiB。')
    try:
        if upload.name.lower().endswith('.json'):
            document=json.loads(text)
            if not isinstance(document,dict) or document.get('format')!='epg-console-channels' or document.get('version')!=1:
                raise ValueError('JSON 不是受支持的频道备份格式。')
            rows=document.get('channels')
        elif upload.name.lower().endswith('.csv'):
            reader=csv.DictReader(io.StringIO(text,newline=''))
            if not reader.fieldnames or 'tvg_id' not in reader.fieldnames or len(set(reader.fieldnames))!=len(reader.fieldnames):
                raise ValueError('CSV 需要 tvg_id 列，且不能有重名列。')
            if set(reader.fieldnames)-set(HEADERS):raise ValueError('CSV 含未知列，请使用本系统导出的模板。')
            rows=[]
            for index,row in enumerate(reader):
                if index>=MAX_ROWS:raise ValueError('一次最多导入 10000 个频道。')
                if None in row or any(value is None for value in row.values()):raise ValueError('CSV 列数不匹配。')
                escaped=json.loads(row.pop('_escaped','') or '[]')
                if not isinstance(escaped,list) or any(key not in HEADERS for key in escaped):raise ValueError('CSV 转义标记无效。')
                for key in escaped:
                    if row.get(key,'').startswith("'"):row[key]=row[key][1:]
                rows.append(row)
        else:raise ValueError('请选择 .json 或 .csv 文件。')
    except (json.JSONDecodeError,csv.Error):raise ValueError('文件格式无效，请检查 JSON 或 CSV 内容。') from None
    if not isinstance(rows,list) or not rows or len(rows)>MAX_ROWS:raise ValueError('频道列表为空或超过 10000 条。')
    return rows


def revision():
    rows=list(Channel.objects.order_by('pk').values('pk',*FIELDS))
    return hashlib.sha256(json.dumps(rows,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def validate_rows(rows,mode):
    if mode not in ('merge','local'):raise ValueError('导入方式无效。')
    result=[];seen=set();targets=set()
    for index,raw in enumerate(rows,1):
        try:
            if not isinstance(raw,dict) or set(raw)-set(HEADERS):raise ValueError('包含未知字段。')
            identity=str(raw.get('tvg_id') or raw.get('tvg_name') or '').strip()
            if not identity or identity in seen:raise ValueError('tvg-id 为空或在文件中重复。')
            seen.add(identity)
            if mode=='local':
                pk=int(raw.get('id') or 0)
                current=Channel.objects.filter(pk=pk).first()
                if not current or raw.get('original_tvg_id')!=current.tvg_id:
                    raise ValueError('本机 ID 或 original_tvg_id 不匹配；迁移请使用按 tvg-id 合并。')
            else:current=Channel.objects.filter(tvg_id=identity).first()
            channel=Channel.objects.get(pk=current.pk) if current else Channel()
            for key in FIELDS:
                if key in raw:
                    value=raw[key]
                    if key in ('has_epg','ineed','recrawl','sort_order'):
                        value=int(value)
                        if value<0 or (key!='sort_order' and value not in (0,1)):raise ValueError(f'{key} 数值无效。')
                    elif value is not None and not isinstance(value,str):raise ValueError(f'{key} 应为文本。')
                    setattr(channel,key,value)
            channel.tvg_id=identity
            channel.name=channel.name or channel.tvg_name
            channel.tvg_name=channel.tvg_name or channel.name
            channel.sort=channel.sort or '其他'
            mapping=parse_mapping(channel.channel_id)
            if not mapping or any(not value for value in mapping.values()):raise ValueError('请填写有效来源 ID。')
            channel.source=channel.source or next(iter(mapping))
            from crawl.source_registry import get_source_names
            if channel.source not in get_source_names():
                raise ValueError(f'主来源 {channel.source} 未安装或已移除；请先放入对应来源文件并重启，或在导入文件中改选有效来源。')
            channel.full_clean()
            if current and current.pk in targets:raise ValueError('同一目标频道在文件中出现多次。')
            if current:targets.add(current.pk)
            result.append({'pk':current.pk if current else None,'fields':{key:getattr(channel,key) for key in FIELDS}})
        except (ValueError,TypeError,ValidationError) as exc:
            raise ValueError(f'第 {index} 行：{exc}') from None
    return result


def stage(rows,mode,user):
    with transaction.atomic():
        current_revision=revision()
        checked=validate_rows(rows,mode)
    directory=Path(settings.DATA_DIR)/'channel-imports';directory.mkdir(parents=True,exist_ok=True)
    for stale in directory.glob('*.json'):
        if len(stale.stem)==32 and all(c in '0123456789abcdef' for c in stale.stem):
            try:
                if stale.stat().st_mtime<time.time()-3600:stale.unlink(missing_ok=True)
            except OSError:pass
    name=secrets.token_hex(16)
    payload={'rows':rows,'mode':mode,'revision':current_revision}
    data=json.dumps(payload,ensure_ascii=False).encode()
    (directory/(name+'.json')).write_bytes(data)
    token=signing.dumps({'name':name,'user':user.pk,'digest':hashlib.sha256(data).hexdigest()},salt='channel-import')
    return checked,token


def apply(token,user):
    signed=signing.loads(token,salt='channel-import',max_age=1800)
    name=signed.get('name','')
    if signed.get('user')!=user.pk or len(name)!=32 or any(c not in '0123456789abcdef' for c in name):raise ValueError('导入确认无效。')
    path=Path(settings.DATA_DIR)/'channel-imports'/(name+'.json')
    try:data=path.read_bytes()
    except OSError:raise ValueError('导入预览已失效，请重新上传。') from None
    if hashlib.sha256(data).hexdigest()!=signed['digest']:raise ValueError('导入文件已变化，请重新上传。')
    payload=json.loads(data)
    with transaction.atomic():
        list(Channel.objects.select_for_update().values_list('pk',flat=True))
        if revision()!=payload['revision']:raise ValueError('频道列表已发生变化，请重新上传预览。')
        rows=validate_rows(payload['rows'],payload['mode'])
        created=updated=0
        for row in rows:
            channel=Channel.objects.get(pk=row['pk']) if row['pk'] else Channel()
            for key,value in row['fields'].items():setattr(channel,key,value)
            channel.save()
            if row['pk']:updated+=1
            else:created+=1
    try:path.unlink(missing_ok=True)
    except OSError:pass
    return created,updated
