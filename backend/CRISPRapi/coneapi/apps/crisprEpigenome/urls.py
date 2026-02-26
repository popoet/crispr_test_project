from django.urls import path, re_path
from . import views
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView, TokenVerifyView
from django.views.static import serve
from django.conf import settings
import os


urlpatterns = [
    path('execute/', views.CrisprEpigenomeExecuteView.as_view(), name='crispr_epigenome_execute'),
    path('crispr_epigenome_Jbrowse_API/', views.CrisprEpigenomeJbrowseAPI.as_view(), name='crispr_epigenome_Jbrowse_API'),
    re_path(r'^crispr_epigenome_task/(?P<path>.*)$', serve, {"document_root": os.path.join(str(settings.BASE_DIR), "work", "crisprEpigenomeTmp")}),
]