from django.urls import path, re_path
from . import views
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView, TokenVerifyView
from django.views.static import serve
from django.conf import settings


urlpatterns = [
    path('execute/', views.Cas9ExecuteView.as_view(), name='cas9_execute'),
    path('cas9_Jbrowse_API/', views.Cas9JbrowseAPI.as_view(), name='cas9_Jbrowse_API'),
    re_path(r'^cas9_task/(?P<path>.*)$',serve,{"document_root": f"{settings.BASE_DIR}/work/cas9Tmp"}),
]
