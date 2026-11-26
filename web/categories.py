"""Shared category navigation for the public website and the administration UI."""
from collections import Counter
from urllib.parse import urlencode
from django.urls import reverse
from .models import Channel

PRESETS = [('cctv', '央视', ['央视']), ('satellite', '卫视', ['卫视']),
           ('digital', '数字付费', ['数字付费']), ('overseas', '港澳台 / 海外', ['香港', '澳门', '台湾', '海外'])]


def navigation(request):
    if not (request.path in ('/', '/api/web/') or request.path.startswith(('/category/', '/admin/'))):
        return {}
    public = not request.path.startswith('/admin/')
    query = Channel.objects.all()
    if public:
        query = query.filter(ineed=1, has_epg=1)
    counts = Counter(query.values_list('sort', flat=True))
    fixed = {x for _, _, names in PRESETS for x in names}
    items = []
    selected_key = (request.resolver_match.kwargs.get('category', '') if request.resolver_match else '') or request.GET.get('category', '')
    selected_sort = request.GET.get('sort', request.GET.get('sort__exact', ''))
    from .options import options
    show_empty=not public or options()['public_show_empty_categories']
    for key, label, names in PRESETS:
        if not show_empty and not sum(counts[n] for n in names):continue
        url = '/category/' + key + '/' if public else reverse('admin:web_channel_changelist') + '?' + urlencode({'category': key})
        items.append({'key': key, 'label': label, 'count': sum(counts[n] for n in names), 'url': url, 'active': key == selected_key})
    local = [{'label': name, 'count': counts[name], 'active': selected_sort == name, 'url': ('/?' if public else reverse('admin:web_channel_changelist') + '?') + urlencode({'sort': name} if public else {'sort__exact': name})}
             for name in sorted(counts) if name not in fixed]
    return {'category_navigation': items, 'local_categories': local, 'category_all_active': not selected_key and not selected_sort,
            'all_channel_count': sum(counts.values())}


def filter_category(queryset, category='', sort=''):
    if category:
        names = next((names for key, _, names in PRESETS if key == category), [])
        return queryset.filter(sort__in=names)
    return queryset.filter(sort=sort) if sort else queryset
