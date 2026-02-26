from django.urls import path, re_path
from . import views
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView, TokenVerifyView
from django.views.static import serve
from django.conf import settings
import os


urlpatterns = [
    path('execute/', views.BaseEditorExecuteView.as_view(), name='base_editor_execute'),
    path('base_editor_Jbrowse_API/', views.BaseEditorJbrowseAPI.as_view(), name='base_editor_Jbrowse_API'),
    re_path(r'^base_editor_task/(?P<path>.*)$', serve, {"document_root": os.path.join(str(settings.BASE_DIR), "work", "baseEditorTmp")}),
]