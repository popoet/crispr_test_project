import logging
import uuid
import json
import os
from celery import shared_task
from django.utils import timezone
from django.conf import settings
from . import cas12
from .models import result_cas12a_list, result_cas12b_list

logger = logging.getLogger(__name__)

@shared_task(bind=True)
def run_cas12a_analysis(self, task_id, inputSequence, pam, spacerLength, sgRNAModule, name_db):
    """
    异步执行cas12a分析任务
    """
    try:
        # 获取任务记录
        cas12a_task_record = result_cas12a_list.objects.get(task_id=task_id)
        
        # 更新任务状态为运行中
        cas12a_task_record.task_status = 'running'
        cas12a_task_record.save()
        
        # 执行分析
        response_data = cas12.form12aDatabase(task_id, inputSequence, pam, spacerLength, sgRNAModule, name_db)
        
        # 检查返回结果
        if response_data == "未能获取序列和位置信息":
            cas12a_task_record.task_status = 'failed'
            cas12a_task_record.log = '未能获取序列和位置信息'
            cas12a_task_record.save()
            logger.error(f"CAS12A任务 {task_id} 失败: 未能获取序列和位置信息")
            return {"error": "未能获取序列和位置信息"}
        elif response_data == "获取到的目标区域基因数目超出最大限制2":
            cas12a_task_record.task_status = 'failed'
            cas12a_task_record.log = '获取到的目标区域基因数目超出最大限制2'
            cas12a_task_record.save()
            logger.error(f"CAS12A任务 {task_id} 失败: 获取到的目标区域基因数目超出最大限制2")
            return {"error": "获取到的目标区域基因数目超出最大限制2"}
        elif response_data == "目标区域包含基因数量超过限制（最多2个）":
            cas12a_task_record.task_status = 'failed'
            cas12a_task_record.log = '目标区域包含基因数量超过限制（最多2个）'
            cas12a_task_record.save()
            logger.error(f"CAS12A任务 {task_id} 失败: 目标区域包含基因数量超过限制（最多2个）")
            return {"error": "目标区域包含基因数量超过限制（最多2个）"}
        else:
            # 任务成功完成
            cas12a_task_record.task_status = 'finished'
            # 保存结果文件路径到数据库
            cas12a_task_record.sgRNA_with_JBrowse_json = response_data
            cas12a_task_record.save()
            logger.info(f"CAS12A任务 {task_id} 成功完成")
            
            # 返回结果数据
            result_file_path = os.path.join(settings.BASE_DIR, response_data)
            with open(result_file_path, 'r') as f:
                result_data = json.load(f)
            return result_data
            
    except result_cas12a_list.DoesNotExist:
        error_msg = f"CAS12A任务 {task_id} 记录不存在"
        logger.error(error_msg)
        # 尝试记录错误到数据库（如果任务存在）
        try:
            cas12a_task_record = result_cas12a_list.objects.get(task_id=task_id)
            cas12a_task_record.task_status = 'failed'
            cas12a_task_record.log = error_msg
            cas12a_task_record.save()
        except result_cas12a_list.DoesNotExist:
            pass
        return {"error": "任务记录不存在"}
    except Exception as e:
        # 记录错误日志
        error_msg = f"CAS12A任务 {task_id} 执行失败: {str(e)}"
        logger.error(error_msg, exc_info=True)
        try:
            cas12a_task_record = result_cas12a_list.objects.get(task_id=task_id)
            cas12a_task_record.task_status = 'failed'
            cas12a_task_record.log = error_msg
            cas12a_task_record.save()
        except result_cas12a_list.DoesNotExist:
            pass
        return {"error": f"任务执行失败: {str(e)}"}


@shared_task(bind=True)
def run_cas12b_analysis(self, task_id, inputSequence, pam, spacerLength, sgRNAModule, name_db):
    """
    异步执行cas12b分析任务
    """
    try:
        # 获取任务记录
        cas12b_task_record = result_cas12b_list.objects.get(task_id=task_id)
        
        # 更新任务状态为运行中
        cas12b_task_record.task_status = 'running'
        cas12b_task_record.save()
        
        # 执行分析
        response_data = cas12.form12bDatabase(task_id, inputSequence, pam, spacerLength, sgRNAModule, name_db)
        
        # 检查返回结果
        if response_data == "未能获取序列和位置信息":
            cas12b_task_record.task_status = 'failed'
            cas12b_task_record.log = '未能获取序列和位置信息'
            cas12b_task_record.save()
            logger.error(f"CAS12B任务 {task_id} 失败: 未能获取序列和位置信息")
            return {"error": "未能获取序列和位置信息"}
        elif response_data == "获取到的目标区域基因数目超出最大限制2":
            cas12b_task_record.task_status = 'failed'
            cas12b_task_record.log = '获取到的目标区域基因数目超出最大限制2'
            cas12b_task_record.save()
            logger.error(f"CAS12B任务 {task_id} 失败: 获取到的目标区域基因数目超出最大限制2")
            return {"error": "获取到的目标区域基因数目超出最大限制2"}
        elif response_data == "目标区域包含基因数量超过限制（最多2个）":
            cas12b_task_record.task_status = 'failed'
            cas12b_task_record.log = '目标区域包含基因数量超过限制（最多2个）'
            cas12b_task_record.save()
            logger.error(f"CAS12B任务 {task_id} 失败: 目标区域包含基因数量超过限制（最多2个）")
            return {"error": "目标区域包含基因数量超过限制（最多2个）"}
        else:
            # 任务成功完成
            cas12b_task_record.task_status = 'finished'
            # 保存结果文件路径到数据库
            cas12b_task_record.sgRNA_with_JBrowse_json = response_data
            cas12b_task_record.save()
            logger.info(f"CAS12B任务 {task_id} 成功完成")
            
            # 返回结果数据
            result_file_path = os.path.join(settings.BASE_DIR, response_data)
            with open(result_file_path, 'r') as f:
                result_data = json.load(f)
            return result_data
            
    except result_cas12b_list.DoesNotExist:
        error_msg = f"CAS12B任务 {task_id} 记录不存在"
        logger.error(error_msg)
        # 尝试记录错误到数据库（如果任务存在）
        try:
            cas12b_task_record = result_cas12b_list.objects.get(task_id=task_id)
            cas12b_task_record.task_status = 'failed'
            cas12b_task_record.log = error_msg
            cas12b_task_record.save()
        except result_cas12b_list.DoesNotExist:
            pass
        return {"error": "任务记录不存在"}
    except Exception as e:
        # 记录错误日志
        error_msg = f"CAS12B任务 {task_id} 执行失败: {str(e)}"
        logger.error(error_msg, exc_info=True)
        try:
            cas12b_task_record = result_cas12b_list.objects.get(task_id=task_id)
            cas12b_task_record.task_status = 'failed'
            cas12b_task_record.log = error_msg
            cas12b_task_record.save()
        except result_cas12b_list.DoesNotExist:
            pass
        return {"error": f"任务执行失败: {str(e)}"}