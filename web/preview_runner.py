"""Isolated live source read. Never ingests programmes or saves the dummy channel."""
import contextlib
import datetime
import json
import os
import re
import sys

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'epg.docker_settings')
import django
django.setup()
from django.utils import timezone
from .models import Channel


def normalize(result, date):
    from .programmes import project
    from .options import options
    opts=options()
    from .text_processing import display_text
    from .programmes import prepare
    midnight=timezone.make_aware(datetime.datetime.combine(date,datetime.time.min))
    rows=prepare(result.get('epgs') or [],strict=True)
    if not any(row['program_date']==date or (row['starttime']<midnight and row['endtime'] and row['endtime']>midnight) for row in rows):
        return []
    return [{'title':(display_text(str(row.get('title') or '').strip(),opts)+('' if row.get('gap_filler') else opts['title_suffix']))[:500],
             'start':row['starttime'].isoformat(),'end':row['endtime'].isoformat() if row['endtime'] else None,
             'gap_filler':bool(row.get('gap_filler')),
             'inferred_end':bool(row.get('inferred_end')),'split_fragment':bool(row.get('split_fragment'))}
            for row in project(result.get('epgs') or [],date=date,strict=True) if str(row.get('title') or '').strip()]

def main():
    params = json.loads(sys.stdin.read(10000))
    source, identifier = params['source'], params['identifier']
    date = datetime.date.fromisoformat(params['date'])
    channel = Channel(id=0, name=params.get('name', '来源预览'), tvg_name='preview',
        sort='其他', source=source, channel_id=f'<{source}:{identifier}>')
    try:
        with open(os.devnull, 'w') as sink, contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            from crawl.spiders import epg_func
            result = epg_func(channel, identifier, date, source=source)
        from .runtime_config import get_config
        from .options import options
        config=get_config(); opts=options(config)
        warning=''
        current=result.get('epgs') or []
        if config.split_midnight and opts['preview_previous_day'] and current:
            from .programmes import aware
            midnight=timezone.make_aware(datetime.datetime.combine(date,datetime.time.min))
            if min(aware(row['starttime']) for row in current)>midnight:
                with open(os.devnull,'w') as sink, contextlib.redirect_stdout(sink),contextlib.redirect_stderr(sink):
                    previous=epg_func(channel,identifier,date-datetime.timedelta(days=1),source=source)
                if previous.get('epgs'): result={**result,'epgs':previous['epgs']+current}
                else: warning='前一天节目未返回，凌晨衔接片段可能不完整。'
        programmes = normalize(result, date)
        message = str(result.get('msg') or '该来源未返回当天节目。')
        message = re.sub(r'https?://\S+', '[接口地址]', message)[:300]
        response = {'status': 'success' if programmes else 'empty', 'count': len(programmes),
            'real_count':sum(not row['gap_filler'] for row in programmes),
            'filled_count':sum(row['gap_filler'] for row in programmes),
            'programmes': programmes[:opts['preview_rows']], 'truncated': len(programmes) > opts['preview_rows'], 'limit':opts['preview_rows'], 'warning':warning,
            'message': '' if programmes else message}
        if source == 'xmltv':
            response.update({key: result.get(key) for key in ('origin_updated_at', 'origin_name', 'origin_stale')})
    except Exception:
        response = {'status': 'error', 'count': 0, 'programmes': [],
                    'message': '来源请求或节目解析失败，可尝试其他来源。'}
    print(json.dumps(response, ensure_ascii=False))


if __name__ == '__main__':
    main()

