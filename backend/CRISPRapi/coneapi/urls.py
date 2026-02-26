from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    # path('admin/', admin.site.urls),
    path('api/cas9/', include("cas9.urls")),

    path('api/fanZor/', include("fanZor.urls")),
    path('api/iscB/', include("iscB.urls")),
    path('api/tnpB/', include("tnpB.urls")),
    path('api/cas12/', include("cas12.urls")),
    path('api/baseEditor/', include("baseEditor.urls")),
    path('api/crisprKnockin/', include("crisprKnockin.urls")),
    path('api/crisprA/', include("crisprA.urls")),
    path('api/crisprEpigenome/', include("crisprEpigenome.urls")),

    path('api/editAnalysis/', include("editAnalysis.urls")),
] + static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
