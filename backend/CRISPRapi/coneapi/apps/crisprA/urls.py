from django.urls import path, re_path
from . import views
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView, TokenVerifyView
from django.views.static import serve
from django.conf import settings
import os


urlpatterns = [
    path('execute/', views.CrisprAExecuteView.as_view(), name='crispra_execute'),
    path('crispra_Jbrowse_API/', views.CrisprAJbrowseAPI.as_view(), name='crispra_Jbrowse_API'),
    re_path(r'^crispra_task/(?P<path>.*)$', serve, {"document_root": os.path.join(str(settings.BASE_DIR), "work", "crisprATmp")}),
]