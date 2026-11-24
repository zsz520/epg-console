"""Uniform lazy adapters for built-in and /data/sources single-file providers."""
from functools import partial
from crawl.source_registry import registry,invoke

_sources, SOURCE_ERRORS = registry()
epg_funcs={key:partial(invoke,source,'epg') for key,source in _sources.items()}
epg_source={key:partial(invoke,source,'channels') for key,source in _sources.items() if source.channels}
func_args={key:source.argument for key,source in _sources.items()}


def epg_func(channel,id,dt,func_arg=0,source=0):
    identifier=source or channel.source
    try:
        if identifier not in epg_funcs: raise ValueError('来源未注册，请检查来源文件并重启 web/crawler')
        result=epg_funcs[identifier](channel,id,dt)
        if not isinstance(result,dict) or not isinstance(result.get('epgs',[]),list):
            raise ValueError('来源返回的数据格式无效')
        result.setdefault('epgs',[]);result.setdefault('msg','');result.setdefault('ban',0)
        result.setdefault('last_program_date',dt)
        result['source']=identifier
        if result.get('ban'):
            return {**result,'success':0,'epgs':[]}
        if result.get('success') == 0 and result['epgs']:
            raise ValueError(result.get('msg') or '来源标记抓取失败，保留原数据。')
        from web.programmes import prepare
        rows=prepare(result['epgs'],strict=True)
        if any(str(row.get('channel_id'))!=str(channel.pk) for row in rows):
            raise ValueError('节目所属频道不符。')
        import datetime
        from django.utils import timezone
        day=dt.date() if isinstance(dt,datetime.datetime) else dt
        midnight=timezone.make_aware(datetime.datetime.combine(day,datetime.time.min))
        if rows and not any(row['program_date']==day or (row['starttime']<midnight and row['endtime'] and row['endtime']>midnight) for row in rows):
            raise ValueError('来源未返回请求日期的真实节目。')
        result['epgs']=rows
        result['success']=int(bool(result['epgs']))
        return result
    except Exception as exc:
        return {'success':0,'epgs':[],'msg':str(exc),'ban':0,'last_program_date':dt}
