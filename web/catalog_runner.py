"""Isolated per-source fetch; the parent applies an execution timeout."""
import json
import os
import sys
from pathlib import Path
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'epg.docker_settings')
import django
django.setup()
from crawl.spiders import epg_source
from .catalog import refresh_catalog

if __name__ == '__main__':
    source = sys.argv[1]
    print(f'正在获取来源：{source}', flush=True)
    try:
        result = refresh_catalog(source, epg_source[source]())
    except Exception as exc:
        from crawl.source_utils import error_message
        result={'source':source,'status':'failed','error':error_message(exc)}
        if len(sys.argv)>2:
            Path(sys.argv[2]).write_text(json.dumps(result,ensure_ascii=False),encoding='utf-8')
        print(result['error'],flush=True)
        sys.exit(1)
    if len(sys.argv) > 2:
        Path(sys.argv[2]).write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
    print('RESULT_JSON=' + json.dumps(result, ensure_ascii=False), flush=True)
