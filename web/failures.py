import datetime
import re
from django.utils import timezone
from django.db import transaction
from .models import CrawlFailure,Task


def failed(channel,day,reason):
    from .options import options
    reason=re.sub(r'https?://\S+','[来源接口]',str(reason))[:700]
    with transaction.atomic():
        row,_=CrawlFailure.objects.get_or_create(channel=channel,program_date=day)
        if row.resolved:row.auto_attempts=0;row.attempts=0
        row.resolved=False;row.attempts+=1;row.reason=reason
        row.next_retry=timezone.now()+datetime.timedelta(minutes=options()['failure_retry_minutes'])
        row.save()


def eligible(now=None):
    from .options import options
    today=timezone.localdate(now)
    return CrawlFailure.objects.filter(resolved=False,channel__ineed=1,channel__has_epg=1,
        program_date__gte=today,program_date__lt=today+datetime.timedelta(days=options()['crawl_days']))


def queue_due(now=None):
    from .options import options
    from .tasks import enqueue
    now=now or timezone.now();opts=options()
    if not opts['auto_retry_failures'] or Task.objects.filter(kind='retry',status__in=['queued','running']).exists():return
    ids=list(eligible(now).filter(next_retry__lte=now,auto_attempts__lt=opts['failure_retry_rounds']).order_by('next_retry','pk').values_list('pk',flat=True)[:opts['failure_retry_batch_size']])
    if ids:enqueue('retry',params={'failure_ids':ids,'automatic':True})


def run(task):
    from crawl.crawl import get_epg,gen_xml
    from .ingest import save_programmes
    import gzip,os,shutil
    from utils.general import xmlinfo
    from .options import options
    from django.db.models import F
    requested=task.params.get('failure_ids',[])
    if not isinstance(requested,list) or len(requested)>10000:raise ValueError('补抓范围无效。')
    ids=list(eligible().filter(pk__in=requested).values_list('pk',flat=True))
    passed=0
    for pk in ids:
        row=eligible().select_related('channel').filter(pk=pk).first()
        if not row:continue
        if task.params.get('automatic'):
            if row.auto_attempts>=options()['failure_retry_rounds']:continue
            CrawlFailure.objects.filter(pk=pk).update(auto_attempts=F('auto_attempts')+1)
        try:
            today=timezone.localdate()
            result=get_epg(row.channel,row.program_date,earliest_date=today)
            if not result.get('epgs'):raise ValueError(result.get('msg') or '所有来源均未返回节目。')
            save_programmes(row.channel,result,date_range=(row.program_date,row.program_date))
            passed+=1
        except Exception as exc:failed(row.channel,row.program_date,exc)
    if passed:
        for group in xmlinfo:
            _,filename=gen_xml(group)
            with open(filename,'rb') as inp,gzip.open(filename+'.gz.tmp','wb') as dest:shutil.copyfileobj(inp,dest)
            os.replace(filename+'.gz.tmp',filename+'.gz')
    Task.objects.filter(pk=task.pk).update(status='success' if passed==len(ids) else ('partial' if passed else 'failed'),
        summary=f'失败项补抓 {passed}/{len(ids)} 成功；已过期或停用频道自动跳过，失败保留旧节目。')
