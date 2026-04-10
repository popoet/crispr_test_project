import logging
import uuid
import json
import os
import subprocess
from celery import shared_task
from django.utils import timezone
from django.conf import settings
from . import crisprA
from .models import result_crispra_list

logger = logging.getLogger(__name__)

@shared_task(bind=True)
def run_crispra_analysis(self, task_id, inputSequence, pam, spacerLength, sgRNAModule, name_db, upstream_sequence_length=2000):
    """
    异步执行crisprA分析任务
    """
    try:
        # 获取任务记录
        crispra_task_record = result_crispra_list.objects.get(task_id=task_id)
        
        # 更新任务状态为运行中
        crispra_task_record.task_status = 'running'
        crispra_task_record.upstream_sequence_length = upstream_sequence_length
        crispra_task_record.save()
        
        # 执行分析
        response_data = crisprA.form2Database(task_id, inputSequence, pam, spacerLength, sgRNAModule, name_db, upstream_sequence_length)
        
        # 检查返回结果
        if response_data == "未能获取序列和位置信息":
            crispra_task_record.task_status = 'failed'
            crispra_task_record.log = '未能获取序列和位置信息'
            crispra_task_record.save()
            logger.error(f"CRISPRa任务 {task_id} 失败: 未能获取序列和位置信息")
            return {"error": "未能获取序列和位置信息"}
        elif response_data == "获取到的目标区域基因数目超出最大限制2":
            crispra_task_record.task_status = 'failed'
            crispra_task_record.log = '获取到的目标区域基因数目超出最大限制2'
            crispra_task_record.save()
            logger.error(f"CRISPRa任务 {task_id} 失败: 获取到的目标区域基因数目超出最大限制2")
            return {"error": "获取到的目标区域基因数目超出最大限制2"}
        elif response_data == "目标区域包含基因数量超过限制（最多2个）":
            crispra_task_record.task_status = 'failed'
            crispra_task_record.log = '目标区域包含基因数量超过限制（最多2个）'
            crispra_task_record.save()
            logger.error(f"CRISPRa任务 {task_id} 失败: 目标区域包含基因数量超过限制（最多2个）")
            return {"error": "目标区域包含基因数量超过限制（最多2个）"}
        else:
            # 保存结果文件路径到数据库
            crispra_task_record.sgRNA_with_JBrowse_json = response_data
            crispra_task_record.save()
            
            # 预生成 sgRNA GFF 和基因 GFF 文件
            try:
                logger.info(f"开始生成CRISPRa任务 {task_id} 的GFF文件")
                
                result_file = os.path.join(settings.BASE_DIR, response_data)
                if not os.path.exists(result_file):
                    logger.error(f"CRISPRa任务 {task_id} 的结果文件不存在: {result_file}")
                else:
                    tmp_dir = os.path.join(settings.BASE_DIR, 'work', 'crisprATmp', task_id)
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
                    logger.info(f"CRISPRa任务 {task_id} sgRNA GFF生成完成")
                    
                    # === 生成基因 GFF ===
                    gene_gff = os.path.join(tmp_dir, f"{name_db}_{task_id}_genes.gff3")
                    gene_gff_gz = gene_gff + ".gz"
                    
                    from .views import CrisprAJbrowseAPI
                    view_instance = CrisprAJbrowseAPI()
                    view_instance._generate_gene_gff(result_file, gene_gff, name_db)
                    
                    subprocess.run([f"{settings.CONDA_ENV_BIN_PATH}/bgzip", "-f", gene_gff], check=True)
                    subprocess.run([f"{settings.CONDA_ENV_BIN_PATH}/tabix", "-p", "gff", "-C", gene_gff_gz], check=True)
                    logger.info(f"CRISPRa任务 {task_id} 基因GFF生成完成")
                    
            except Exception as e:
                logger.warning(f"CRISPRa任务 {task_id} GFF文件生成失败: {str(e)}")
            
            crispra_task_record.task_status = 'finished'
            crispra_task_record.save()
            logger.info(f"CRISPRa任务 {task_id} 成功完成")

            # 返回结果数据
            result_file_path = os.path.join(settings.BASE_DIR, response_data)
            with open(result_file_path, 'r') as f:
                result_data = json.load(f)
            return result_data
            
    except result_crispra_list.DoesNotExist:
        error_msg = f"CRISPRa任务 {task_id} 记录不存在"
        logger.error(error_msg)
        # 尝试记录错误到数据库（如果任务存在）
        try:
            crispra_task_record = result_crispra_list.objects.get(task_id=task_id)
            crispra_task_record.task_status = 'failed'
            crispra_task_record.log = error_msg
            crispra_task_record.save()
        except result_crispra_list.DoesNotExist:
            pass
        return {"error": "任务记录不存在"}
    except Exception as e:
        # 记录错误日志
        error_msg = f"CRISPRa任务 {task_id} 执行失败: {str(e)}"
        logger.error(error_msg, exc_info=True)
        try:
            crispra_task_record = result_crispra_list.objects.get(task_id=task_id)
            crispra_task_record.task_status = 'failed'
            crispra_task_record.log = error_msg
            crispra_task_record.save()
        except result_crispra_list.DoesNotExist:
            pass
        return {"error": f"任务执行失败: {str(e)}"}