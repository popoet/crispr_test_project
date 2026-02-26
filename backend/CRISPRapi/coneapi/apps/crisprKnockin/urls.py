from django.urls import path, re_path
from . import views
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView, TokenVerifyView
from django.views.static import serve
from django.conf import settings
import os


urlpatterns = [
    path('execute/', views.CrisprKnockinExecuteView.as_view(), name='crisprknockin_execute'),
    path('crisprknockin_Jbrowse_API/', views.CrisprKnockinJbrowseAPI.as_view(), name='crisprknockin_Jbrowse_API'),
    re_path(r'^crisprknockin_task/(?P<path>.*)$',serve,{"document_root":os.path.join(str(settings.BASE_DIR), "work", "crisprKnockinTmp")}),
]