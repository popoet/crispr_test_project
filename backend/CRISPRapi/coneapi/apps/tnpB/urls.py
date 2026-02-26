from django.urls import path, re_path
from . import views
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView, TokenVerifyView
from django.views.static import serve
from django.conf import settings
import os


urlpatterns = [
    path('execute/', views.TnpBExecuteView.as_view(), name='tnpb_execute'),
    path('tnpb_Jbrowse_API/', views.TnpBJbrowseAPI.as_view(), name='tnpb_Jbrowse_API'),
    re_path(r'^tnpB_task/(?P<path>.*)$',serve,{"document_root":os.path.join(str(settings.BASE_DIR), "work", "tnpBTmp")}),
]
