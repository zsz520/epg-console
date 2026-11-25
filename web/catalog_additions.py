"""Preview and atomically add catalogue selections as one or separate channels."""
import datetime
import hashlib
import json
import re

from django import forms
from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

from .catalog import SOURCE_NAMES, combine, mapping_text
from .models import Channel, Channel_list

SALT = 'catalog-channel-additions-v1'
LIMIT = 1000
ROW_FIELDS = ('pk', 'source', 'out_channel_id', 'out_name', 'category', 'logo',
              'is_alive', 'inner_channel_id', 'inner_name')


def row_digest(rows):
    data = [[getattr(row, field) for field in ROW_FIELDS] for row in rows]
    return hashlib.sha256(json.dumps(data, ensure_ascii=False).encode()).hexdigest()


def channel_digest():
    data = list(Channel.objects.order_by('pk').values_list('pk', 'tvg_id'))
    return hashlib.sha256(json.dumps(data, ensure_ascii=False).encode()).hexdigest()


def checked_rows(ids):
    if not ids or len(ids) > LIMIT or len(ids) != len(set(ids)):
        raise ValidationError(f'请选择 1–{LIMIT} 条有效的来源频道。')
    rows = list(Channel_list.objects.filter(pk__in=ids).order_by('pk'))
    if len(rows) != len(ids):
        raise ValidationError('所选来源条目已删除，请重新选择。')
    for row in rows:
        if not row.is_alive or row.source not in SOURCE_NAMES or not row.out_name.strip():
            raise ValidationError(f'{row.out_name} 的来源已失效、未加载或名称无效，请先更新目录。')
    return rows


class AdditionForm(forms.Form):
    def __init__(self, mode, rows, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if mode == 'merge':
            from .forms import GroupInput
            from .xmltv import source_name
            first = rows[0]
            self.fields.update(
                name=forms.CharField(label='频道显示名称', max_length=100, initial=first.out_name[:100]),
                tvg_name=forms.CharField(label='tvg-name', max_length=100, required=False,
                    help_text='留空使用频道显示名称。'),
                tvg_id=forms.CharField(label='tvg-id', max_length=100, required=False,
                    help_text='留空自动生成；自动生成的 ID 重复时加数字后缀。手填 ID 重复时会提示修改。'),
                sort=forms.CharField(label='频道分类', max_length=50, required=False,
                    initial=(first.category or '其他')[:50], widget=GroupInput()),
                primary=forms.ChoiceField(label='首选来源', initial=str(first.pk),
                    choices=[(str(row.pk), f'{source_name(row.out_channel_id) if row.source=="xmltv" else SOURCE_NAMES[row.source]} · {row.out_name} · {row.out_channel_id}') for row in rows]),
            )


def plan(rows, mode, values):
    if mode not in ('merge', 'separate'):
        raise ValidationError('无效的新增模式。')
    used = set(Channel.objects.exclude(tvg_id__isnull=True).values_list('tvg_id', flat=True))
    groups = [rows] if mode == 'merge' else [[row] for row in rows]
    planned = []
    for group in groups:
        if mode == 'merge':
            primary = int(values.get('primary') or group[0].pk)
            if primary not in {row.pk for row in group}:
                raise ValidationError('首选来源不在当前选择范围内。')
            group = sorted(group, key=lambda row: row.pk != primary)
        first = group[0]
        name = (values.get('name') if mode=='merge' else first.out_name).strip()[:100]
        tvg_name = (values.get('tvg_name') or name) if mode=='merge' else name
        custom_id = values.get('tvg_id') if mode=='merge' else ''
        if custom_id:
            identity = custom_id
        else:
            base = re.sub(r'''[\s<>&"']''', '', tvg_name)[:100]
            if not base or base == '9999':
                base = f'channel-{first.source}-{first.pk}'
            identity, suffix = base, 1
            while identity in used:
                suffix += 1
                ending = f'-{suffix}'
                identity = base[:100-len(ending)] + ending
        if identity in used:
            raise ValidationError(f'tvg-id {identity} 已存在，请修改或留空自动生成。')
        used.add(identity)
        mapping = combine([], group)
        fields = dict(name=name, tvg_name=tvg_name, tvg_id=identity,
            sort=((values.get('sort') or first.category or '其他') if mode=='merge' else (first.category or '其他'))[:50],
            logo=(first.logo or '')[:400], source=mapping[0][0], channel_id=mapping_text(mapping),
            has_epg=1, ineed=1, recrawl=0, last_program_date=datetime.date(2000, 1, 1))
        Channel(**fields).full_clean()
        planned.append({'fields': fields, 'rows': [row.pk for row in group], 'source_count': len(mapping)})
    return planned


def selection_token(rows, mode, user):
    return signing.dumps({'ids': [row.pk for row in rows], 'rows': row_digest(rows),
        'channels': channel_digest(), 'mode': mode, 'user': user.pk}, salt=SALT, compress=True)


def load_selection(token, mode, user):
    signed = signing.loads(token, salt=SALT, max_age=1800)
    if signed.get('user') != user.pk or signed.get('mode') != mode:
        raise ValidationError('新增确认与当前账号或模式不符，请重新选择。')
    rows = checked_rows(signed['ids'])
    if row_digest(rows) != signed['rows'] or channel_digest() != signed['channels']:
        raise ValidationError('来源目录或频道列表已发生变化，请重新选择并预览。')
    return rows


def apply(token, mode, user, values):
    if not user.has_perm('web.add_channel') or not user.has_perm('web.change_channel_list'):
        raise PermissionDenied
    with transaction.atomic():
        rows = load_selection(token, mode, user)
        planned = plan(rows, mode, values)
        created = []
        for item in planned:
            channel = Channel(**item['fields'])
            channel.save()
            created.append(channel.pk)
            Channel_list.objects.filter(pk__in=item['rows']).update(
                inner_channel_id=channel.pk, inner_name=channel.tvg_name)
        if user.has_perm('web.add_task'):
            from .tasks import enqueue
            marker = hashlib.sha256(token.encode()).hexdigest()[:20]
            transaction.on_commit(lambda: enqueue('xml', user=user, params={'catalog_additions': marker}), robust=True)
    return created
