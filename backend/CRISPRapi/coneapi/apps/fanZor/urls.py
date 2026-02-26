from django.urls import path, re_path
from . import views
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView, TokenVerifyView
from django.views.static import serve
from django.conf import settings
import os


urlpatterns = [
    path('execute/', views.FanZorExecuteView.as_view(), name='fanzor_execute'),
    path('fanzor_Jbrowse_API/', views.FanZorJbrowseAPI.as_view(), name='fanzor_Jbrowse_API'),
    re_path(r'^fanzor_task/(?P<path>.*)$',serve,{"document_root":os.path.join(str(settings.BASE_DIR), "work", "fanZorTmp")}),
]
