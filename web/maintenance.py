"""Scoped cleanup plans: no programmes, channels, sources or live snapshots."""
import datetime
import re
from pathlib import Path
from django.conf import settings
from django.utils import timezone
from django.db import transaction
from .models import Task,Crawl_log,AutoScheduleState


def candidates(now=None):
    from .options import options
    now=now or timezone.now();opts=options();root=Path(settings.DATA_DIR).resolve();result=[]
    def add(path):
        if path.is_symlink() or not path.is_file():return
        try:path.resolve().relative_to(root)
        except ValueError:return
        stat=path.stat();result.append({'path':str(path.relative_to(root)),'size':stat.st_size,'mtime':stat.st_mtime_ns})
    old=now-datetime.timedelta(days=opts['cleanup_log_days'])
    finished=set(Task.objects.filter(finished_at__lt=old,status__in=['success','partial','failed','cancelled']).values_list('pk',flat=True))
    for p in (root/'tasks').glob('*'):
        match=re.fullmatch(r'(\d+)(?:-[\w]+)?\.(?:log|json)',p.name)
        if match and int(match[1]) in finished:add(p)
    cutoff=(now-datetime.timedelta(days=opts['cleanup_cache_days'])).timestamp()
    for p in (root/'source-previews').glob('*.json'):
        if re.fullmatch(r'[a-f0-9]{64}\.json',p.name) and not p.is_symlink() and p.stat().st_mtime<cutoff:add(p)
    for pattern in ('epg-full-*.zip','epg-before-upgrade-*.sqlite3'):
        files=sorted((p for p in (root/'backups').glob(pattern) if p.is_file() and not p.is_symlink()),key=lambda p:p.stat().st_mtime,reverse=True)
        for p in files[opts['backup_keep']:]:add(p)
    return result


def execute(plan,cutoff):
    root=Path(settings.DATA_DIR).resolve()
    allowed={item['path']:item for item in candidates()};deleted=0
    for item in plan:
        if allowed.get(item.get('path'))!=item:continue
        p=root/item['path']
        if p.is_symlink():continue
        stat=p.stat()
        if stat.st_size==item['size'] and stat.st_mtime_ns==item['mtime']:
            p.unlink();deleted+=1
    rows=Crawl_log.objects.filter(dt__lt=datetime.datetime.fromisoformat(cutoff)).delete()[0]
    return deleted,rows


def schedule_maintenance(now=None):
    from .options import options
    from .tasks import enqueue
    now=now or timezone.now();opts=options()
    for kind,enabled in [('backup',opts['auto_backup']),('cleanup',opts['auto_cleanup'])]:
        with transaction.atomic():
            state=AutoScheduleState.objects.filter(kind=kind).first()
            signature=f'{enabled}:{opts["backup_hour"]}'
            if not enabled:
                if state:state.signature=signature;state.save(update_fields=['signature'])
                continue
            if not state or state.signature!=signature:
                local=timezone.localtime(now);when=local.replace(hour=opts['backup_hour'],minute=0,second=0,microsecond=0) if kind=='backup' else local+datetime.timedelta(days=1)
                if when<=now:when+=datetime.timedelta(days=1)
                AutoScheduleState.objects.update_or_create(kind=kind,defaults={'signature':signature,'next_run':when});continue
            if state.next_run>now:continue
            if not Task.objects.filter(kind=kind,status__in=['queued','running']).exists():enqueue(kind,params={'automatic':True})
            tomorrow=timezone.localtime(now)+datetime.timedelta(days=1)
            state.next_run=tomorrow.replace(hour=opts['backup_hour'],minute=0,second=0,microsecond=0) if kind=='backup' else now+datetime.timedelta(days=1)
            state.save(update_fields=['next_run'])
