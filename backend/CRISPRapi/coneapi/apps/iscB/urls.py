from django.urls import path, re_path
from . import views
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView, TokenVerifyView
from django.views.static import serve
from django.conf import settings
import os


urlpatterns = [
    path('execute/', views.IscBExecuteView.as_view(), name='iscb_execute'),
    path('iscb_Jbrowse_API/', views.IscBJbrowseAPI.as_view(), name='iscb_Jbrowse_API'),
    re_path(r'^iscb_task/(?P<path>.*)$',serve,{"document_root":os.path.join(str(settings.BASE_DIR), "work", "iscBTmp")}),
]
