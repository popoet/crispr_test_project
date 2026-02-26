from django.db import models
from django.contrib.auth.models import AbstractUser


class User(AbstractUser):
    nickname = models.CharField(max_length=50, blank=True, null=True, verbose_name='昵称')
    mobile = models.CharField(max_length=15, blank=True, null=True, verbose_name='手机号')

    class Meta:
        db_table = 'crispr1_users'
        verbose_name = '用户信息'
        verbose_name_plural = verbose_name