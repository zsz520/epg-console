"""Persistent, coalescing schedules for the single locked worker."""
import datetime
import json
import re
from zoneinfo import ZoneInfo
from django.db import transaction
from django.utils import timezone
from .models import AutoScheduleState, Task
from .runtime_config import get_config

ZONE = ZoneInfo('Asia/Shanghai')


def parse_times(value):
    result = set()
    for part in re.split(r'[\s,，;；]+', value.strip()):
        if not part:
            continue
        if not re.fullmatch(r'\d{1,2}:\d{2}', part):
            raise ValueError('请使用 24 小时制 HH:MM，例如 06:00,12:30,18:00。')
        hour, minute = map(int, part.split(':'))
        if hour > 23 or minute > 59:
            raise ValueError('时间须在 00:00 至 23:59 之间。')
        result.add(f'{hour:02d}:{minute:02d}')
    if len(result) > 48:
        raise ValueError('每天最多设置 48 个时间点。')
    return sorted(result)


def spec(config, kind):
    epg = kind == 'epg'
    return dict(enabled=config.auto_crawl if epg else config.auto_catalog,
                mode=config.epg_schedule_mode if epg else config.catalog_schedule_mode,
                seconds=config.interval_seconds if epg else config.catalog_interval_seconds,
                times=parse_times(config.epg_schedule_times if epg else config.catalog_schedule_times),
                on_start=config.crawl_on_start if epg else config.catalog_on_start)


def signature(plan):
    active={'enabled':plan['enabled'],'mode':plan['mode']}
    key='times' if plan['mode']=='daily' else 'seconds'
    active[key]=plan[key]
    return json.dumps(active, sort_keys=True)


def matches(state, plan):
    try:
        # 2.9 stored inactive fields too. Preserve its next run on upgrade.
        return signature(json.loads(state.signature)) == signature(plan)
    except (ValueError, KeyError, TypeError):
        return False


def next_daily(now, times, include_minute=False):
    local = now.astimezone(ZONE)
    threshold = local.replace(second=0, microsecond=0) if include_minute else local
    for offset in (0, 1):
        day = local.date() + datetime.timedelta(days=offset)
        for value in times:
            hour, minute = map(int, value.split(':'))
            candidate = datetime.datetime.combine(day, datetime.time(hour, minute), ZONE)
            if (include_minute and candidate >= threshold) or (not include_minute and candidate > threshold):
                return candidate
    raise ValueError('每天定时至少需要一个时间点。')


def initial_run(plan, now, first, kind):
    if plan['mode'] == 'daily':
        return next_daily(now, plan['times'], include_minute=True)
    if first:
        # Preserve the legacy automatic EPG completion anchor on upgrade.
        latest = Task.objects.filter(kind=kind, source='', requested_by__isnull=True).order_by('-created_at').first()
        if latest:
            return (latest.finished_at or latest.created_at) + datetime.timedelta(seconds=plan['seconds'])
        if plan['on_start']:
            return now
    return now + datetime.timedelta(seconds=plan['seconds'])


def tick(now=None):
    """Called between jobs and while a child runs, never executes network I/O."""
    now = now or timezone.now()
    config = get_config()
    for kind in ('epg', 'catalog'):
        plan = spec(config, kind)
        fingerprint = signature(plan)
        with transaction.atomic():
            state = AutoScheduleState.objects.filter(pk=kind).first()
            if not plan['enabled']:
                if state and state.signature != fingerprint:
                    state.signature = fingerprint
                    state.save(update_fields=['signature'])
                continue
            if not state or not matches(state,plan):
                first = state is None
                state, _ = AutoScheduleState.objects.update_or_create(kind=kind, defaults={
                    'signature': fingerprint, 'next_run': initial_run(plan, now, first, kind)})
            if now < state.next_run:
                continue
            # A whole-source manual task covers the same work as an automatic
            # one, regardless of creator or parameters. Partial tasks stay separate.
            pending = Task.objects.filter(kind=kind, source='', status__in=['queued', 'running'])
            pending = any(not t.params.get('channel_ids') for t in pending)
            if not pending:
                Task.objects.create(kind=kind, params={'automatic': True})
            if plan['mode'] == 'daily':
                # Missed slots coalesce into at most one job, including restarts.
                state.next_run = next_daily(now, plan['times'])
                state.save(update_fields=['next_run'])
            elif not pending:
                state.next_run = now + datetime.timedelta(seconds=plan['seconds'])
                state.save(update_fields=['next_run'])


    from .failures import queue_due
    from .maintenance import schedule_maintenance
    queue_due(now)
    schedule_maintenance(now)


def completed(task, now=None):
    """Interval schedules wait a full interval after a whole-source job finishes."""
    if task.kind not in ('epg', 'catalog') or task.source or task.params.get('channel_ids'):
        return
    now = now or timezone.now()
    plan = spec(get_config(), task.kind)
    if plan['enabled'] and plan['mode'] == 'interval':
        AutoScheduleState.objects.filter(pk=task.kind).update(
            signature=signature(plan), next_run=now + datetime.timedelta(seconds=plan['seconds']))


def summaries(config=None, now=None):
    config = config or get_config()
    now = now or timezone.now()
    result = []
    for kind, label in [('epg', '节目单'), ('catalog', '频道列表')]:
        plan = spec(config, kind)
        state = AutoScheduleState.objects.filter(pk=kind).first()
        due = state.next_run if state and matches(state,plan) else None
        pending=next((task for task in Task.objects.filter(kind=kind,source='',status__in=['queued','running']).order_by('pk')
                      if not task.params.get('channel_ids')),None)
        if pending and plan['mode']=='interval': due=None
        result.append({'label': label, 'enabled': plan['enabled'],
                       'pending':pending.get_status_display() if pending else '',
                       'next_run': due.astimezone(ZONE) if due else None,
                       'waiting': bool(due and due <= now)})
    return result
