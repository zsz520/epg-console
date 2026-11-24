"""Replace one channel's returned programme dates as a single transaction."""
from django.db import transaction
from .models import Epg, Channel


def save_programmes(channel, result, date_range=None):
    records = result.get('epgs') or []
    if not records or any(str(record.get('channel_id')) != str(channel.pk) for record in records):
        raise ValueError('节目列表为空或包含不属于此频道的记录。')
    from .programmes import prepare
    records=prepare(records,strict=True)
    if date_range:
        records=[row for row in records if date_range[0]<=row['program_date']<=date_range[1]]
        if not records:
            raise ValueError('来源没有返回本轮日期范围内的节目，原数据保持不变。')
    result={**result,'epgs':records}
    context=prepare(result.get('context_epgs') or [],strict=True)
    if date_range:
        context=[row for row in context if date_range[0]<=row['program_date']<=date_range[1]]
    if any(str(row.get('channel_id'))!=str(channel.pk) for row in context):
        raise ValueError('前一日衔接数据属于其他频道。')
    dates = {record['program_date'] for record in records}
    with transaction.atomic():
        from .quality import check
        check(channel,records)
        Epg.objects.filter(channel_id=str(channel.pk), program_date__in=dates).delete()
        saved = Epg.save_to_dbs(Epg, result)
        if not saved['success']:
            raise ValueError(saved['msg'])
        for row in context:
            if row['program_date'] in dates:
                continue
            if not Epg.objects.filter(channel_id=str(channel.pk),starttime=row['starttime']).exists():
                carry_saved=Epg.save_to_dbs(Epg,{**result,'epgs':[row]})
                if not carry_saved['success']: raise ValueError(carry_saved['msg'])
        latest = max(dates)
        # Update ingestion metadata only. Revalidating the whole saved channel
        # would reject legacy/removed primary sources after a valid fallback.
        from django.utils import timezone
        current=Channel.objects.values_list('last_program_date',flat=True).get(pk=channel.pk)
        channel.last_program_date = max(filter(None, [current, latest]))
        channel.last_crawl_dt = timezone.now()
        Channel.objects.filter(pk=channel.pk).update(last_program_date=channel.last_program_date,last_crawl_dt=channel.last_crawl_dt)
        from .models import CrawlFailure
        CrawlFailure.objects.filter(channel=channel,program_date__in=dates).update(resolved=True,next_retry=None)
    return len(records)
