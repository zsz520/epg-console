"""Bounded, read-only live previews with short, explicitly labelled caching."""
import datetime
import re
import fcntl
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from django.conf import settings
from django.utils import timezone


def run_preview(params, timeout=15):
    process = subprocess.Popen([sys.executable, '-m', 'web.preview_runner'], stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, start_new_session=True)
    try:
        output, _ = process.communicate(json.dumps(params), timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.communicate()
        return {'status': 'timeout', 'count': 0, 'programmes': [], 'message': f'来源超过 {timeout} 秒未返回，可稍后重试。'}
    if process.returncode or len(output) > 1024 * 1024:
        return {'status': 'error', 'count': 0, 'programmes': [], 'message': '来源预览未正常返回。'}
    try:
        result = json.loads(output)
        if result.get('status') not in ('success', 'empty', 'error') or not isinstance(result.get('programmes'), list):
            raise ValueError
        return result
    except (ValueError, AttributeError):
        return {'status': 'error', 'count': 0, 'programmes': [], 'message': '来源预览返回格式异常。'}


def resolve_date(value=None):
    from django.core.exceptions import ValidationError
    today=timezone.localdate()
    if value is None:
        return today
    try:
        if not isinstance(value,str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',value):
            raise ValueError
        day=datetime.date.fromisoformat(value)
        if abs((day-today).days)>7:
            raise ValueError
        return day
    except ValueError:
        raise ValidationError('预览日期请选择今天前后 7 天内的有效日期。') from None


def preview(source, identifier, name, force=False, date=None):
    from .runtime_config import get_config
    from .options import options
    config=get_config(); opts=options(config)
    date = resolve_date(date).isoformat()
    directory = Path(settings.DATA_DIR) / 'source-previews'
    directory.mkdir(parents=True, exist_ok=True)
    version = ''
    if source == 'xmltv':
        from .xmltv import split_identifier
        from .models import XmltvFeed
        try:
            feed_id, _ = split_identifier(identifier)
            version = list(XmltvFeed.objects.filter(pk=feed_id).values_list('snapshot', 'enabled', 'last_error'))
        except ValueError:
            pass
    from crawl.source_registry import registry
    provider=registry()[0].get(source)
    revision=[]
    if provider:
        try:
            stat=provider.path.stat();revision=[stat.st_mtime_ns,stat.st_size]
        except OSError: pass
    key = hashlib.sha256(json.dumps(['validated-days-v3',source, identifier, date, version,revision,config.split_midnight,config.fill_programme_gaps,opts],sort_keys=True).encode()).hexdigest()
    filename = directory / (key + '.json')
    if not force:
        try:
            result = json.loads(filename.read_text())
            ttl = opts['preview_cache_seconds'] if result.get('status') == 'success' else opts['preview_error_cache_seconds']
            if 0 <= time.time() - filename.stat().st_mtime < ttl:
                return {**result, 'cached': True}
        except (OSError, ValueError):
            pass
    # Limit work across all web threads/processes, leaving capacity for normal UI.
    slot = None
    for number in range(opts['preview_concurrency']):
        candidate = open(directory / f'slot-{number}.lock', 'a')
        try:
            fcntl.flock(candidate, fcntl.LOCK_EX | fcntl.LOCK_NB)
            slot = candidate
            break
        except BlockingIOError:
            candidate.close()
    if slot is None:
        return {'status': 'busy', 'count': 0, 'programmes': [], 'date': date,
                'message': '其他来源正在预览，请稍后重试。', 'cached': False}
    try:
        result = {**run_preview({'source': source, 'identifier': identifier, 'name': name, 'date': date},timeout=opts['preview_timeout']),
            'date': date, 'fetched_at': timezone.now().isoformat(), 'cached': False}
        temporary = filename.with_name(key + f'.{os.getpid()}.{time.time_ns()}.tmp')
        temporary.write_text(json.dumps(result, ensure_ascii=False))
        os.replace(temporary, filename)
        return result
    finally:
        slot.close()
