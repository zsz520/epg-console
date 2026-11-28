"""Private token refresh and useful CSRF errors without weakening validation."""
from django.contrib.auth import get_user
from django.core.exceptions import PermissionDenied
from django.http import JsonResponse, HttpResponseNotAllowed
from django.middleware.csrf import get_token
from django.template.response import TemplateResponse
from django.views.decorators.cache import never_cache


@never_cache
def refresh(request):
    if request.method != 'GET':
        return HttpResponseNotAllowed(['GET'])
    user = request.user
    if not user.is_authenticated or not user.is_active or not (user.is_staff or user.is_superuser):
        raise PermissionDenied
    if request.headers.get('Sec-Fetch-Site') == 'cross-site':
        raise PermissionDenied
    response = JsonResponse({'token': get_token(request)})
    response['Referrer-Policy'] = 'same-origin'
    return response


def failure(request, reason=''):
    # CSRF middleware can reject a request before AuthenticationMiddleware's view hooks.
    user = getattr(request, 'user', None) or get_user(request)
    if reason.startswith('Origin checking failed') or reason.startswith('Referer checking failed'):
        message = '访问地址校验失败。请从同一个地址打开后台并保存；使用 HTTPS 反向代理时，需核对代理转发的域名、端口和可信来源。'
        code = 'origin'
    elif 'CSRF cookie not set' in reason:
        message = '浏览器没有发送校验 Cookie。请允许本站 Cookie，重新打开设置页后保存。'
        code = 'cookie'
    else:
        message = '当前页面的校验令牌已失效或缺失。请返回原页面重试；如刚在其他标签页重新登录，重新打开设置页可获得新令牌。'
        code = 'token'
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or 'application/json' in request.headers.get('Accept', ''):
        response = JsonResponse({'error': message, 'code': 'csrf_'+code}, status=403)
    else:
        response = TemplateResponse(request, '403_csrf.html', {
            'message': message, 'code': code,
            'show_diagnostics': user.is_authenticated and (user.is_staff or user.is_superuser),
        }, status=403)
    response['Cache-Control'] = 'no-store'
    return response
