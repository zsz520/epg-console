"""Reorder visible channels in their global slots without moving hidden channels."""
import hashlib
import json
from django.core import signing
from .models import Channel
from .ordering import ordered_channels


def fingerprint(rows):
    return hashlib.sha256(json.dumps(rows).encode()).hexdigest()


def ordered_rows():
    return list(ordered_channels(Channel.objects.all()).values_list('pk', 'sort_order', 'sort'))


def token(user, ids, rows=None):
    return signing.dumps({'user': user.pk, 'ids': ids, 'revision': fingerprint(ordered_rows() if rows is None else rows)}, salt='channel-order')


def reorder(user, signed, ids):
    from django.db import transaction
    if not isinstance(signed, str):
        raise ValueError('排序凭证无效，请刷新页面。')
    data = signing.loads(signed, salt='channel-order', max_age=3600)
    if data.get('user') != user.pk or not isinstance(ids, list) or any(type(pk) is not int for pk in ids):
        raise ValueError('排序请求无效，请刷新页面。')
    before = data['ids']
    if len(ids) != len(before) or len(set(ids)) != len(ids) or set(ids) != set(before):
        raise ValueError('排序频道与当前页面不一致，请刷新页面。')
    with transaction.atomic():
        channels = list(ordered_channels(Channel.objects.select_for_update()))
        rows = [(c.pk, c.sort_order, c.sort) for c in channels]
        if fingerprint(rows) != data['revision']:
            raise ValueError('频道顺序或列表已在其他页面修改，请刷新后重试。')
        targets = iter(ids)
        lookup = {c.pk: c for c in channels}
        if [lookup[pk].sort for pk in before] != [lookup[pk].sort for pk in ids]:
            raise ValueError('请在同一分组内拖动排序；分组顺序为央视、卫视、数字付费、港澳台及其他地方分组。')
        visible = set(before)
        arranged = [lookup[next(targets)] if c.pk in visible else c for c in channels]
        for index, channel in enumerate(arranged, 1):
            channel.sort_order = index * 10
        Channel.objects.bulk_update(arranged, ['sort_order'])
        return token(user, ids, [(c.pk, c.sort_order, c.sort) for c in arranged])
