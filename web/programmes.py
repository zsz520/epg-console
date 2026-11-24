"""Shared normalization and reversible day slicing for every programme source."""
from datetime import datetime, time, timedelta
from django.utils import timezone


def aware(value):
    if not isinstance(value,datetime):
        raise ValueError('来源未返回有效节目时间。')
    if timezone.is_naive(value):
        value=timezone.make_aware(value,timezone.get_current_timezone())
    return timezone.localtime(value)


def prepare(records, strict=False):
    rows=[]
    for raw in records:
        row=dict(raw)
        if strict and not str(row.get('title') or '').strip():
            raise ValueError('节目名称不能为空。')
        row['starttime']=aware(row['starttime'])
        row['endtime']=aware(row['endtime']) if row.get('endtime') else None
        if row['endtime'] and row['endtime']<=row['starttime']:
            if strict:
                raise ValueError('节目结束时间必须晚于开始时间。')
            row['endtime']=None
        if row['endtime'] and (row['endtime']-row['starttime']).days>31:
            if strict: raise ValueError('单个节目跨越超过 31 天，时间数据异常。')
            continue
        row['program_date']=row['starttime'].date()
        rows.append(row)
    rows.sort(key=lambda row:(str(row.get('channel_id','')),row['starttime']))
    if strict:
        unique={}
        for row in rows:
            key=(str(row.get('channel_id')),row['starttime'])
            if key in unique:
                previous=unique[key]
                if (previous['title'],previous['endtime'])!=(row['title'],row['endtime']):
                    raise ValueError('同一频道在同一开始时间存在冲突节目。')
                continue
            unique[key]=row
        rows=list(unique.values())
    for index,row in enumerate(rows[:-1]):
        next_row=rows[index+1]
        if row['endtime'] is None and str(row.get('channel_id'))==str(next_row.get('channel_id')) and next_row['starttime']>row['starttime']:
            row['endtime']=next_row['starttime']; row['inferred_end']=True
    return rows


def project(records, split=None, date=None, strict=False, fill=None, channel_ids=None, date_range=None):
    """Align days after fetching: yesterday supplies title, today supplies timing.

    Synthetic rows are projections only and never written into raw snapshots.
    Real starts take precedence over previous-day duration and overlapping stops.
    """
    if split is None or fill is None:
        from .runtime_config import get_config
        config=get_config()
        if split is None: split=config.split_midnight
        if fill is None: fill=config.fill_programme_gaps
    rows=[]; seen=set()
    for row in prepare(records,strict=strict):
        key=(str(row.get('channel_id','')),row['starttime'],row['endtime'],row.get('title',''))
        if key not in seen:
            rows.append(row); seen.add(key)
    groups={str(key):[] for key in (channel_ids or [])}
    for row in rows:
        groups.setdefault(str(row.get('channel_id','')),[]).append(row)
    result=[]
    for channel_id, original in groups.items():
        if date is not None:
            days=[date]
        elif date_range:
            first,last=date_range
            days=[first+timedelta(days=n) for n in range((last-first).days+1)]
        else:
            days=set()
            for row in original:
                last=(row['endtime']-timedelta(microseconds=1)).date() if row['endtime'] else row['program_date']
                days.update(row['program_date']+timedelta(days=n) for n in range((last-row['program_date']).days+1))
            days=sorted(days)
        for day in days:
            midnight=timezone.make_aware(datetime.combine(day,time.min))
            day_end=timezone.make_aware(datetime.combine(day,time(23,59,59)))
            actual=[dict(row) for row in original if row['program_date']==day]
            # First actual programme has authoritative start time. Use yesterday's
            # final title for 00:00 -> that start, even if yesterday's stop disagrees.
            previous=[row for row in original if row['program_date']<day and
                      (row['program_date']==day-timedelta(days=1) or
                       (row['endtime'] and row['endtime']>midnight))]
            carry=max(previous,key=lambda row:row['starttime']) if previous else None
            segments=[]
            if split and carry:
                stop=actual[0]['starttime'] if actual else min(carry['endtime'] or midnight,day_end)
                if stop>midnight:
                    segments.append({**carry,'starttime':midnight,'endtime':stop,
                        'program_date':day,'split_fragment':True,'inferred_end':True})
            elif not split:
                # Raw cross-day programmes remain intact; include only coverage
                # here to decide which intervals really need placeholders.
                segments.extend({**row,'starttime':midnight,'endtime':min(row['endtime'],day_end)}
                    for row in previous if row['endtime'] and row['endtime']>midnight)
            for index,row in enumerate(actual):
                next_start=actual[index+1]['starttime'] if index+1<len(actual) else day_end
                end=row['endtime']
                if end is None:
                    end=next_start; row['inferred_end']=True
                row['endtime']=min(end,next_start,day_end)
                if end.date()>day: row['split_fragment']=True
                segments.append(row)
            cursor=midnight
            for segment in segments:
                if not str(segment.get('title') or '').strip(): continue
                start=max(segment['starttime'],cursor)
                end=min(segment['endtime'],day_end)
                if fill and _visible_gap(cursor,start):
                    result.append(_gap(channel_id,day,cursor,start))
                if end>start:
                    if split: result.append({**segment,'starttime':start,'endtime':end})
                    cursor=end
            if fill and _visible_gap(cursor,day_end):
                result.append(_gap(channel_id,day,cursor,day_end))
    if not split:
        result.extend(row for row in rows if (date is None or row['program_date']==date) and
                      (date_range is None or date_range[0]<=row['program_date']<=date_range[1]))
    return sorted(result,key=lambda row:(row['starttime'],str(row.get('channel_id',''))))


def _visible_gap(start,end):
    # All public programme times are shown to the minute. Do not create a
    # placeholder whose two endpoints would both display as the same minute.
    return end.replace(second=0,microsecond=0)>start.replace(second=0,microsecond=0)


def _gap(channel_id,day,start,end):
    return {'channel_id':channel_id,'program_date':day,'starttime':start,'endtime':end,
            'title':'精彩继续','descr':'','desc':'','source':'','gap_filler':True}


def add_previous_context(channel,result,date,identifier,source):
    """Fetch only missing midnight carry-in, without replacing yesterday's snapshot."""
    from .runtime_config import get_config
    from .options import options
    from .models import Epg
    config=get_config()
    if not config.split_midnight or not options(config)['fetch_previous_day'] or not result.get('epgs'):
        return result
    midnight=timezone.make_aware(datetime.combine(date,time.min))
    current=prepare(result['epgs'],strict=True)
    if min(row['starttime'] for row in current)<=midnight or Epg.objects.filter(channel_id=str(channel.pk),starttime__lt=midnight,endtime__gt=midnight).exists():
        return result
    from crawl.spiders import epg_func
    previous=epg_func(channel,identifier,date-timedelta(days=1),source=source)
    if previous.get('epgs'):
        combined=prepare(previous['epgs']+current,strict=True)
        previous_rows=[row for row in combined if row['program_date']==date-timedelta(days=1)]
        carry=[max(previous_rows,key=lambda row:row['starttime'])] if previous_rows else []
        return {**result,'context_epgs':carry}
    return result
