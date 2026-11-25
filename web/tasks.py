"""Persistent tasks executed by the locked background worker process."""
import datetime
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import fcntl
from django.db import close_old_connections
from django.utils import timezone
from .models import Task
from .runtime_config import get_config

DATA = Path(os.getenv('EPG_DATA_DIR', '/data'))


def enqueue(kind, source='', user=None, params=None):
    selection_key = {'xmltv':'feed_ids', 'catalog':'sources'}.get(kind, 'channel_ids')
    explicit_all = kind == 'catalog' and (params or {}).get('all_sources')
    if kind in ('epg', 'catalog', 'xmltv') and not source and not (params or {}).get(selection_key) and not explicit_all:
        for candidate in Task.objects.filter(kind=kind, source='', status__in=['queued', 'running']).order_by('pk'):
            if not candidate.params.get(selection_key):
                return candidate, False
    existing = Task.objects.filter(kind=kind, source=source, params=params or {}, status__in=['queued', 'running']).first()
    if existing:
        return existing, False
    return Task.objects.create(kind=kind, source=source, requested_by=user, params=params or {}), True


def heartbeat():
    (DATA / 'worker-heartbeat').touch()


def run_job(task, stopping):
    from .options import options
    config = get_config()
    opts = options(config)
    timeout = opts.get(task.kind + '_task_timeout_seconds', config.task_timeout_seconds)
    log_dir = DATA / 'tasks'
    log_dir.mkdir(exist_ok=True)
    with open(log_dir / f'{task.pk}.log', 'a', encoding='utf-8') as log:
        child = subprocess.Popen([sys.executable, '-m', 'web.task_runner', str(task.pk)],
                                 stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        deadline = time.monotonic() + timeout
        reason = ''
        while child.poll() is None:
            heartbeat()
            close_old_connections()
            from .scheduling import tick
            try:
                tick()
            except Exception as exc:
                print(f'Scheduler error: {exc}', flush=True)
            cancelled = Task.objects.filter(pk=task.pk, cancel_requested=True).exists()
            if cancelled or stopping[0] or time.monotonic() > deadline:
                reason = '已取消' if cancelled else ('工作进程停止，任务中断，可重新执行' if stopping[0] else f'任务超过 {timeout} 秒')
                os.killpg(child.pid, signal.SIGTERM)
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(child.pid, signal.SIGKILL)
                    child.wait()
                break
            time.sleep(0.5)
        task.refresh_from_db()
        if reason:
            task.status = 'cancelled' if cancelled else 'failed'
            task.summary = reason
        elif child.returncode != 0:
            task.status = 'failed'
            task.summary = f'执行异常（退出码 {child.returncode}），请查看日志。'
        elif task.status == 'running':
            task.status = 'success'
            task.summary = '执行完成，请查看日志及来源结果。'
        task.finished_at = timezone.now()
        task.save(update_fields=['status', 'summary', 'finished_at'])


def worker():
    stopping = [False]
    def shutdown(signum, frame):
        stopping[0] = True
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    with open(DATA / 'worker.lock', 'w') as worker_lock:
        try:
            fcntl.flock(worker_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('已有工作进程使用此数据目录。')
        from .scheduling import tick, completed
        interrupted = list(Task.objects.filter(status='running'))
        interrupted_at = timezone.now()
        Task.objects.filter(status='running').update(status='failed', finished_at=interrupted_at, summary='工作进程曾中断，请重新执行。')
        for task in interrupted:
            completed(task, interrupted_at)
        from .catalog import queue_new_source_catalogs
        queue_new_source_catalogs()
        while not stopping[0]:
            heartbeat()
            close_old_connections()
            try:
                tick()
                with open(DATA / 'crawl.lock', 'w') as lock:
                    try:
                        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        time.sleep(1)
                        continue
                    task = Task.objects.filter(status='queued').order_by('id').first()
                    if task and Task.objects.filter(pk=task.pk, status='queued').update(status='running', started_at=timezone.now(), summary='任务已开始执行…'):
                        print(f'Start task #{task.pk}: {task.kind}', flush=True)
                        run_job(task, stopping)
                        completed(task)
                        print(f'Finished task #{task.pk}', flush=True)
            except Exception as exc:
                print(f'Worker error: {exc}', flush=True)
            time.sleep(1)
