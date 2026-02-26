from django.db import models

class result_crispra_list(models.Model):
    def __str__(self):
        return self.task_id
    
    TASK_STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('running', 'Running'),
        ('finished', 'Finished'),
        ('failed', 'Failed'),
    ]
    
    task_id = models.CharField(max_length=64, primary_key=True)
    pam_type = models.CharField(max_length=30)
    name_db = models.CharField(max_length=255)
    input_sequence = models.TextField()
    sequence_position = models.TextField(default="")
    sgRNA_module = models.CharField(max_length=20)
    spacer_length = models.PositiveSmallIntegerField()
    upstream_sequence_length = models.PositiveIntegerField(default=2000)
    sgRNA_with_JBrowse_json = models.TextField(default="")  # 存储结果文件路径
    task_status = models.CharField(max_length=20, choices=TASK_STATUS_CHOICES, default='pending')
    log = models.TextField(default="")
    submit_time = models.DateTimeField(auto_now_add=True)
    update_time = models.DateTimeField(auto_now=True)