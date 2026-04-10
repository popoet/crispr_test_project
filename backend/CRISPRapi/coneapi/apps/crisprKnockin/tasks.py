import logging
import uuid
import json
import os
import subprocess
from celery import shared_task
from django.utils import timezone
from django.conf import settings
from . import crisprKnockin
from .models import result_crisprknockin_list

logger = logging.getLogger(__name__)

@shared_task(bind=True)
def run_crisprknockin_analysis(self, task_id, inputSequence, pam, spacerLength, sgRNAModule, name_db):
    """
    异步执行crisprKnockin分析任务
    """
    try:
        # 获取任务记录
        crisprknockin_task_record = result_crisprknockin_list.objects.get(task_id=task_id)
        
        # 更新任务状态为运行中
        crisprknockin_task_record.task_status = 'running'
        crisprknockin_task_record.save()
        
        # 执行分析
        response_data = crisprKnockin.form2Database(task_id, inputSequence, pam, spacerLength, sgRNAModule, name_db)
        
        # 检查返回结果
        if response_data == "未能获取序列和位置信息":
            crisprknockin_task_record.task_status = 'failed'
            crisprknockin_task_record.log = '未能获取序列和位置信息'
            crisprknockin_task_record.save()
            logger.error(f"CRISPRKNOCKIN任务 {task_id} 失败: 未能获取序列和位置信息")
            return {"error": "未能获取序列和位置信息"}
        elif response_data == "获取到的目标区域基因数目超出最大限制2":
            crisprknockin_task_record.task_status = 'failed'
            crisprknockin_task_record.log = '获取到的目标区域基因数目超出最大限制2'
            crisprknockin_task_record.save()
            logger.error(f"CRISPRKNOCKIN任务 {task_id} 失败: 获取到的目标区域基因数目超出最大限制2")
            return {"error": "获取到的目标区域基因数目超出最大限制2"}
        elif response_data == "目标区域包含基因数量超过限制（最多2个）":
            crisprknockin_task_record.task_status = 'failed'
            crisprknockin_task_record.log = '目标区域包含基因数量超过限制（最多2个）'
            crisprknockin_task_record.save()
            logger.error(f"CRISPRKNOCKIN任务 {task_id} 失败: 目标区域包含基因数量超过限制（最多2个）")
            return {"error": "目标区域包含基因数量超过限制（最多2个）"}
        else:
            # 保存结果文件路径到数据库
            crisprknockin_task_record.sgRNA_with_JBrowse_json = response_data
            crisprknockin_task_record.save()
            
            # 预生成 sgRNA GFF 和基因 GFF 文件
            try:
                logger.info(f"开始生成CRISPRKNOCKIN任务 {task_id} 的GFF文件")
                
                result_file = os.path.join(settings.BASE_DIR, response_data)
                if not os.path.exists(result_file):
                    logger.error(f"CRISPRKNOCKIN任务 {task_id} 的结果文件不存在: {result_file}")
                else:
                    tmp_dir = os.path.join(settings.BASE_DIR, 'work', 'crisprKnockinTmp', task_id)
                    os.makedirs(tmp_dir, exist_ok=True)
                    
                    # === 生成 sgRNA GFF ===
                    gff = os.path.join(tmp_dir, f"{name_db}_{task_id}_sgRNA.gff3")
                    gff_gz = gff + ".gz"
                    
                    with open(gff, "w", encoding="utf-8") as gff_file:
                        gff_file.write("##gff-version 3\n")
                        
                        with open(result_file, "r") as f:
                            data = json.load(f)
                        
                        for row in data["TableData"]["json_data"]["rows"]:
                            seqid, start_str = row["sgRNA_position"].split(":")
                            start = int(start_str)
                            end = start + len(row["sgRNA_seq"]) - 1
                            strand = "+" if row["sgRNA_strand"] == "5'------3'" else "-"
                            sgRNA_id = row["sgRNA_id"]
                            attributes = f"ID={sgRNA_id};Name={sgRNA_id};Sequence={row['sgRNA_seq']}"
                            
                            gff_file.write(f"{seqid}\tsgRNA\tguide\t{start}\t{end}\t.\t{strand}\t.\t{attributes}\n")
                    
                    subprocess.run(["sort", "-t", "\t", "-k1,1", "-k4,4n", gff, "-o", gff], check=True)
                    subprocess.run([f"{settings.CONDA_ENV_BIN_PATH}/bgzip", "-f", gff], check=True)
                    subprocess.run([f"{settings.CONDA_ENV_BIN_PATH}/tabix", "-p", "gff", "-C", gff_gz], check=True)
                    logger.info(f"CRISPRKNOCKIN任务 {task_id} sgRNA GFF生成完成")
                    
                    # === 生成基因 GFF ===
                    gene_gff = os.path.join(tmp_dir, f"{name_db}_{task_id}_genes.gff3")
                    gene_gff_gz = gene_gff + ".gz"
                    
                    from .views import CrisprKnockinJbrowseAPI
                    view_instance = CrisprKnockinJbrowseAPI()
                    view_instance._generate_gene_gff(result_file, gene_gff, name_db)
                    
                    subprocess.run([f"{settings.CONDA_ENV_BIN_PATH}/bgzip", "-f", gene_gff], check=True)
                    subprocess.run([f"{settings.CONDA_ENV_BIN_PATH}/tabix", "-p", "gff", "-C", gene_gff_gz], check=True)
                    logger.info(f"CRISPRKNOCKIN任务 {task_id} 基因GFF生成完成")
                    
            except Exception as e:
                logger.warning(f"CRISPRKNOCKIN任务 {task_id} GFF文件生成失败: {str(e)}")
            
            crisprknockin_task_record.task_status = 'finished'
            crisprknockin_task_record.save()
            logger.info(f"CRISPRKNOCKIN任务 {task_id} 成功完成")

            # 返回结果数据
            result_file_path = os.path.join(settings.BASE_DIR, response_data)
            with open(result_file_path, 'r') as f:
                result_data = json.load(f)
            return result_data
            
    except result_crisprknockin_list.DoesNotExist:
        error_msg = f"CRISPRKNOCKIN任务 {task_id} 记录不存在"
        logger.error(error_msg)
        # 尝试记录错误到数据库（如果任务存在）
        try:
            crisprknockin_task_record = result_crisprknockin_list.objects.get(task_id=task_id)
            crisprknockin_task_record.task_status = 'failed'
            crisprknockin_task_record.log = error_msg
            crisprknockin_task_record.save()
        except result_crisprknockin_list.DoesNotExist:
            pass
        return {"error": "任务记录不存在"}
    except Exception as e:
        # 记录错误日志
        error_msg = f"CRISPRKNOCKIN任务 {task_id} 执行失败: {str(e)}"
        logger.error(error_msg, exc_info=True)
        try:
            crisprknockin_task_record = result_crisprknockin_list.objects.get(task_id=task_id)
            crisprknockin_task_record.task_status = 'failed'
            crisprknockin_task_record.log = error_msg
            crisprknockin_task_record.save()
        except result_crisprknockin_list.DoesNotExist:
            pass
        return {"error": f"任务执行失败: {str(e)}"}