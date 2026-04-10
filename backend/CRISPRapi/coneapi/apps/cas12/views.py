import os
import subprocess
import uuid
import json
import shutil
import threading
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.http import FileResponse, HttpResponseNotFound, HttpResponse
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView
from . import cas12, models
from .tasks import run_cas12a_analysis, run_cas12b_analysis

# 用于并发控制的锁
_task_cleanup_lock = threading.Lock()
_task_execution_lock = threading.Lock()

# 重试限制配置
MAX_RETRY_COUNT = 3  # 最大重试次数
RETRY_CACHE_TIMEOUT = 3600  # 重试计数缓存时间（秒）

class Cas12aExecuteView(APIView):
    """
    Cas12a执行
    """

    def get(self, request):
        """
        GET请求：通过 task_id 查询已有任务结果
        """
        task_id = request.query_params.get('task_id')
            
        if not task_id:
            return Response({"msg": "缺少 task_id 参数"}, status=status.HTTP_400_BAD_REQUEST)
            
        return self._query_existing_task_a(task_id)
    
    def post(self, request):
        # 检查是否提供了 task_id 参数（用于直接访问已有结果）
        task_id = request.data.get('task_id') or request.query_params.get('task_id')
        
        if task_id:
            # 直接查询已有任务结果
            return self._query_existing_task_a(task_id)
        
        # 否则执行原有的新任务创建逻辑
        inputSequence = request.data.get('inputSequence')
        pam = request.data.get('pam', 'TTTN')
        spacerLength = request.data.get('spacerLength', 23)
        sgRNAModule = request.data.get('sgRNAModule', 'spacerpam')
        name_db = request.data.get('name_db')
        
        # 使用全局锁防止同一参数的并发执行
        task_key = f"{inputSequence}_{pam}_{spacerLength}_{sgRNAModule}_{name_db}"
        with _task_execution_lock:
            return self._process_request(inputSequence, pam, spacerLength, sgRNAModule, name_db)

    def _process_request(self, inputSequence, pam, spacerLength, sgRNAModule, name_db):
        """
        实际处理CAS12a请求的核心逻辑
        包含完整的任务重试和并发处理逻辑
        """
        # 检查数据库中是否已存在相同且已完成的任务
        result_cas12a = models.result_cas12a_list.objects.filter(input_sequence=inputSequence,
                                                                 pam_type=pam,
                                                                 spacer_length=spacerLength,
                                                                 sgRNA_module=sgRNAModule,
                                                                 name_db=name_db,
                                                                 task_status="finished",
                                                                 sgRNA_with_JBrowse_json__isnull=False)
        if result_cas12a.first():
            task_record = result_cas12a.first()
            # 从文件中读取结果数据
            result_file_path = os.path.join(settings.BASE_DIR, task_record.sgRNA_with_JBrowse_json)
            if os.path.exists(result_file_path):
                with open(result_file_path, 'r') as f:
                    response_data = json.load(f)
                # 添加原始输入参数到返回结果
                response_data['inputSequence'] = task_record.input_sequence
                response_data['pam'] = task_record.pam_type
                response_data['spacerLength'] = task_record.spacer_length
                response_data['sgRNAModule'] = task_record.sgRNA_module
                response_data['name_db'] = task_record.name_db
                return Response(response_data, status=status.HTTP_200_OK)
            else:
                # 如果文件不存在，清除记录并重新处理
                self._safe_cleanup_task_a(task_record.task_id)

        # 检查是否已存在相同但失败的任务
        failed_cas12a = models.result_cas12a_list.objects.filter(input_sequence=inputSequence,
                                                                 pam_type=pam,
                                                                 spacer_length=spacerLength,
                                                                 sgRNA_module=sgRNAModule,
                                                                 name_db=name_db,
                                                                 task_status="failed")
        if failed_cas12a.exists():
            # 生成任务标识符用于重试计数
            task_identifier = f"cas12a_{inputSequence}_{pam}_{spacerLength}_{sgRNAModule}_{name_db}"
            
            # 从Redis获取重试次数
            retry_count = cache.get(task_identifier, 0)
            
            # 如果超过最大重试次数，返回错误信息而不是无限重试
            if retry_count >= MAX_RETRY_COUNT:
                return Response({
                    "msg": f"任务已达到最大重试次数({MAX_RETRY_COUNT}次)，请检查输入参数或联系管理员",
                    "error": "重试次数超限",
                    "retry_count": retry_count,
                    "max_retries": MAX_RETRY_COUNT
                }, status=status.HTTP_429_TOO_MANY_REQUESTS)
            
            # 增加重试计数
            cache.set(task_identifier, retry_count + 1, timeout=RETRY_CACHE_TIMEOUT)
            
            # 自动清理所有失败的任务并重新执行
            for failed_task in failed_cas12a:
                old_task_id = failed_task.task_id
                # 安全清理旧任务
                cleanup_result = self._safe_cleanup_task_a(old_task_id)
                if not cleanup_result['success']:
                    return Response({
                        "msg": "清理旧任务失败",
                        "error": cleanup_result['error']
                    }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
            
            # 继续执行下面的新任务创建逻辑

        # 检查是否已存在正在进行中或等待中的任务
        existing_cas12a = models.result_cas12a_list.objects.filter(input_sequence=inputSequence,
                                                                   pam_type=pam,
                                                                   spacer_length=spacerLength,
                                                                   sgRNA_module=sgRNAModule,
                                                                   name_db=name_db)
        pending_or_running_cas12a = existing_cas12a.filter(task_status__in=['pending', 'running'])
        if pending_or_running_cas12a.exists():
            # 返回最新的任务状态信息
            latest_task = pending_or_running_cas12a.latest('submit_time')
            # 简单估算：假设任务需要5分钟完成
            estimated_completion = latest_task.submit_time + timedelta(minutes=5)
            now = timezone.now()
            remaining_time = estimated_completion - now if estimated_completion > now else timedelta(minutes=1)
            
            return Response({
                "msg": "任务正在分析中",
                "task_id": latest_task.task_id,
                "status": latest_task.task_status,
                "estimated_completion": estimated_completion.isoformat(),
                "remaining_time_seconds": remaining_time.total_seconds(),
                "retry_info": self._get_retry_info_a(inputSequence, pam, spacerLength, sgRNAModule, name_db)
            }, status=status.HTTP_202_ACCEPTED)

        # 验证参数
        try:
            spacerLength = int(spacerLength)
        except (ValueError, TypeError):
            return Response({"msg": "请输入正确的范围"}, status=status.HTTP_400_BAD_REQUEST)

        # 创建新任务
        task_id = str(uuid.uuid4())
        
        # 创建任务记录
        cas12a_task_record = models.result_cas12a_list(
            task_id=task_id,
            input_sequence=inputSequence,
            pam_type=pam,
            spacer_length=spacerLength,
            sgRNA_module=sgRNAModule,
            name_db=name_db,
            task_status='pending'
        )
        cas12a_task_record.save()

        # 异步执行任务
        run_cas12a_analysis.delay(task_id, inputSequence, pam, spacerLength, sgRNAModule, name_db)
        
        # 返回任务已提交的信息
        return Response({
            "msg": "任务已提交",
            "task_id": task_id,
            "status": "pending"
        }, status=status.HTTP_202_ACCEPTED)
    def _safe_cleanup_task_a(self, task_id):
        """
        安全地清理CAS12a任务相关的数据库记录和文件
        使用锁防止并发问题
        """
        with _task_cleanup_lock:
            try:
                # 删除数据库记录
                try:
                    task_record = models.result_cas12a_list.objects.get(task_id=task_id)
                    task_record.delete()
                except models.result_cas12a_list.DoesNotExist:
                    # 记录不存在，但继续尝试清理文件
                    pass
                
                # 删除工作目录
                task_work_dir = os.path.join(settings.BASE_DIR, 'work', 'cas12aTasks', task_id)
                task_tmp_dir = os.path.join(settings.BASE_DIR, 'work', 'cas12aTmp', task_id)
                
                # 安全删除目录
                for dir_path in [task_work_dir, task_tmp_dir]:
                    if os.path.exists(dir_path):
                        try:
                            shutil.rmtree(dir_path)
                        except Exception as e:
                            # 记录警告但不中断流程
                            print(f"警告：删除目录 {dir_path} 失败: {str(e)}")
                
                return {'success': True}
                
            except Exception as e:
                return {'success': False, 'error': str(e)}

    def _cleanup_orphaned_data_a(self, input_sequence, pam_type, spacer_length, sgRNA_module, name_db):
        """
        清理CAS12a孤立的数据（有记录但无文件，或有文件但无记录）
        """
        with _task_cleanup_lock:
            # 查找可能存在的孤立记录
            orphaned_records = models.result_cas12a_list.objects.filter(
                input_sequence=input_sequence,
                pam_type=pam_type,
                spacer_length=spacer_length,
                sgRNA_module=sgRNA_module,
                name_db=name_db
            )
            
            for record in orphaned_records:
                # 检查文件是否存在
                if record.sgRNA_with_JBrowse_json and record.task_status == 'finished':
                    result_file_path = os.path.join(settings.BASE_DIR, record.sgRNA_with_JBrowse_json)
                    if not os.path.exists(result_file_path):
                        # 文件不存在，删除记录
                        print(f"清理孤立记录: {record.task_id}")
                        record.delete()
                        continue
                
                # 检查工作目录是否存在
                task_work_dir = os.path.join(settings.BASE_DIR, 'work', 'cas12aTasks', record.task_id)
                task_tmp_dir = os.path.join(settings.BASE_DIR, 'work', 'cas12aTmp', record.task_id)
                
                if not os.path.exists(task_work_dir) and not os.path.exists(task_tmp_dir):
                    # 目录都不存在，可能是孤立记录
                    if record.task_status in ['pending', 'running']:
                        # 对于未完成的任务，如果目录不存在则标记为失败
                        record.task_status = 'failed'
                        record.log = '任务目录丢失，可能被意外删除'
                        record.save()
                        print(f"标记丢失任务为失败: {record.task_id}")
                    elif record.task_status == 'failed':
                        # 失败任务且目录不存在，可以安全删除记录
                        print(f"清理失败任务记录: {record.task_id}")
                        record.delete()

    def _get_retry_info_a(self, input_sequence, pam_type, spacer_length, sgRNA_module, name_db):
        """
        获取CAS12a重试相关信息
        """
        task_identifier = f"cas12a_{input_sequence}_{pam_type}_{spacer_length}_{sgRNA_module}_{name_db}"
        retry_count = cache.get(task_identifier, 0)
        return {
            "retry_count": retry_count,
            "max_retries": MAX_RETRY_COUNT,
            "can_retry": retry_count < MAX_RETRY_COUNT
        }
    
    def _query_existing_task_a(self, task_id):
        """
        查询已有CAS12a任务的结果
        """
        try:
            # 查询任务记录
            task_record = models.result_cas12a_list.objects.get(task_id=task_id)
            
            # 检查任务状态
            if task_record.task_status == 'failed':
                return Response({
                    "msg": "任务执行失败",
                    "error": task_record.log or "未知错误"
                }, status=status.HTTP_400_BAD_REQUEST)
            
            if task_record.task_status == 'pending' or task_record.task_status == 'running':
                return Response({
                    "msg": "任务正在运行中",
                    "task_id": task_id,
                    "status": task_record.task_status
                }, status=status.HTTP_202_ACCEPTED)
            
            if task_record.task_status != 'finished':
                return Response({
                    "msg": "任务状态未知",
                    "status": task_record.task_status
                }, status=status.HTTP_400_BAD_REQUEST)
            
            # 检查结果文件是否存在
            if not task_record.sgRNA_with_JBrowse_json:
                return Response({
                    "msg": "任务结果不存在"
                }, status=status.HTTP_404_NOT_FOUND)
            
            result_file_path = os.path.join(settings.BASE_DIR, task_record.sgRNA_with_JBrowse_json)
            if not os.path.exists(result_file_path):
                return Response({
                    "msg": "结果文件丢失"
                }, status=status.HTTP_404_NOT_FOUND)
            
            # 返回结果
            with open(result_file_path, 'r') as f:
                response_data = json.load(f)

            # 添加原始输入参数到返回结果
            response_data['inputSequence'] = task_record.input_sequence
            response_data['pam'] = task_record.pam_type
            response_data['spacerLength'] = task_record.spacer_length
            response_data['sgRNAModule'] = task_record.sgRNA_module
            response_data['name_db'] = task_record.name_db

            return Response(response_data, status=status.HTTP_200_OK)

        except models.result_cas12a_list.DoesNotExist:
            return Response({
                "msg": "任务不存在"
            }, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            return Response({
                "msg": f"查询失败：{str(e)}"
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class Cas12aJbrowseAPI(APIView):
    def get(self, request):
        task_id = request.query_params.get('task_id')
        file_type = request.query_params.get('file_type')

        if not task_id or not file_type:
            return HttpResponseNotFound("Missing parameters")

        tmp_dir = os.path.join(settings.BASE_DIR, "work", "cas12aTmp", task_id)
        os.makedirs(tmp_dir, exist_ok=True)

        try:
            cas12a_task_record = models.result_cas12a_list.objects.get(task_id=task_id)
        except models.result_cas12a_list.DoesNotExist:
            return HttpResponseNotFound("Task not found")

        fa = os.path.join(settings.BASE_DIR, f"database/TargetGenome/{cas12a_task_record.name_db}/{cas12a_task_record.name_db}.fa")
        fai = fa + ".fai"
        gff = os.path.join(tmp_dir, f"{cas12a_task_record.name_db}_{task_id}_sgRNA.gff3")
        gff_gz = gff + ".gz"
        gff_csi = gff_gz + ".csi"
        gff_tbi = gff_gz + ".tbi"
        
        # 基因轨道文件路径
        gene_gff = os.path.join(tmp_dir, f"{cas12a_task_record.name_db}_{task_id}_genes.gff3")
        gene_gff_gz = gene_gff + ".gz"
        gene_gff_csi = gene_gff_gz + ".csi"

        file_paths = {
            "fa": fa,
            "fai": fai,
            "gff3.gz": gff_gz,
            "gff3.gz.csi": gff_csi,
            "genes.gff3.gz": gene_gff_gz,
            "genes.gff3.gz.csi": gene_gff_csi,
        }

        # 用户请求非法类型
        if file_type not in file_paths:
            return HttpResponseNotFound("Invalid file type")

        # ========== 如果 gff_gz 和 index 都存在，直接返回 ==========
        if file_type == "gff3.gz.csi":
            if os.path.exists(gff_csi):
                return FileResponse(open(gff_csi, "rb"))
            if os.path.exists(gff_tbi):
                return FileResponse(open(gff_tbi, "rb"))
        elif file_type == "genes.gff3.gz.csi":
            # 基因轨道索引文件
            if os.path.exists(gene_gff_csi):
                return FileResponse(open(gene_gff_csi, "rb"))
            return HttpResponseNotFound("Gene track index file missing")
        elif file_type == "genes.gff3.gz":
            # 基因轨道 GFF 文件
            if all(os.path.exists(p) for p in [gene_gff_gz]):
                return FileResponse(open(gene_gff_gz, "rb"))
            # 如果需要生成，继续执行下面的逻辑
        else:
            if all(os.path.exists(p) for p in [fa, fai, gff_gz]) and (os.path.exists(gff_csi) or os.path.exists(gff_tbi)):
                return FileResponse(open(file_paths[file_type], "rb"))

        # ========== 生成 sgRNA GFF ==========
        result_file = os.path.join(settings.BASE_DIR, cas12a_task_record.sgRNA_with_JBrowse_json)
        if not os.path.exists(result_file):
            return HttpResponseNotFound("sgRNA result file missing")

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

        # ========== 排序 ==========
        try:
            subprocess.run([
                "sort", "-t", "\t", "-k1,1", "-k4,4n", gff, "-o", gff
            ], check=True)

            subprocess.run([f"{settings.CONDA_ENV_BIN_PATH}/bgzip", "-f", gff], check=True)
            subprocess.run([f"{settings.CONDA_ENV_BIN_PATH}/tabix", "-p", "gff", "-C", gff_gz], check=True)
        except subprocess.CalledProcessError:
            return HttpResponse("Error generating gff3.gz and index", status=500)

        # ========== 返回文件：csi 优先，没有用 tbi ==========
        if file_type == "gff3.gz.csi":
            if os.path.exists(gff_csi):
                return FileResponse(open(gff_csi, "rb"))
            if os.path.exists(gff_tbi):
                return FileResponse(open(gff_tbi, "rb"))
            return HttpResponseNotFound("Index file missing (.csi and .tbi)")
        elif file_type == "genes.gff3.gz.csi":
            # 基因轨道索引文件 - 异步生成，只返回已存在的文件
            if os.path.exists(gene_gff_csi):
                return FileResponse(open(gene_gff_csi, "rb"))
            return HttpResponseNotFound("Gene track index file not ready")
        elif file_type == "genes.gff3.gz":
            # 基因轨道 GFF 文件 - 异步生成，只返回已存在的文件
            if os.path.exists(gene_gff_gz):
                return FileResponse(open(gene_gff_gz, "rb"))
            return HttpResponseNotFound("Gene track file not ready")
        else:
            if os.path.exists(file_paths[file_type]):
                return FileResponse(open(file_paths[file_type], "rb"))
            return HttpResponseNotFound("File generation failed")
    
    def _generate_gene_gff(self, result_file, gene_gff_path, name_db):
        """
        从 sgRNA 结果文件中提取基因信息，并生成基因 GFF 文件（保持原始格式）
        
        参数:
            result_file: sgRNA 结果 JSON 文件路径
            gene_gff_path: 输出的基因 GFF 文件路径
            name_db: 数据库名称
        """
        try:
            print(f"DEBUG: 开始生成基因GFF文件")
            print(f"DEBUG: result_file = {result_file}")
            print(f"DEBUG: gene_gff_path = {gene_gff_path}")
            print(f"DEBUG: name_db = {name_db}")

            with open(result_file, "r") as f:
                data = json.load(f)

            print(f"DEBUG: result_file读取成功，data的顶层keys: {list(data.keys())}")

            gene_ids = set()

            if "TableData" not in data:
                print(f"DEBUG: 错误！data中没有TableData键")
                with open(gene_gff_path, "w", encoding="utf-8") as f:
                    f.write("##gff-version 3\n")
                return

            table_data = data.get("TableData", {})
            print(f"DEBUG: TableData的keys: {list(table_data.keys()) if isinstance(table_data, dict) else type(table_data)}")

            json_data = table_data.get("json_data", {})
            print(f"DEBUG: json_data的keys: {list(json_data.keys()) if isinstance(json_data, dict) else type(json_data)}")

            rows = json_data.get("rows", [])
            print(f"DEBUG: rows数量: {len(rows)}")

            if len(rows) == 0:
                print(f"DEBUG: 警告！rows为空")
                with open(gene_gff_path, "w", encoding="utf-8") as f:
                    f.write("##gff-version 3\n")
                return

            for i, row in enumerate(rows):
                gene_id_from_type = None
                if 'sgRNA_type' in row and row['sgRNA_type']:
                    type_value = row['sgRNA_type']
                    if isinstance(type_value, str):
                        parts = [p.strip() for p in type_value.split(',')]
                        for part in parts:
                            if part.startswith('gene-'):
                                gene_id = part.split('-')[1]
                                gene_id = self._normalize_gene_id(gene_id)
                                if gene_id:
                                    gene_ids.add(gene_id)
                                break
                        if not gene_id_from_type and parts:
                            first_part = parts[0]
                            if first_part:
                                gene_id_from_type = self._normalize_gene_id(first_part)
                                if gene_id_from_type:
                                    gene_ids.add(gene_id_from_type)

                elif 'sgRNA_family' in row and row['sgRNA_family']:
                    family_value = row['sgRNA_family']
                    if isinstance(family_value, str):
                        for gene_id in family_value.split(','):
                            gene_id = gene_id.strip()
                            if gene_id:
                                normalized_id = self._normalize_gene_id(gene_id)
                                if normalized_id:
                                    gene_ids.add(normalized_id)
                    elif isinstance(family_value, (list, tuple)):
                        for gene_id in family_value:
                            if gene_id:
                                normalized_id = self._normalize_gene_id(str(gene_id))
                                if normalized_id:
                                    gene_ids.add(normalized_id)

                if i < 3:
                    print(f"DEBUG: row {i} sgRNA_type: {row.get('sgRNA_type', 'N/A')}, sgRNA_family: {row.get('sgRNA_family', 'N/A')}")

            print(f"DEBUG: 从 sgRNA 结果中提取的规范化基因 ID 数量: {len(gene_ids)}")
            if gene_ids:
                print(f"DEBUG: 基因 ID 示例: {list(gene_ids)[:5]}")

            if not gene_ids:
                print(f"DEBUG: gene_ids为空，创建空的基因GFF文件")
                with open(gene_gff_path, "w", encoding="utf-8") as f:
                    f.write("##gff-version 3\n")
                return

            gff_path = os.path.join(settings.BASE_DIR, 'database', 'TargetGenomeGff', name_db, f'{name_db}.gff')
            print(f"DEBUG: 尝试从 {gff_path} 提取基因信息")
            print(f"DEBUG: gff_path是否存在: {os.path.exists(gff_path)}")

            if not os.path.exists(gff_path):
                print(f"DEBUG: 错误！GFF文件不存在: {gff_path}")
                with open(gene_gff_path, "w", encoding="utf-8") as f:
                    f.write("##gff-version 3\n")
                return

            gene_records_df = self._extract_genes_from_gff(gff_path, gene_ids)
            print(f"DEBUG: _extract_genes_from_gff返回了 {len(gene_records_df)} 条记录")

            print(f"DEBUG: 开始写入基因GFF文件...")
            with open(gene_gff_path, "w", encoding="utf-8") as gff_file:
                gff_file.write("##gff-version 3\n")

                if not gene_records_df.empty:
                    for _, row in gene_records_df.iterrows():
                        line = f"{row['seqid']}\t{row['source']}\t{row['featuretype']}\t{row['start']}\t{row['end']}\t{row['score']}\t{row['strand']}\t{row['phase']}\t{row['attributes']}\n"
                        gff_file.write(line)

            print(f"DEBUG: 基因GFF文件写入完成，验证文件...")
            if os.path.exists(gene_gff_path):
                with open(gene_gff_path, "r") as f:
                    lines = f.readlines()
                print(f"DEBUG: 基因GFF文件存在，行数: {len(lines)}")
            else:
                print(f"DEBUG: 错误！基因GFF文件写入失败")

            subprocess.run([
                "sort", "-t", "\t", "-k1,1", "-k4,4n", gene_gff_path, "-o", gene_gff_path
            ], check=True)

        except Exception as e:
            print(f"生成基因 GFF 文件时发生错误：{str(e)}")
            import traceback
            traceback.print_exc()
            with open(gene_gff_path, "w", encoding="utf-8") as f:
                f.write("##gff-version 3\n")
    
    def _extract_genes_from_gff(self, gff_path, gene_ids):
        """
        从 GFF 文件中提取指定基因 ID 的完整信息（支持多种ID格式和子特征ID反推）
        
        参数:
            gff_path: GFF 文件路径
            gene_ids: 需要提取的基因 ID 集合
        
        返回:
            包含原始 GFF 行的 DataFrame
        """
        import pandas as pd
        
        try:
            gff_df = pd.read_csv(
                gff_path,
                sep='\t',
                comment='#',
                header=None,
                names=['seqid', 'source', 'featuretype', 'start', 'end', 'score', 'strand', 'phase', 'attributes'],
                dtype={'seqid': str, 'start': int, 'end': int}
            )
            
            def parse_attributes(attr_str):
                attrs = {}
                for item in str(attr_str).split(';'):
                    if '=' in item:
                        key, value = item.split('=', 1)
                        attrs[key.strip()] = value.strip()
                return attrs
            
            gff_df['parsed_attrs'] = gff_df['attributes'].apply(parse_attributes)
            gff_df['ID'] = gff_df['parsed_attrs'].apply(lambda x: x.get('ID', ''))
            gff_df['Parent'] = gff_df['parsed_attrs'].apply(lambda x: x.get('Parent', ''))
            
            id_to_indices = {}
            for idx, row in gff_df.iterrows():
                if row['ID']:
                    id_to_indices[row['ID']] = idx
            
            normalized_id_map = {}
            for raw_id in id_to_indices.keys():
                norm_id = self._normalize_gene_id(raw_id)
                if norm_id and norm_id not in normalized_id_map:
                    normalized_id_map[norm_id] = []
                if norm_id:
                    normalized_id_map[norm_id].append(id_to_indices[raw_id])
            
            print(f"DEBUG: GFF 文件中共有 {len(id_to_indices)} 个唯一 ID")
            print(f"DEBUG: 规范化后有 {len(normalized_id_map)} 个唯一 ID")
            
            target_indices = set()
            found_genes = set()
            not_found_genes = set()
            
            for gene_id in gene_ids:
                norm_gene_id = self._normalize_gene_id(gene_id)
                found = False
                
                if norm_gene_id in normalized_id_map:
                    for idx in normalized_id_map[norm_gene_id]:
                        row = gff_df.loc[idx]
                        if row['featuretype'] == 'gene':
                            target_indices.add(idx)
                            self._add_child_features(gff_df, row['ID'], target_indices, id_to_indices)
                            found_genes.add(gene_id)
                            found = True
                            print(f"DEBUG: 通过规范化ID匹配找到基因 {gene_id} -> {row['ID']}")
                            break
                
                if found:
                    continue
                
                if gene_id in id_to_indices:
                    idx = id_to_indices[gene_id]
                    row = gff_df.loc[idx]
                    if row['featuretype'] == 'gene':
                        target_indices.add(idx)
                        self._add_child_features(gff_df, row['ID'], target_indices, id_to_indices)
                        found_genes.add(gene_id)
                        found = True
                        print(f"DEBUG: 通过精确匹配找到基因 {gene_id}")
                        continue
                
                if gene_id in id_to_indices:
                    idx = id_to_indices[gene_id]
                    parent_gene_id = self._find_parent_gene(gff_df, id_to_indices, gene_id)
                    if parent_gene_id:
                        gene_idx = id_to_indices.get(parent_gene_id)
                        if gene_idx is not None:
                            target_indices.add(gene_idx)
                            self._add_child_features(gff_df, parent_gene_id, target_indices, id_to_indices)
                            found_genes.add(gene_id)
                            found = True
                            print(f"DEBUG: 通过子特征ID反推找到基因 {gene_id} -> {parent_gene_id}")
                            continue
                
                if not found:
                    for raw_id, idx in id_to_indices.items():
                        if norm_gene_id and (norm_gene_id in raw_id or raw_id in norm_gene_id):
                            row = gff_df.loc[idx]
                            if row['featuretype'] == 'gene':
                                target_indices.add(idx)
                                self._add_child_features(gff_df, row['ID'], target_indices, id_to_indices)
                                found_genes.add(gene_id)
                                found = True
                                print(f"DEBUG: 通过模糊匹配找到基因 {gene_id} -> {raw_id}")
                                break
                
                if not found:
                    not_found_genes.add(gene_id)
            
            print(f"DEBUG: 找到的基因数量: {len(found_genes)}")
            print(f"DEBUG: 未找到的基因数量: {len(not_found_genes)}")
            if not_found_genes:
                print(f"DEBUG: 未找到的基因 ID 示例: {list(not_found_genes)[:5]}")
            
            if target_indices:
                result_df = gff_df.loc[list(target_indices)].copy()
                result_df = result_df.sort_values(['seqid', 'start'])
                return result_df
            else:
                return pd.DataFrame(columns=gff_df.columns[:-3])
                
        except Exception as e:
            print(f"从 GFF 提取基因信息时发生错误：{str(e)}")
            import traceback
            traceback.print_exc()
            return pd.DataFrame()
    
    def _normalize_gene_id(self, gene_id):
        """
        规范化基因ID，去除版本号和前缀
        
        参数:
            gene_id: 原始基因ID
        
        返回:
            规范化后的基因ID
        """
        if not gene_id:
            return None
        
        gene_id = str(gene_id).strip()
        
        if ':' in gene_id:
            gene_id = gene_id.split(':')[-1]
        
        parts = gene_id.rsplit('.', 1)
        if len(parts) > 1 and parts[-1].isdigit():
            gene_id = parts[0]
        
        return gene_id.strip()
    
    def _add_child_features(self, gff_df, parent_id, target_indices, id_to_indices):
        """
        递归添加所有子特征
        
        参数:
            gff_df: GFF DataFrame
            parent_id: 父特征ID
            target_indices: 目标行索引集合
            id_to_indices: ID到索引的映射
        """
        children = gff_df[gff_df['Parent'] == parent_id]
        for idx in children.index:
            target_indices.add(idx)
            child_id = gff_df.loc[idx, 'ID']
            if child_id:
                self._add_child_features(gff_df, child_id, target_indices, id_to_indices)
    
    def _find_parent_gene(self, gff_df, id_to_indices, feature_id, visited=None):
        """
        通过Parent关系向上查找gene ID
        
        参数:
            gff_df: GFF DataFrame
            id_to_indices: ID到索引的映射
            feature_id: 起始特征ID
            visited: 已访问的ID集合（防止循环）
        
        返回:
            找到的gene ID，如果没找到返回None
        """
        if visited is None:
            visited = set()
        
        if feature_id in visited:
            return None
        visited.add(feature_id)
        
        if feature_id not in id_to_indices:
            return None
        
        row = gff_df.loc[id_to_indices[feature_id]]
        parent_id = row['Parent']
        
        if not parent_id:
            return None
        
        if parent_id in id_to_indices:
            parent_row = gff_df.loc[id_to_indices[parent_id]]
            if parent_row['featuretype'] == 'gene':
                return parent_id
            else:
                return self._find_parent_gene(gff_df, id_to_indices, parent_id, visited)
        
        return None


class Cas12bExecuteView(APIView):
    """
    Cas12b执行
    """

    def get(self, request):
        """
        GET请求：通过 task_id 查询已有任务结果
        """
        task_id = request.query_params.get('task_id')
            
        if not task_id:
            return Response({"msg": "缺少 task_id 参数"}, status=status.HTTP_400_BAD_REQUEST)
            
        return self._query_existing_task_b(task_id)
    
    def post(self, request):
        # 检查是否提供了 task_id 参数（用于直接访问已有结果）
        task_id = request.data.get('task_id') or request.query_params.get('task_id')
        
        if task_id:
            # 直接查询已有任务结果
            return self._query_existing_task_b(task_id)
        
        # 否则执行原有的新任务创建逻辑
        inputSequence = request.data.get('inputSequence')
        pam = request.data.get('pam', 'TTN')
        spacerLength = request.data.get('spacerLength', 20)
        sgRNAModule = request.data.get('sgRNAModule', 'spacerpam')
        name_db = request.data.get('name_db')
        
        # 使用全局锁防止同一参数的并发执行
        task_key = f"{inputSequence}_{pam}_{spacerLength}_{sgRNAModule}_{name_db}"
        with _task_execution_lock:
            return self._process_request_b(inputSequence, pam, spacerLength, sgRNAModule, name_db)

    def _process_request_b(self, inputSequence, pam, spacerLength, sgRNAModule, name_db):
        """
        实际处理CAS12b请求的核心逻辑
        包含完整的任务重试和并发处理逻辑
        """
        # 检查数据库中是否已存在相同且已完成的任务
        result_cas12b = models.result_cas12b_list.objects.filter(input_sequence=inputSequence,
                                                                 pam_type=pam,
                                                                 spacer_length=spacerLength,
                                                                 sgRNA_module=sgRNAModule,
                                                                 name_db=name_db,
                                                                 task_status="finished",
                                                                 sgRNA_with_JBrowse_json__isnull=False)
        if result_cas12b.first():
            task_record = result_cas12b.first()
            # 从文件中读取结果数据
            result_file_path = os.path.join(settings.BASE_DIR, task_record.sgRNA_with_JBrowse_json)
            if os.path.exists(result_file_path):
                with open(result_file_path, 'r') as f:
                    response_data = json.load(f)
                # 添加原始输入参数到返回结果
                response_data['inputSequence'] = task_record.input_sequence
                response_data['pam'] = task_record.pam_type
                response_data['spacerLength'] = task_record.spacer_length
                response_data['sgRNAModule'] = task_record.sgRNA_module
                response_data['name_db'] = task_record.name_db
                return Response(response_data, status=status.HTTP_200_OK)
            else:
                # 如果文件不存在，清除记录并重新处理
                self._safe_cleanup_task_b(task_record.task_id)

        # 检查是否已存在相同但失败的任务
        failed_cas12b = models.result_cas12b_list.objects.filter(input_sequence=inputSequence,
                                                                 pam_type=pam,
                                                                 spacer_length=spacerLength,
                                                                 sgRNA_module=sgRNAModule,
                                                                 name_db=name_db,
                                                                 task_status="failed")
        if failed_cas12b.exists():
            # 生成任务标识符用于重试计数
            task_identifier = f"cas12b_{inputSequence}_{pam}_{spacerLength}_{sgRNAModule}_{name_db}"
            
            # 从Redis获取重试次数
            retry_count = cache.get(task_identifier, 0)
            
            # 如果超过最大重试次数，返回错误信息而不是无限重试
            if retry_count >= MAX_RETRY_COUNT:
                return Response({
                    "msg": f"任务已达到最大重试次数({MAX_RETRY_COUNT}次)，请检查输入参数或联系管理员",
                    "error": "重试次数超限",
                    "retry_count": retry_count,
                    "max_retries": MAX_RETRY_COUNT
                }, status=status.HTTP_429_TOO_MANY_REQUESTS)
            
            # 增加重试计数
            cache.set(task_identifier, retry_count + 1, timeout=RETRY_CACHE_TIMEOUT)
            
            # 自动清理所有失败的任务并重新执行
            for failed_task in failed_cas12b:
                old_task_id = failed_task.task_id
                # 安全清理旧任务
                cleanup_result = self._safe_cleanup_task_b(old_task_id)
                if not cleanup_result['success']:
                    return Response({
                        "msg": "清理旧任务失败",
                        "error": cleanup_result['error']
                    }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
            
            # 继续执行下面的新任务创建逻辑

        # 检查是否已存在正在进行中或等待中的任务
        existing_cas12b = models.result_cas12b_list.objects.filter(input_sequence=inputSequence,
                                                                   pam_type=pam,
                                                                   spacer_length=spacerLength,
                                                                   sgRNA_module=sgRNAModule,
                                                                   name_db=name_db)
        pending_or_running_cas12b = existing_cas12b.filter(task_status__in=['pending', 'running'])
        if pending_or_running_cas12b.exists():
            # 返回最新的任务状态信息
            latest_task = pending_or_running_cas12b.latest('submit_time')
            # 简单估算：假设任务需要5分钟完成
            estimated_completion = latest_task.submit_time + timedelta(minutes=5)
            now = timezone.now()
            remaining_time = estimated_completion - now if estimated_completion > now else timedelta(minutes=1)
            
            return Response({
                "msg": "任务正在分析中",
                "task_id": latest_task.task_id,
                "status": latest_task.task_status,
                "estimated_completion": estimated_completion.isoformat(),
                "remaining_time_seconds": remaining_time.total_seconds(),
                "retry_info": self._get_retry_info_b(inputSequence, pam, spacerLength, sgRNAModule, name_db)
            }, status=status.HTTP_202_ACCEPTED)

        # 验证参数
        try:
            spacerLength = int(spacerLength)
        except (ValueError, TypeError):
            return Response({"msg": "请输入正确的范围"}, status=status.HTTP_400_BAD_REQUEST)

        # 创建新任务
        task_id = str(uuid.uuid4())
        
        # 创建任务记录
        cas12b_task_record = models.result_cas12b_list(
            task_id=task_id,
            input_sequence=inputSequence,
            pam_type=pam,
            spacer_length=spacerLength,
            sgRNA_module=sgRNAModule,
            name_db=name_db,
            task_status='pending'
        )
        cas12b_task_record.save()

        # 异步执行任务
        run_cas12b_analysis.delay(task_id, inputSequence, pam, spacerLength, sgRNAModule, name_db)
        
        # 返回任务已提交的信息
        return Response({
            "msg": "任务已提交",
            "task_id": task_id,
            "status": "pending",
            "retry_info": self._get_retry_info_b(inputSequence, pam, spacerLength, sgRNAModule, name_db)
        }, status=status.HTTP_202_ACCEPTED)

    def _safe_cleanup_task_b(self, task_id):
        """
        安全地清理CAS12b任务相关的数据库记录和文件
        使用锁防止并发问题
        """
        with _task_cleanup_lock:
            try:
                # 删除数据库记录
                try:
                    task_record = models.result_cas12b_list.objects.get(task_id=task_id)
                    task_record.delete()
                except models.result_cas12b_list.DoesNotExist:
                    # 记录不存在，但继续尝试清理文件
                    pass
                
                # 删除工作目录
                task_work_dir = os.path.join(settings.BASE_DIR, 'work', 'cas12bTasks', task_id)
                task_tmp_dir = os.path.join(settings.BASE_DIR, 'work', 'cas12bTmp', task_id)
                
                # 安全删除目录
                for dir_path in [task_work_dir, task_tmp_dir]:
                    if os.path.exists(dir_path):
                        try:
                            shutil.rmtree(dir_path)
                        except Exception as e:
                            # 记录警告但不中断流程
                            print(f"警告：删除目录 {dir_path} 失败: {str(e)}")
                
                return {'success': True}
                
            except Exception as e:
                return {'success': False, 'error': str(e)}

    def _cleanup_orphaned_data_b(self, input_sequence, pam_type, spacer_length, sgRNA_module, name_db):
        """
        清理CAS12b孤立的数据（有记录但无文件，或有文件但无记录）
        """
        with _task_cleanup_lock:
            # 查找可能存在的孤立记录
            orphaned_records = models.result_cas12b_list.objects.filter(
                input_sequence=input_sequence,
                pam_type=pam_type,
                spacer_length=spacer_length,
                sgRNA_module=sgRNA_module,
                name_db=name_db
            )
            
            for record in orphaned_records:
                # 检查文件是否存在
                if record.sgRNA_with_JBrowse_json and record.task_status == 'finished':
                    result_file_path = os.path.join(settings.BASE_DIR, record.sgRNA_with_JBrowse_json)
                    if not os.path.exists(result_file_path):
                        # 文件不存在，删除记录
                        print(f"清理孤立记录: {record.task_id}")
                        record.delete()
                        continue
                
                # 检查工作目录是否存在
                task_work_dir = os.path.join(settings.BASE_DIR, 'work', 'cas12bTasks', record.task_id)
                task_tmp_dir = os.path.join(settings.BASE_DIR, 'work', 'cas12bTmp', record.task_id)
                
                if not os.path.exists(task_work_dir) and not os.path.exists(task_tmp_dir):
                    # 目录都不存在，可能是孤立记录
                    if record.task_status in ['pending', 'running']:
                        # 对于未完成的任务，如果目录不存在则标记为失败
                        record.task_status = 'failed'
                        record.log = '任务目录丢失，可能被意外删除'
                        record.save()
                        print(f"标记丢失任务为失败: {record.task_id}")
                    elif record.task_status == 'failed':
                        # 失败任务且目录不存在，可以安全删除记录
                        print(f"清理失败任务记录: {record.task_id}")
                        record.delete()

    def _get_retry_info_b(self, input_sequence, pam_type, spacer_length, sgRNA_module, name_db):
        """
        获取CAS12b重试相关信息
        """
        task_identifier = f"cas12b_{input_sequence}_{pam_type}_{spacer_length}_{sgRNA_module}_{name_db}"
        retry_count = cache.get(task_identifier, 0)
        return {
            "retry_count": retry_count,
            "max_retries": MAX_RETRY_COUNT,
            "can_retry": retry_count < MAX_RETRY_COUNT
        }
    
    def _query_existing_task_b(self, task_id):
        """
        查询已有CAS12b任务的结果
        """
        try:
            # 查询任务记录
            task_record = models.result_cas12b_list.objects.get(task_id=task_id)
            
            # 检查任务状态
            if task_record.task_status == 'failed':
                return Response({
                    "msg": "任务执行失败",
                    "error": task_record.log or "未知错误"
                }, status=status.HTTP_400_BAD_REQUEST)
            
            if task_record.task_status == 'pending' or task_record.task_status == 'running':
                return Response({
                    "msg": "任务正在运行中",
                    "task_id": task_id,
                    "status": task_record.task_status
                }, status=status.HTTP_202_ACCEPTED)
            
            if task_record.task_status != 'finished':
                return Response({
                    "msg": "任务状态未知",
                    "status": task_record.task_status
                }, status=status.HTTP_400_BAD_REQUEST)
            
            # 检查结果文件是否存在
            if not task_record.sgRNA_with_JBrowse_json:
                return Response({
                    "msg": "任务结果不存在"
                }, status=status.HTTP_404_NOT_FOUND)
            
            result_file_path = os.path.join(settings.BASE_DIR, task_record.sgRNA_with_JBrowse_json)
            if not os.path.exists(result_file_path):
                return Response({
                    "msg": "结果文件丢失"
                }, status=status.HTTP_404_NOT_FOUND)
            
            # 返回结果
            with open(result_file_path, 'r') as f:
                response_data = json.load(f)

            # 添加原始输入参数到返回结果
            response_data['inputSequence'] = task_record.input_sequence
            response_data['pam'] = task_record.pam_type
            response_data['spacerLength'] = task_record.spacer_length
            response_data['sgRNAModule'] = task_record.sgRNA_module
            response_data['name_db'] = task_record.name_db

            return Response(response_data, status=status.HTTP_200_OK)

        except models.result_cas12b_list.DoesNotExist:
            return Response({
                "msg": "任务不存在"
            }, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            return Response({
                "msg": f"查询失败：{str(e)}"
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class Cas12bJbrowseAPI(APIView):
    def get(self, request):
        task_id = request.query_params.get('task_id')
        file_type = request.query_params.get('file_type')

        if not task_id or not file_type:
            return HttpResponseNotFound("Missing parameters")

        tmp_dir = os.path.join(settings.BASE_DIR, "work", "cas12bTmp", task_id)
        os.makedirs(tmp_dir, exist_ok=True)

        try:
            cas12b_task_record = models.result_cas12b_list.objects.get(task_id=task_id)
        except models.result_cas12b_list.DoesNotExist:
            return HttpResponseNotFound("Task not found")

        fa = os.path.join(settings.BASE_DIR, f"database/TargetGenome/{cas12b_task_record.name_db}/{cas12b_task_record.name_db}.fa")
        fai = fa + ".fai"
        gff = os.path.join(tmp_dir, f"{cas12b_task_record.name_db}_{task_id}_sgRNA.gff3")
        gff_gz = gff + ".gz"
        gff_csi = gff_gz + ".csi"
        gff_tbi = gff_gz + ".tbi"
        
        # 基因轨道文件路径
        gene_gff = os.path.join(tmp_dir, f"{cas12b_task_record.name_db}_{task_id}_genes.gff3")
        gene_gff_gz = gene_gff + ".gz"
        gene_gff_csi = gene_gff_gz + ".csi"

        file_paths = {
            "fa": fa,
            "fai": fai,
            "gff3.gz": gff_gz,
            "gff3.gz.csi": gff_csi,
            "genes.gff3.gz": gene_gff_gz,
            "genes.gff3.gz.csi": gene_gff_csi,
        }

        # 用户请求非法类型
        if file_type not in file_paths:
            return HttpResponseNotFound("Invalid file type")

        # ========== 如果 gff_gz 和 index 都存在，直接返回 ==========
        if file_type == "gff3.gz.csi":
            if os.path.exists(gff_csi):
                return FileResponse(open(gff_csi, "rb"))
            if os.path.exists(gff_tbi):
                return FileResponse(open(gff_tbi, "rb"))
        elif file_type == "genes.gff3.gz.csi":
            # 基因轨道索引文件
            if os.path.exists(gene_gff_csi):
                return FileResponse(open(gene_gff_csi, "rb"))
            return HttpResponseNotFound("Gene track index file missing")
        elif file_type == "genes.gff3.gz":
            # 基因轨道 GFF 文件
            if all(os.path.exists(p) for p in [gene_gff_gz]):
                return FileResponse(open(gene_gff_gz, "rb"))
            # 如果需要生成，继续执行下面的逻辑
        else:
            if all(os.path.exists(p) for p in [fa, fai, gff_gz]) and (os.path.exists(gff_csi) or os.path.exists(gff_tbi)):
                return FileResponse(open(file_paths[file_type], "rb"))

        # ========== 生成 sgRNA GFF ==========
        result_file = os.path.join(settings.BASE_DIR, cas12b_task_record.sgRNA_with_JBrowse_json)
        if not os.path.exists(result_file):
            return HttpResponseNotFound("sgRNA result file missing")

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

        # ========== 排序 ==========
        try:
            subprocess.run([
                "sort", "-t", "\t", "-k1,1", "-k4,4n", gff, "-o", gff
            ], check=True)

            subprocess.run([f"{settings.CONDA_ENV_BIN_PATH}/bgzip", "-f", gff], check=True)
            subprocess.run([f"{settings.CONDA_ENV_BIN_PATH}/tabix", "-p", "gff", "-C", gff_gz], check=True)
        except subprocess.CalledProcessError:
            return HttpResponse("Error generating gff3.gz and index", status=500)

        # ========== 返回文件：csi 优先，没有用 tbi ==========
        if file_type == "gff3.gz.csi":
            if os.path.exists(gff_csi):
                return FileResponse(open(gff_csi, "rb"))
            if os.path.exists(gff_tbi):
                return FileResponse(open(gff_tbi, "rb"))
            return HttpResponseNotFound("Index file missing (.csi and .tbi)")
        elif file_type == "genes.gff3.gz.csi":
            # 基因轨道索引文件 - 异步生成，只返回已存在的文件
            if os.path.exists(gene_gff_csi):
                return FileResponse(open(gene_gff_csi, "rb"))
            return HttpResponseNotFound("Gene track index file not ready")
        elif file_type == "genes.gff3.gz":
            # 基因轨道 GFF 文件 - 异步生成，只返回已存在的文件
            if os.path.exists(gene_gff_gz):
                return FileResponse(open(gene_gff_gz, "rb"))
            return HttpResponseNotFound("Gene track file not ready")
        else:
            if os.path.exists(file_paths[file_type]):
                return FileResponse(open(file_paths[file_type], "rb"))
            return HttpResponseNotFound("File generation failed")
    
    def _generate_gene_gff(self, result_file, gene_gff_path, name_db):
        """
        从 sgRNA 结果文件中提取基因信息，并生成基因 GFF 文件（保持原始格式）
        
        参数:
            result_file: sgRNA 结果 JSON 文件路径
            gene_gff_path: 输出的基因 GFF 文件路径
            name_db: 数据库名称
        """
        try:
            print(f"DEBUG: 开始生成基因GFF文件")
            print(f"DEBUG: result_file = {result_file}")
            print(f"DEBUG: gene_gff_path = {gene_gff_path}")
            print(f"DEBUG: name_db = {name_db}")

            with open(result_file, "r") as f:
                data = json.load(f)

            print(f"DEBUG: result_file读取成功，data的顶层keys: {list(data.keys())}")

            gene_ids = set()

            if "TableData" not in data:
                print(f"DEBUG: 错误！data中没有TableData键")
                with open(gene_gff_path, "w", encoding="utf-8") as f:
                    f.write("##gff-version 3\n")
                return

            table_data = data.get("TableData", {})
            print(f"DEBUG: TableData的keys: {list(table_data.keys()) if isinstance(table_data, dict) else type(table_data)}")

            json_data = table_data.get("json_data", {})
            print(f"DEBUG: json_data的keys: {list(json_data.keys()) if isinstance(json_data, dict) else type(json_data)}")

            rows = json_data.get("rows", [])
            print(f"DEBUG: rows数量: {len(rows)}")

            if len(rows) == 0:
                print(f"DEBUG: 警告！rows为空")
                with open(gene_gff_path, "w", encoding="utf-8") as f:
                    f.write("##gff-version 3\n")
                return

            for i, row in enumerate(rows):
                gene_id_from_type = None
                if 'sgRNA_type' in row and row['sgRNA_type']:
                    type_value = row['sgRNA_type']
                    if isinstance(type_value, str):
                        parts = [p.strip() for p in type_value.split(',')]
                        for part in parts:
                            if part.startswith('gene-'):
                                gene_id = part.split('-')[1]
                                gene_id = self._normalize_gene_id(gene_id)
                                if gene_id:
                                    gene_ids.add(gene_id)
                                break
                        if not gene_id_from_type and parts:
                            first_part = parts[0]
                            if first_part:
                                gene_id_from_type = self._normalize_gene_id(first_part)
                                if gene_id_from_type:
                                    gene_ids.add(gene_id_from_type)

                elif 'sgRNA_family' in row and row['sgRNA_family']:
                    family_value = row['sgRNA_family']
                    if isinstance(family_value, str):
                        for gene_id in family_value.split(','):
                            gene_id = gene_id.strip()
                            if gene_id:
                                normalized_id = self._normalize_gene_id(gene_id)
                                if normalized_id:
                                    gene_ids.add(normalized_id)
                    elif isinstance(family_value, (list, tuple)):
                        for gene_id in family_value:
                            if gene_id:
                                normalized_id = self._normalize_gene_id(str(gene_id))
                                if normalized_id:
                                    gene_ids.add(normalized_id)

                if i < 3:
                    print(f"DEBUG: row {i} sgRNA_type: {row.get('sgRNA_type', 'N/A')}, sgRNA_family: {row.get('sgRNA_family', 'N/A')}")

            print(f"DEBUG: 从 sgRNA 结果中提取的规范化基因 ID 数量: {len(gene_ids)}")
            if gene_ids:
                print(f"DEBUG: 基因 ID 示例: {list(gene_ids)[:5]}")

            if not gene_ids:
                print(f"DEBUG: gene_ids为空，创建空的基因GFF文件")
                with open(gene_gff_path, "w", encoding="utf-8") as f:
                    f.write("##gff-version 3\n")
                return

            gff_path = os.path.join(settings.BASE_DIR, 'database', 'TargetGenomeGff', name_db, f'{name_db}.gff')
            print(f"DEBUG: 尝试从 {gff_path} 提取基因信息")
            print(f"DEBUG: gff_path是否存在: {os.path.exists(gff_path)}")

            if not os.path.exists(gff_path):
                print(f"DEBUG: 错误！GFF文件不存在: {gff_path}")
                with open(gene_gff_path, "w", encoding="utf-8") as f:
                    f.write("##gff-version 3\n")
                return

            gene_records_df = self._extract_genes_from_gff(gff_path, gene_ids)
            print(f"DEBUG: _extract_genes_from_gff返回了 {len(gene_records_df)} 条记录")

            print(f"DEBUG: 开始写入基因GFF文件...")
            with open(gene_gff_path, "w", encoding="utf-8") as gff_file:
                gff_file.write("##gff-version 3\n")

                if not gene_records_df.empty:
                    for _, row in gene_records_df.iterrows():
                        line = f"{row['seqid']}\t{row['source']}\t{row['featuretype']}\t{row['start']}\t{row['end']}\t{row['score']}\t{row['strand']}\t{row['phase']}\t{row['attributes']}\n"
                        gff_file.write(line)

            print(f"DEBUG: 基因GFF文件写入完成，验证文件...")
            if os.path.exists(gene_gff_path):
                with open(gene_gff_path, "r") as f:
                    lines = f.readlines()
                print(f"DEBUG: 基因GFF文件存在，行数: {len(lines)}")
            else:
                print(f"DEBUG: 错误！基因GFF文件写入失败")

            subprocess.run([
                "sort", "-t", "\t", "-k1,1", "-k4,4n", gene_gff_path, "-o", gene_gff_path
            ], check=True)

        except Exception as e:
            print(f"生成基因 GFF 文件时发生错误：{str(e)}")
            import traceback
            traceback.print_exc()
            with open(gene_gff_path, "w", encoding="utf-8") as f:
                f.write("##gff-version 3\n")
    
    def _extract_genes_from_gff(self, gff_path, gene_ids):
        """
        从 GFF 文件中提取指定基因 ID 的完整信息（支持多种ID格式和子特征ID反推）
        
        参数:
            gff_path: GFF 文件路径
            gene_ids: 需要提取的基因 ID 集合
        
        返回:
            包含原始 GFF 行的 DataFrame
        """
        import pandas as pd
        
        try:
            gff_df = pd.read_csv(
                gff_path,
                sep='\t',
                comment='#',
                header=None,
                names=['seqid', 'source', 'featuretype', 'start', 'end', 'score', 'strand', 'phase', 'attributes'],
                dtype={'seqid': str, 'start': int, 'end': int}
            )
            
            def parse_attributes(attr_str):
                attrs = {}
                for item in str(attr_str).split(';'):
                    if '=' in item:
                        key, value = item.split('=', 1)
                        attrs[key.strip()] = value.strip()
                return attrs
            
            gff_df['parsed_attrs'] = gff_df['attributes'].apply(parse_attributes)
            gff_df['ID'] = gff_df['parsed_attrs'].apply(lambda x: x.get('ID', ''))
            gff_df['Parent'] = gff_df['parsed_attrs'].apply(lambda x: x.get('Parent', ''))
            
            id_to_indices = {}
            for idx, row in gff_df.iterrows():
                if row['ID']:
                    id_to_indices[row['ID']] = idx
            
            normalized_id_map = {}
            for raw_id in id_to_indices.keys():
                norm_id = self._normalize_gene_id(raw_id)
                if norm_id and norm_id not in normalized_id_map:
                    normalized_id_map[norm_id] = []
                if norm_id:
                    normalized_id_map[norm_id].append(id_to_indices[raw_id])
            
            print(f"DEBUG: GFF 文件中共有 {len(id_to_indices)} 个唯一 ID")
            print(f"DEBUG: 规范化后有 {len(normalized_id_map)} 个唯一 ID")
            
            target_indices = set()
            found_genes = set()
            not_found_genes = set()
            
            for gene_id in gene_ids:
                norm_gene_id = self._normalize_gene_id(gene_id)
                found = False
                
                if norm_gene_id in normalized_id_map:
                    for idx in normalized_id_map[norm_gene_id]:
                        row = gff_df.loc[idx]
                        if row['featuretype'] == 'gene':
                            target_indices.add(idx)
                            self._add_child_features(gff_df, row['ID'], target_indices, id_to_indices)
                            found_genes.add(gene_id)
                            found = True
                            print(f"DEBUG: 通过规范化ID匹配找到基因 {gene_id} -> {row['ID']}")
                            break
                
                if found:
                    continue
                
                if gene_id in id_to_indices:
                    idx = id_to_indices[gene_id]
                    row = gff_df.loc[idx]
                    if row['featuretype'] == 'gene':
                        target_indices.add(idx)
                        self._add_child_features(gff_df, row['ID'], target_indices, id_to_indices)
                        found_genes.add(gene_id)
                        found = True
                        print(f"DEBUG: 通过精确匹配找到基因 {gene_id}")
                        continue
                
                if gene_id in id_to_indices:
                    idx = id_to_indices[gene_id]
                    parent_gene_id = self._find_parent_gene(gff_df, id_to_indices, gene_id)
                    if parent_gene_id:
                        gene_idx = id_to_indices.get(parent_gene_id)
                        if gene_idx is not None:
                            target_indices.add(gene_idx)
                            self._add_child_features(gff_df, parent_gene_id, target_indices, id_to_indices)
                            found_genes.add(gene_id)
                            found = True
                            print(f"DEBUG: 通过子特征ID反推找到基因 {gene_id} -> {parent_gene_id}")
                            continue
                
                if not found:
                    for raw_id, idx in id_to_indices.items():
                        if norm_gene_id and (norm_gene_id in raw_id or raw_id in norm_gene_id):
                            row = gff_df.loc[idx]
                            if row['featuretype'] == 'gene':
                                target_indices.add(idx)
                                self._add_child_features(gff_df, row['ID'], target_indices, id_to_indices)
                                found_genes.add(gene_id)
                                found = True
                                print(f"DEBUG: 通过模糊匹配找到基因 {gene_id} -> {raw_id}")
                                break
                
                if not found:
                    not_found_genes.add(gene_id)
            
            print(f"DEBUG: 找到的基因数量: {len(found_genes)}")
            print(f"DEBUG: 未找到的基因数量: {len(not_found_genes)}")
            if not_found_genes:
                print(f"DEBUG: 未找到的基因 ID 示例: {list(not_found_genes)[:5]}")
            
            if target_indices:
                result_df = gff_df.loc[list(target_indices)].copy()
                result_df = result_df.sort_values(['seqid', 'start'])
                return result_df
            else:
                return pd.DataFrame(columns=gff_df.columns[:-3])
                
        except Exception as e:
            print(f"从 GFF 提取基因信息时发生错误：{str(e)}")
            import traceback
            traceback.print_exc()
            return pd.DataFrame()
    
    def _normalize_gene_id(self, gene_id):
        """
        规范化基因ID，去除版本号和前缀
        
        参数:
            gene_id: 原始基因ID
        
        返回:
            规范化后的基因ID
        """
        if not gene_id:
            return None
        
        gene_id = str(gene_id).strip()
        
        if ':' in gene_id:
            gene_id = gene_id.split(':')[-1]
        
        parts = gene_id.rsplit('.', 1)
        if len(parts) > 1 and parts[-1].isdigit():
            gene_id = parts[0]
        
        return gene_id.strip()
    
    def _add_child_features(self, gff_df, parent_id, target_indices, id_to_indices):
        """
        递归添加所有子特征
        
        参数:
            gff_df: GFF DataFrame
            parent_id: 父特征ID
            target_indices: 目标行索引集合
            id_to_indices: ID到索引的映射
        """
        children = gff_df[gff_df['Parent'] == parent_id]
        for idx in children.index:
            target_indices.add(idx)
            child_id = gff_df.loc[idx, 'ID']
            if child_id:
                self._add_child_features(gff_df, child_id, target_indices, id_to_indices)
    
    def _find_parent_gene(self, gff_df, id_to_indices, feature_id, visited=None):
        """
        通过Parent关系向上查找gene ID
        
        参数:
            gff_df: GFF DataFrame
            id_to_indices: ID到索引的映射
            feature_id: 起始特征ID
            visited: 已访问的ID集合（防止循环）
        
        返回:
            找到的gene ID，如果没找到返回None
        """
        if visited is None:
            visited = set()
        
        if feature_id in visited:
            return None
        visited.add(feature_id)
        
        if feature_id not in id_to_indices:
            return None
        
        row = gff_df.loc[id_to_indices[feature_id]]
        parent_id = row['Parent']
        
        if not parent_id:
            return None
        
        if parent_id in id_to_indices:
            parent_row = gff_df.loc[id_to_indices[parent_id]]
            if parent_row['featuretype'] == 'gene':
                return parent_id
            else:
                return self._find_parent_gene(gff_df, id_to_indices, parent_id, visited)
        
        return None