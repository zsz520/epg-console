"""Download XMLTV snapshots and build an immutable, indexed local programme cache."""
import base64
from contextlib import closing
import datetime
import fcntl
import gzip
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
import time
import uuid
import xml.etree.ElementTree as ET
import requests
from django.core.exceptions import ValidationError
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from .catalog import name_key
from .models import Channel_list, XmltvFeed

MAX_DOWNLOAD = 100 * 1024 * 1024
MAX_XML = 512 * 1024 * 1024


def identifier(feed_id, channel_id):
    encoded = base64.urlsafe_b64encode(channel_id.encode()).decode().rstrip('=')
    value = f'{feed_id}:{encoded}'
    if len(value) > 285:
        raise ValueError('XMLTV 频道 ID 过长。')
    return value


def split_identifier(value):
    feed_id, encoded = value.split(':', 1)
    return int(feed_id), base64.b64decode(encoded + '=' * (-len(encoded) % 4), altchars=b'-_', validate=True).decode()


def directory():
    path = Path(settings.DATA_DIR) / 'xmltv'
    path.mkdir(parents=True, exist_ok=True)
    return path


def parse_time(value):
    match = re.fullmatch(r'(\d{12}|\d{14})(?:\s*(Z|[+-]\d{4}))?', (value or '').strip())
    if not match:
        raise ValueError('无效 XMLTV 节目时间。')
    digits, offset = match.groups()
    dt = datetime.datetime.strptime(digits, '%Y%m%d%H%M%S' if len(digits) == 14 else '%Y%m%d%H%M')
    offset = '+0800' if not offset else ('+0000' if offset == 'Z' else offset)
    return dt.replace(tzinfo=datetime.datetime.strptime(offset, '%z').tzinfo).astimezone(timezone.get_current_timezone())


def download(url, destination):
    from crawl.source_env import scope
    with scope('xmltv'):
        return _download(url,destination)


def _download(url, destination):
    from .runtime_config import get_config
    from .options import options
    config=get_config(); opts=options(config)
    limit=config.options.get('xmltv_download_mib',MAX_DOWNLOAD//1024//1024)*1024*1024
    deadline = time.monotonic() + opts['xmltv_download_seconds']
    session = requests.Session()
    session.max_redirects = 5
    with session, session.get(url, stream=True, timeout=(opts['xmltv_connect_seconds'],opts['xmltv_read_seconds'])) as response:
        response.raise_for_status()
        total = 0
        with open(destination, 'wb') as output:
            for chunk in response.iter_content(65536):
                total += len(chunk)
                if total > limit or time.monotonic() > deadline:
                    raise ValueError('XMLTV 下载超过大小或时间限制。')
                output.write(chunk)


def expand(source, destination):
    from .runtime_config import get_config
    config=get_config()
    limit=config.options['xmltv_expanded_mib']*1024*1024 if 'xmltv_expanded_mib' in config.options else MAX_XML
    with open(source, 'rb') as stream:
        compressed = stream.read(2) == b'\x1f\x8b'
    opener = gzip.open if compressed else open
    with opener(source, 'rb') as stream, open(destination, 'wb') as output:
        total, tail = 0, b''
        while chunk := stream.read(65536):
            total += len(chunk)
            checked = (tail + chunk).upper()
            if total > limit:
                raise ValueError(f'XMLTV 解压后超过设置的 {limit//1024//1024} MiB。')
            if b'<!ENTITY' in checked or b'\x00' in chunk:
                raise ValueError('XMLTV 不支持实体声明或 UTF-16/32 编码，请使用 UTF-8 XML。')
            tail = checked[-16:]
            output.write(chunk)
    # XMLTV commonly declares an external xmltv.dtd. Ignore a standalone
    # declaration in the prolog; never fetch a DTD or allow an internal subset.
    quoted = rb'''(?:"[^"<>\[\]]{1,2048}"|'[^'<>\[\]]{1,2048}')'''
    pattern = rb'<!DOCTYPE\s+tv(?:\s+SYSTEM\s+' + quoted + rb'|\s+PUBLIC\s+' + quoted + rb'\s+' + quoted + rb')?\s*>'
    with open(destination, 'r+b') as output:
        prefix = output.read(65536)
        match = re.search(pattern, prefix)
        root_at = prefix.find(b'<tv')
        if match and (root_at == -1 or match.end() <= root_at):
            output.seek(match.start())
            output.write(b' ' * len(match.group()))
        output.seek(0)
        tail = b''
        while chunk := output.read(65536):
            if b'<!DOCTYPE' in (tail + chunk).upper():
                raise ValueError('不支持带内部子集、重复或位置异常的 DTD 声明；标准外部 XMLTV 声明可用。')
            tail = chunk[-16:]


def failure_message(exc, phase):
    if isinstance(exc, requests.HTTPError):
        code = exc.response.status_code if exc.response is not None else '未知'
        reason = f'服务器返回 HTTP {code}'
    elif isinstance(exc, requests.Timeout):
        reason = '连接或读取超时'
    elif isinstance(exc, requests.RequestException):
        reason = f'网络请求失败（{type(exc).__name__}），请检查 DNS、网络或证书'
    elif isinstance(exc, ET.ParseError):
        reason = f'XML 格式错误（第 {exc.position[0]} 行，第 {exc.position[1]} 列）'
    elif isinstance(exc, (gzip.BadGzipFile, EOFError)):
        reason = 'gzip 文件不完整或压缩格式无效'
    elif isinstance(exc, PermissionError):
        reason = '本地 data/xmltv 文件读写或替换权限不足'
    elif isinstance(exc, ValidationError):
        reason = '来源配置未通过校验，请检查名称和 HTTP(S) 地址'
    elif isinstance(exc, (ValueError, RuntimeError)):
        reason = re.sub(r'https?://\S+', '[地址已隐藏]', str(exc))[:180]
    elif isinstance(exc, OSError):
        reason = f'本地文件操作失败（错误码 {exc.errno}）'
    else:
        reason = f'处理异常（{type(exc).__name__}）'
    return f'{phase}：{reason}；已保留原有数据。'[:300]


def build_index(xml_file, database, feed_id):
    from .options import options
    limit=options()['xmltv_max_programmes']
    count = skipped = 0
    with closing(sqlite3.connect(database)) as db:
        db.executescript('CREATE TABLE channels (id TEXT PRIMARY KEY, name TEXT, logo TEXT, category TEXT);'
            'CREATE TABLE programmes (channel TEXT, date TEXT, start TEXT, stop TEXT, title TEXT, description TEXT);')
        root = None
        for event, element in ET.iterparse(xml_file, events=('start', 'end')):
            if root is None:
                root = element
                if element.tag != 'tv':
                    raise ValueError('文件不是 XMLTV（根节点必须为 tv）。')
            if event != 'end' or element.tag not in ('channel', 'programme'):
                continue
            if element.tag == 'channel':
                key = (element.get('id') or '').strip()
                name = (element.findtext('display-name') or key).strip()
                if not key or not name or len(name) > 160:
                    raise ValueError('XMLTV 频道名称或 ID 无效。')
                identifier(feed_id, key)
                icon = element.find('icon')
                db.execute('INSERT OR REPLACE INTO channels VALUES (?,?,?,?)',
                    (key, name, (icon.get('src', '') if icon is not None else '')[:500], (element.findtext('category') or '其他')[:100]))
            else:
                try:
                    start = parse_time(element.get('start'))
                    end = parse_time(element.get('stop')) if element.get('stop') else None
                    title = (element.findtext('title') or '').strip()
                    if not element.get('channel') or not title or (end and end <= start):
                        raise ValueError
                    db.execute('INSERT INTO programmes VALUES (?,?,?,?,?,?)', (element.get('channel'),
                        start.date().isoformat(), start.isoformat(), end.isoformat() if end else None,
                        title[:200], (element.findtext('desc') or '')[:20000]))
                    count += 1
                    if count > limit:
                        raise RuntimeError(f'XMLTV 超过设置的 {limit} 条节目限制。')
                except (ValueError, OverflowError):
                    skipped += 1
            element.clear()
            root.clear()
        channel_count = db.execute('SELECT COUNT(*) FROM channels').fetchone()[0]
        matched = db.execute('SELECT COUNT(*) FROM programmes p JOIN channels c ON c.id=p.channel').fetchone()[0]
        if not channel_count or not matched:
            raise ValueError('XMLTV 没有有效频道与对应节目，保留原快照。')
        db.executescript('CREATE INDEX programme_channel_date ON programmes(channel,date,start);')
        db.commit()
    return channel_count, count, skipped


def refresh_feed(pk):
    from .options import options
    opts=options()
    root = directory()
    with open(root / f'{pk}.lock', 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        feed = XmltvFeed.objects.get(pk=pk, enabled=True)
        for abandoned in root.glob(f'{pk}-staging-*'):
            if abandoned.is_dir():
                shutil.rmtree(abandoned, ignore_errors=True)
        phase = '检查配置'
        try:
            feed.full_clean()
            with tempfile.TemporaryDirectory(prefix=f'{pk}-staging-', dir=root) as staging:
                work = Path(staging)
                phase = '下载文件'
                download(feed.url, work / 'source.download')
                phase = '解压和检查 XML'
                expand(work / 'source.download', work / 'source.xml')
                phase = '解析节目'
                channels, programmes, skipped = build_index(work / 'source.xml', work / 'index.sqlite3', pk)
                os.remove(work / 'source.download')
                generation = f'{pk}-{uuid.uuid4().hex}'
                target = root / generation
                phase = '发布本地快照'
                os.replace(work, target)
                try:
                    phase = '同步频道目录'
                    with closing(sqlite3.connect(target / 'index.sqlite3')) as db, transaction.atomic():
                        current = XmltvFeed.objects.select_for_update().get(pk=pk)
                        if current.url != feed.url or not current.enabled:
                            raise ValueError('来源配置已变更，请重新更新。')
                        Channel_list.objects.filter(source='xmltv', out_channel_id__startswith=f'{pk}:').update(is_alive=False)
                        for channel_id, name, logo, category in db.execute('SELECT * FROM channels'):
                            key = identifier(pk, channel_id)
                            Channel_list.objects.update_or_create(source='xmltv', out_channel_id=key,
                                defaults={'out_name': name, 'normalized_name': name_key(name), 'logo': logo,
                                    'category': category, 'is_alive': True, 'update_date': timezone.localdate()})
                        current.snapshot = generation
                        current.last_updated = timezone.now()
                        current.last_error = ''
                        current.channel_count, current.programme_count = channels, programmes
                        current.save(update_fields=['snapshot','last_updated','last_error','channel_count','programme_count'])
                except Exception:
                    shutil.rmtree(target)
                    raise
            # Keep the latest two generations; allow in-flight readers an hour to finish.
            old = sorted((p for p in root.glob(f'{pk}-*') if p.is_dir() and '-staging-' not in p.name), key=lambda p:p.stat().st_mtime, reverse=True)
            for path in old[opts['xmltv_snapshot_keep']:]:
                if path.stat().st_mtime < time.time() - opts['xmltv_snapshot_grace_seconds']:
                    shutil.rmtree(path, ignore_errors=True)
            return {'feed_id': pk, 'name': feed.name, 'channels': channels, 'programmes': programmes, 'skipped': skipped, 'status': 'success'}
        except Exception as exc:
            message = failure_message(exc, phase)
            XmltvFeed.objects.filter(pk=pk).update(last_error=message)
            raise ValueError(message) from None


def source_name(value):
    try:
        pk, _ = split_identifier(value)
        return 'XMLTV · ' + XmltvFeed.objects.get(pk=pk).name
    except (ValueError, XmltvFeed.DoesNotExist):
        return 'XMLTV 文件'


def get_programmes(channel, value, date):
    pk, external_id = split_identifier(value)
    feed = XmltvFeed.objects.get(pk=pk, enabled=True)
    if not re.fullmatch(rf'{pk}-[a-f0-9]{{32}}', feed.snapshot):
        raise ValueError('请先更新 XMLTV 来源。')
    database = directory() / feed.snapshot / 'index.sqlite3'
    with closing(sqlite3.connect(f'file:{database}?mode=ro', uri=True)) as db:
        records = list(db.execute('SELECT start,stop,title,description FROM programmes WHERE channel=? AND date=? ORDER BY start', (external_id, date.isoformat())))
        # Missing stops are inferred only when a later programme is actually present.
        next_day = db.execute('SELECT start FROM programmes WHERE channel=? AND date>? ORDER BY start LIMIT 1', (external_id,date.isoformat())).fetchone()
    epgs = []
    for i, (start, stop, title, desc) in enumerate(records):
        inferred = stop is None
        if not stop:
            stop = records[i+1][0] if i+1 < len(records) else (next_day[0] if next_day else None)
        epgs.append({'channel_id': channel.pk, 'starttime': datetime.datetime.fromisoformat(start),
            'endtime': datetime.datetime.fromisoformat(stop) if stop else None,
            'title': title, 'desc': desc, 'program_date': date, 'inferred_end': bool(inferred and stop)})
    return {'success': int(bool(epgs)), 'epgs': epgs, 'msg': 'XMLTV 本地快照', 'ban': 0,
        'origin_updated_at': feed.last_updated.isoformat() if feed.last_updated else '',
        'origin_name': feed.name, 'origin_stale': bool(feed.last_error)}


def refresh_feeds_for_task(task):
    """Bound each feed in a child process; failures must not block normal spiders."""
    import subprocess
    import sys
    from .models import Task
    from .options import options
    rows = XmltvFeed.objects.filter(enabled=True)
    if task.params.get('feed_ids'):
        rows = rows.filter(pk__in=task.params['feed_ids'])
    results = []
    for feed in rows:
        Task.objects.filter(pk=task.pk).update(summary=f'正在更新 XMLTV：{feed.name}')
        output = directory() / f'result-{task.pk}-{feed.pk}.json'
        output.unlink(missing_ok=True)
        timeout = options()['xmltv_source_timeout_seconds']
        try:
            child = subprocess.run([sys.executable, '-m', 'web.xmltv_runner', str(feed.pk), str(output)],
                timeout=timeout)
            if not output.exists():
                raise RuntimeError(f'更新进程退出但未返回结果（退出码 {child.returncode}）')
            result = json.loads(output.read_text(encoding='utf-8'))
            if result.get('status') not in ('success', 'failed') or (child.returncode and result['status'] != 'failed'):
                raise RuntimeError('更新进程返回格式异常')
        except subprocess.TimeoutExpired:
            result = {'feed_id': feed.pk, 'status': 'failed', 'error': f'文件更新超过任务中心设置的 {timeout} 秒；已保留原有数据。'}
        except Exception as exc:
            result = {'feed_id': feed.pk, 'status': 'failed', 'error': failure_message(exc, '更新进程')}
        if result['status'] == 'failed':
            XmltvFeed.objects.filter(pk=feed.pk).update(last_error=result['error'])
        output.unlink(missing_ok=True)
        result['name'] = feed.name
        results.append(result)
        print(f"XMLTV {feed.name}: {result['status']}", flush=True)
        if result.get('error'):
            print(result['error'], flush=True)
        Task.objects.filter(pk=task.pk).update(details=results)
    return results
