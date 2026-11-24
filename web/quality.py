"""Check incoming real data before deleting the previous snapshot."""
import datetime
from django.utils import timezone
from .models import Epg


def check(channel,records):
    from .options import options
    opts=options()
    if not opts['quality_check']:return
    for day in {r['program_date'] for r in records}:
        old=Epg.objects.filter(channel_id=str(channel.pk),program_date=day).count()
        if old<opts['quality_min_old_count']:continue
        rows=sorted((r for r in records if r['program_date']==day),key=lambda r:r['starttime'])
        if len(rows)*100<old*opts['quality_min_percent']:
            raise ValueError(f'完整性保护：{day} 新节目 {len(rows)} 条，原有 {old} 条，低于 {opts["quality_min_percent"]}% 阈值；已保留旧节目。')
        if opts['quality_gap_minutes']:
            start=timezone.make_aware(datetime.datetime.combine(day,datetime.time.min))
            end=start+datetime.timedelta(days=1);cursor=start;largest=0
            for i,row in enumerate(rows):
                largest=max(largest,(row['starttime']-cursor).total_seconds())
                stop=row['endtime'] or (rows[i+1]['starttime'] if i+1<len(rows) else end)
                cursor=max(cursor,stop)
            largest=max(largest,(end-cursor).total_seconds())
            if largest>opts['quality_gap_minutes']*60:
                raise ValueError(f'完整性保护：{day} 最长空缺约 {int(largest/60)} 分钟；已保留旧节目。')
