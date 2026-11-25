"""epg URL Configuration

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/3.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from web.console import console_site
from django.urls import path
from django.urls import include
from django.views.generic.base import RedirectView
import web.views

urlpatterns = [
    path("", web.views.index),
    path('category/<str:category>/', web.views.index, name='public-category'),
    path("epg/<str:title>", web.views.download, name='epg-download'),
    path("download/<str:title>", RedirectView.as_view(pattern_name='epg-download', permanent=True, query_string=True)),
    path("api/", include("web.urls")),
    path("admin/", console_site.urls),
]
