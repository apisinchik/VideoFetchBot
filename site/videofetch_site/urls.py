from django.contrib import admin
from django.conf import settings
from django.conf.urls.static import static as static_urlpatterns
from django.templatetags.static import static
from django.urls import path
from django.views.generic.base import RedirectView

from videofetch_app.views import MainScreen, api_analyze, api_job_status, api_start_job, job_download

urlpatterns = [
    path('', MainScreen.as_view(), name='index'),
    path("favicon.ico", RedirectView.as_view(url=static("videofetch_app/imgs/icon.png"), permanent=False)),
    path('api/analyze/', api_analyze, name='api_analyze'),
    path('api/jobs/start/', api_start_job, name='api_start_job'),
    path('api/jobs/<int:job_id>/status/', api_job_status, name='api_job_status'),
    path('jobs/<int:job_id>/download/', job_download, name='job_download'),
    path('admin/', admin.site.urls),
]

if settings.DEBUG:
    urlpatterns += static_urlpatterns(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
