from django.urls import path, re_path
from . import views
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView, TokenVerifyView
from django.views.static import serve
from django.conf import settings
import os


urlpatterns = [
    # CAS12a 相关路由
    path('cas12a/execute/', views.Cas12aExecuteView.as_view(), name='cas12a_execute'),
    path('cas12a/cas12a_Jbrowse_API/', views.Cas12aJbrowseAPI.as_view(), name='cas12a_Jbrowse_API'),
    re_path(r'^cas12a_task/(?P<path>.*)$', serve, {"document_root": os.path.join(str(settings.BASE_DIR), "work", "cas12aTmp")}),
    
    # CAS12b 相关路由
    path('cas12b/execute/', views.Cas12bExecuteView.as_view(), name='cas12b_execute'),
    path('cas12b/cas12b_Jbrowse_API/', views.Cas12bJbrowseAPI.as_view(), name='cas12b_Jbrowse_API'),
    re_path(r'^cas12b_task/(?P<path>.*)$', serve, {"document_root": os.path.join(str(settings.BASE_DIR), "work", "cas12bTmp")}),
]