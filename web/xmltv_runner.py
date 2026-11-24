import json
import os
import sys
from pathlib import Path
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'epg.docker_settings')
import django
django.setup()
from .xmltv import refresh_feed, failure_message

if __name__ == '__main__':
    try:
        result = refresh_feed(int(sys.argv[1]))
        Path(sys.argv[2]).write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
    except Exception as exc:
        message = str(exc)[:300] if isinstance(exc, ValueError) else failure_message(exc, '更新进程')
        Path(sys.argv[2]).write_text(json.dumps({'feed_id':int(sys.argv[1]), 'status':'failed', 'error':message}, ensure_ascii=False), encoding='utf-8')
        print(message, flush=True)
        sys.exit(1)
