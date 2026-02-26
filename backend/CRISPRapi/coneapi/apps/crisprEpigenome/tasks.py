import logging
import uuid
import json
import os
from celery import shared_task
from django.utils import timezone
from django.conf import settings
from . import crisprEpigenome
from .models import result_crispr_epigenome_list

logger = logging.getLogger(__name__)

@shared_task(bind=True)
def run_crispr_epigenome_analysis(self, task_id, inputSequence, pam, spacerLength, sgRNAModule, name_db):
    """
    异步执行crispr epigenome分析任务
    """
    try:
        # 获取任务记录
        crispr_epigenome_task_record = result_crispr_epigenome_list.objects.get(task_id=task_id)
        
        # 更新任务状态为运行中
        crispr_epigenome_task_record.task_status = 'running'
        crispr_epigenome_task_record.save()
        
        # 执行分析
        response_data = crisprEpigenome.form2Database(task_id, inputSequence, pam, spacerLength, sgRNAModule, name_db)
        
        # 检查返回结果
        if response_data == "未能获取序列和位置信息":
            crispr_epigenome_task_record.task_status = 'failed'
            crispr_epigenome_task_record.log = '未能获取序列和位置信息'
            crispr_epigenome_task_record.save()
            logger.error(f"CRISPR Epigenome任务 {task_id} 失败: 未能获取序列和位置信息")
            return {"error": "未能获取序列和位置信息"}
        elif response_data == "获取到的目标区域基因数目超出最大限制2":
            crispr_epigenome_task_record.task_status = 'failed'
            crispr_epigenome_task_record.log = '获取到的目标区域基因数目超出最大限制2'
            crispr_epigenome_task_record.save()
            logger.error(f"CRISPR Epigenome任务 {task_id} 失败: 获取到的目标区域基因数目超出最大限制2")
            return {"error": "获取到的目标区域基因数目超出最大限制2"}
        elif response_data == "目标区域包含基因数量超过限制（最多2个）":
            crispr_epigenome_task_record.task_status = 'failed'
            crispr_epigenome_task_record.log = '目标区域包含基因数量超过限制（最多2个）'
            crispr_epigenome_task_record.save()
            logger.error(f"CRISPR Epigenome任务 {task_id} 失败: 目标区域包含基因数量超过限制（最多2个）")
            return {"error": "目标区域包含基因数量超过限制（最多2个）"}
        else:
            # 任务成功完成
            crispr_epigenome_task_record.task_status = 'finished'
            # 保存结果文件路径到数据库
            crispr_epigenome_task_record.sgRNA_with_JBrowse_json = response_data
            crispr_epigenome_task_record.save()
            logger.info(f"CRISPR Epigenome任务 {task_id} 成功完成")
            
            # 返回结果数据
            result_file_path = os.path.join(settings.BASE_DIR, response_data)
            with open(result_file_path, 'r') as f:
                result_data = json.load(f)
            return result_data
            
    except result_crispr_epigenome_list.DoesNotExist:
        error_msg = f"CRISPR Epigenome任务 {task_id} 记录不存在"
        logger.error(error_msg)
        # 尝试记录错误到数据库（如果任务存在）
        try:
            crispr_epigenome_task_record = result_crispr_epigenome_list.objects.get(task_id=task_id)
            crispr_epigenome_task_record.task_status = 'failed'
            crispr_epigenome_task_record.log = error_msg
            crispr_epigenome_task_record.save()
        except result_crispr_epigenome_list.DoesNotExist:
            pass
        return {"error": "任务记录不存在"}
    except Exception as e:
        # 记录错误日志
        error_msg = f"CRISPR Epigenome任务 {task_id} 执行失败: {str(e)}"
        logger.error(error_msg, exc_info=True)
        try:
            crispr_epigenome_task_record = result_crispr_epigenome_list.objects.get(task_id=task_id)
            crispr_epigenome_task_record.task_status = 'failed'
            crispr_epigenome_task_record.log = error_msg
            crispr_epigenome_task_record.save()
        except result_crispr_epigenome_list.DoesNotExist:
            pass
        return {"error": f"任务执行失败: {str(e)}"}