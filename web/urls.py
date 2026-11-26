from django.urls import path
from . import views

urlpatterns = [
    path("diyp/", views.diyp, name='diyp'),
    path("web/", views.web_single_channel_epg),
]
