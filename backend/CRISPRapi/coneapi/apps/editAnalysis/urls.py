from django.urls import path
from .views import EditAnalysisView, FileUploadView, TaskResultView, ResultFileContentView, DeleteTaskView

urlpatterns = [
    path("analysis/", EditAnalysisView.as_view(), name="edit-analysis"),
    path("upload/", FileUploadView.as_view(), name="file-upload"),
    path("task/<uuid:task_id>/", TaskResultView.as_view(), name="task-result"),
    path("task/<uuid:task_id>/file/<str:filename>/", ResultFileContentView.as_view(), name="result-file-content"),
    path("delete/test/task/", DeleteTaskView.as_view(), name="delete-task")
]