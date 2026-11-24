"""Channel catalogue validation, refresh and conservative name-based merging."""
import datetime
import re
import unicodedata
from django.core.exceptions import ValidationError
from django.db import transaction
from utils.general import cht_to_chs

from crawl.source_registry import get_source_names
SOURCE_NAMES = get_source_names()


def queue_new_source_catalogs():
    """Queue first catalogues at worker startup, with no network I/O here.

    Existing snapshots (including inactive rows) count as initialized. Failed
    first fetches can be retried at the next restart without a permanent marker.
    The caller holds the worker lock, so concurrent workers cannot enqueue twice.
    """
    from crawl.source_registry import registry
    from .models import Channel_list, Task
    from .tasks import enqueue
    from .runtime_config import get_config
    from .options import options

    config = get_config()
    opts = options(config)
    sources, _ = registry()
    existing = set(Channel_list.objects.values_list('source', flat=True).distinct())
    pending = list(Task.objects.filter(kind='catalog', status__in=['queued', 'running']))
    full_pending = any(not task.source and not task.params.get('channel_ids') for task in pending)
    created = []
    if config.auto_catalog and opts['catalog_refresh_on_restart'] and not pending:
        task, new = enqueue('catalog', params={'startup_refresh': True})
        if new:
            task.summary = '已按启动刷新设置排队更新频道目录。'
            task.save(update_fields=['summary'])
            created.append(task)
        full_pending = True
    if not opts['catalog_bootstrap_new_sources']:
        return created
    busy = {task.source for task in pending}
    # A full refresh may be limited to selected sources. New files outside that
    # range still get their own first catalogue when bootstrapping is enabled.
    covered = set(opts['catalog_sources'] or sources) if full_pending else set()
    attempted = set(Task.objects.filter(kind='catalog', params__startup_catalog=True,
        status__in=['failed', 'cancelled']).values_list('source', flat=True))
    for source, entry in sources.items():
        if not entry.external or not entry.channels or source in existing or source in busy or source in covered:
            continue
        if not opts['catalog_retry_failed_on_restart'] and source in attempted:
            continue
        task, new = enqueue('catalog', source=source, params={'startup_catalog': True})
        if new:
            task.summary = '发现新来源，已自动排队抓取首次频道目录。'
            task.save(update_fields=['summary'])
            created.append(task)
            print(f'新来源 {entry.name} ({source})：频道目录任务 #{task.pk} 已自动排队。', flush=True)
    return created


def name_key(value):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', cht_to_chs(value or ''))).casefold()


def parse_mapping(value):
    result = {}
    remaining = (value or '').strip()
    for match in re.finditer(r'<([\w]+):([^<>]+)>', remaining):
        source, identifier = match.groups()
        if source in result and result[source] != identifier:
            raise ValidationError(f'{SOURCE_NAMES.get(source, source)} 存在不同 ID，请只保留一个。')
        result[source] = identifier.strip()
    if re.sub(r'<([\w]+):([^<>]+)>', '', remaining).strip():
        raise ValidationError('来源 ID 格式应为 <来源:ID>，多个来源连续填写。')
    return result


def mapping_text(mapping):
    text = ''.join(f'<{source}:{identifier}>' for source, identifier in mapping.items())
    if len(text) > 300:
        raise ValidationError('合并后的来源 ID 超过 300 字符，请减少来源数量。')
    return text


def combine(mapping, rows):
    merged = dict(mapping)
    for row in rows:
        if row.source not in SOURCE_NAMES:
            raise ValidationError(f'不支持来源：{row.source}')
        if not row.is_alive:
            raise ValidationError(f'{row.out_name} 的 {row.source} 条目已失效，请先刷新列表。')
        if row.source in merged and merged[row.source] != row.out_channel_id:
            raise ValidationError(f'{row.out_name}：同一来源 {row.source} 有不同 ID，请分开导入或手动确认。')
        merged[row.source] = row.out_channel_id
    mapping_text(merged)
    return merged


def refresh_catalog(source, items):
    """Validate the full snapshot before changing any existing catalogue rows."""
    from .models import Channel_list
    if source not in SOURCE_NAMES or not items:
        raise ValueError('来源返回空列表，原有目录保持不变。')
    valid = {}
    for item in items:
        name = cht_to_chs(str(item.get('name', '')).strip())
        ids = item.get('id', [])
        if not isinstance(ids, (list, tuple)) or not ids:
            raise ValueError('频道 ID 数据不完整，原有目录保持不变。')
        identifier = ','.join(str(x).strip() for x in ids)
        if not name or len(name) > 160 or not identifier or len(identifier) > 300 or re.search(r'[<>\r\n]', identifier):
            raise ValueError('来源返回的频道名称或 ID 无效，原有目录保持不变。')
        valid[identifier] = {
            'out_name': name, 'normalized_name': name_key(name), 'is_alive': True,
            'category': str(item.get('sort', ''))[:100],
            'logo': str(item.get('logo', '') or '')[:500],
            'regions': [str(value)[:80] for value in item.get('regions',[])][:100],
            'update_date': datetime.date.today(),
        }
    with transaction.atomic():
        new = updated = 0
        for identifier, fields in valid.items():
            _, created = Channel_list.objects.update_or_create(
                source=source, out_channel_id=identifier, defaults=fields)
            new += int(created)
            updated += int(not created)
        inactive = Channel_list.objects.filter(source=source).exclude(out_channel_id__in=valid).update(is_alive=False)
    return {'source': source, 'new': new, 'updated': updated, 'inactive': inactive, 'total': len(valid)}


def import_catalog(ids, target_id=None):
    """One transaction, exact normalized names, explicit conflicts, no channel deletion."""
    from .models import Channel, Channel_list
    with transaction.atomic():
        rows = list(Channel_list.objects.filter(pk__in=ids).order_by('id'))
        if not rows or len(rows) != len(set(ids)):
            raise ValidationError('请选择有效的来源频道。')
        groups = {}
        for row in rows:
            groups.setdefault(name_key(row.out_name), []).append(row)
        if target_id and len(groups) != 1:
            raise ValidationError('合并到指定频道时，请只选择同一名称的来源条目。')
        channels = list(Channel.objects.all())
        created = updated = 0
        for key, group in groups.items():
            if target_id:
                matches = [c for c in channels if c.pk == int(target_id)]
                if not matches:
                    raise ValidationError('目标频道不存在。')
            else:
                matches = []
                for candidate in channels:
                    try:
                        known = parse_mapping(candidate.channel_id)
                    except ValidationError:
                        known = {}
                    same_id = any(known.get(row.source) == row.out_channel_id for row in group)
                    if same_id or key in (name_key(candidate.name), name_key(candidate.tvg_name)):
                        matches.append(candidate)
            if len(matches) > 1:
                raise ValidationError(f'{group[0].out_name} 对应多个已有频道，请指定目标频道。')
            channel = matches[0] if matches else Channel(
                name=group[0].out_name, tvg_name=group[0].out_name,
                sort=group[0].category or '其他', logo=group[0].logo or '',
                last_program_date=datetime.date(2000, 1, 1), has_epg=1, ineed=1,
            )
            mapping = combine(parse_mapping(channel.channel_id), group)
            channel.channel_id = mapping_text(mapping)
            if not channel.source:
                channel.source = next(iter(mapping))
            channel.full_clean()
            channel.save()
            Channel_list.objects.filter(pk__in=[r.pk for r in group]).update(
                inner_channel_id=channel.pk, inner_name=channel.tvg_name)
            if matches:
                updated += 1
            else:
                created += 1
                channels.append(channel)
        return {'created': created, 'updated': updated}
