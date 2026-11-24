def site_settings(request):
    from .options import options
    opts=options()
    return {'public_site_name':opts['site_name']}
