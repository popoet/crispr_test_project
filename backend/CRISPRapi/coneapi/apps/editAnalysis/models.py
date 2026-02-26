import uuid
from django.db import models


class EditAnalysisFiles(models.Model):
    id = models.BigAutoField(primary_key=True)
    file_type = models.CharField(max_length=50)  # fq_files / target_file
    file_name = models.CharField(max_length=255)
    file_size = models.BigIntegerField()
    file_md5 = models.CharField(max_length=64, unique=True)  # md5 唯一索引

    class Meta:
        db_table = "edit_analysis_files"


class EditAnalysisTasks(models.Model):
    STATUS_CHOICES = [
        ("analysis", "Analysis"),
        ("success", "Success"),
        ("failure", "Failure"),
        ("partial_success", "Partial Success"),
    ]

    id = models.BigAutoField(primary_key=True)
    task_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    fq_files_md5 = models.CharField(max_length=64)
    target_file_md5 = models.CharField(max_length=64)
    start = models.IntegerField()
    end = models.IntegerField()
    result_data = models.JSONField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="analysis")
    create_time = models.DateTimeField(auto_now_add=True)
    time_of_completion = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "edit_analysis_tasks"
        indexes = [
            models.Index(fields=["fq_files_md5", "target_file_md5", "start", "end"]),
        ]
