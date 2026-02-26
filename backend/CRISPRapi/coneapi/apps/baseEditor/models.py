from django.db import models

class result_base_editor_list(models.Model):
    def __str__(self):
        return self.task_id
    
    TASK_STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('running', 'Running'),
        ('finished', 'Finished'),
        ('failed', 'Failed'),
    ]
    
    BASE_EDITOR_TYPE_CHOICES = [
        ('ABE', 'ABE (A to G)'),
        ('CBE', 'CBE (C to T)'),
        ('GBE', 'GBE (C to G)'),
        ('ABE+CBE', 'ABE+CBE'),
        ('TBE', 'TBE (T to G/A)'),
    ]
    
    task_id = models.CharField(max_length=64, primary_key=True)
    pam_type = models.CharField(max_length=30)
    name_db = models.CharField(max_length=255)
    input_sequence = models.TextField()
    sequence_position = models.TextField(default="")
    sgRNA_module = models.CharField(max_length=20)
    spacer_length = models.PositiveSmallIntegerField()
    # Base Editor 特有字段
    base_editor_type = models.CharField(max_length=10, choices=BASE_EDITOR_TYPE_CHOICES)
    base_editing_window = models.CharField(max_length=10)  # 格式如 "14-17"
    sgRNA_with_JBrowse_json = models.TextField(default="")  # 存储结果文件路径
    task_status = models.CharField(max_length=20, choices=TASK_STATUS_CHOICES, default='pending')
    log = models.TextField(default="")
    submit_time = models.DateTimeField(auto_now_add=True)
    update_time = models.DateTimeField(auto_now=True)