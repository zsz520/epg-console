from django.db import connection
from django.http import JsonResponse
from django.urls import path
from .urls import urlpatterns as upstream_urls


def health(request):
    with connection.cursor() as cursor:
        cursor.execute('SELECT 1')
    return JsonResponse({'status': 'ok'})


urlpatterns = [path('healthz/', health), *upstream_urls]
