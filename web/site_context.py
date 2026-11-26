def public_base(value):
    from urllib.parse import urlsplit
    if value is not None and not isinstance(value, str):
        raise ValueError('无效公开地址')
    value = (value or '').strip().rstrip('/')
    if not value:
        return ''
    parts = urlsplit(value)
    if parts.scheme not in ('http','https') or not parts.hostname or parts.username or parts.password or '?' in value or '#' in value or parts.path:
        raise ValueError('无效公开地址')
    if parts.port is not None and not 1 <= parts.port <= 65535:
        raise ValueError('无效端口')
    return value


def site_settings(request):
    from .options import options
    opts=options()
    from django.urls import reverse
    from django.utils import timezone
    from urllib.parse import urlencode
    try:
        base = public_base(opts['public_base_url'])
    except (ValueError, TypeError):
        base = ''
    base = base or request.build_absolute_uri('/').rstrip('/')
    diyp = base + reverse('diyp')
    return {'public_site_name':opts['site_name'],
        'public_ui':{key.removeprefix('public_'):value for key,value in opts.items() if key.startswith('public_')},
        'diyp_default_channel':opts['diyp_default_channel'],
        'public_endpoints':{
            'xml':base + reverse('epg-download',kwargs={'title':opts['all_filename']}),
            'gzip':base + reverse('epg-download',kwargs={'title':opts['all_filename']+'.gz'}),
            'diyp':diyp,
            'diyp_example':diyp + '?' + urlencode({'ch':opts['diyp_default_channel'],'date':timezone.localdate().strftime('%Y%m%d')}),
        }}
