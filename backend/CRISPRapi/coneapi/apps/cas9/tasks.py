import logging
import uuid
import json
import os
from celery import shared_task
from django.utils import timezone
from django.conf import settings
from . import cas9
from .models import result_cas9_list

logger = logging.getLogger(__name__)

@shared_task(bind=True)
def run_cas9_analysis(self, task_id, inputSequence, pam, spacerLength, sgRNAModule, name_db):
    """
    异步执行cas9分析任务
    """
    try:
        # 获取任务记录
        cas9_task_record = result_cas9_list.objects.get(task_id=task_id)
        
        # 更新任务状态为运行中
        cas9_task_record.task_status = 'running'
        cas9_task_record.save()
        
        # 执行分析
        response_data = cas9.form2Database(task_id, inputSequence, pam, spacerLength, sgRNAModule, name_db)
        
        # 检查返回结果
        if response_data == "未能获取序列和位置信息":
            cas9_task_record.task_status = 'failed'
            cas9_task_record.log = '未能获取序列和位置信息'
            cas9_task_record.save()
            logger.error(f"CAS9任务 {task_id} 失败: 未能获取序列和位置信息")
            return {"error": "未能获取序列和位置信息"}
        elif response_data == "获取到的目标区域基因数目超出最大限制2":
            cas9_task_record.task_status = 'failed'
            cas9_task_record.log = '获取到的目标区域基因数目超出最大限制2'
            cas9_task_record.save()
            logger.error(f"CAS9任务 {task_id} 失败: 获取到的目标区域基因数目超出最大限制2")
            return {"error": "获取到的目标区域基因数目超出最大限制2"}
        else:
            # 任务成功完成
            cas9_task_record.task_status = 'finished'
            # 保存结果文件路径到数据库
            cas9_task_record.sgRNA_with_JBrowse_json = response_data
            cas9_task_record.save()
            logger.info(f"CAS9任务 {task_id} 成功完成")
            
            # 返回结果数据
            result_file_path = os.path.join(settings.BASE_DIR, response_data)
            with open(result_file_path, 'r') as f:
                result_data = json.load(f)
            return result_data
            
    except result_cas9_list.DoesNotExist:
        error_msg = f"CAS9任务 {task_id} 记录不存在"
        logger.error(error_msg)
        # 尝试记录错误到数据库（如果任务存在）
        try:
            cas9_task_record = result_cas9_list.objects.get(task_id=task_id)
            cas9_task_record.task_status = 'failed'
            cas9_task_record.log = error_msg
            cas9_task_record.save()
        except result_cas9_list.DoesNotExist:
            pass
        return {"error": "任务记录不存在"}
    except Exception as e:
        # 记录错误日志
        error_msg = f"CAS9任务 {task_id} 执行失败: {str(e)}"
        logger.error(error_msg, exc_info=True)
        try:
            cas9_task_record = result_cas9_list.objects.get(task_id=task_id)
            cas9_task_record.task_status = 'failed'
            cas9_task_record.log = error_msg
            cas9_task_record.save()
        except result_cas9_list.DoesNotExist:
            pass
        return {"error": f"任务执行失败: {str(e)}"}