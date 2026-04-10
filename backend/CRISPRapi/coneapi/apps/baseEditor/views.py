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
from . import baseEditor, models
from .tasks import run_base_editor_analysis

# 用于并发控制的锁
_task_cleanup_lock = threading.Lock()
_task_execution_lock = threading.Lock()

# 重试限制配置
MAX_RETRY_COUNT = 3  # 最大重试次数
RETRY_CACHE_TIMEOUT = 3600  # 重试计数缓存时间（秒）

class BaseEditorExecuteView(APIView):
    """
    Base Editor执行
    """

    def get(self, request):
        """
        GET请求：通过 task_id 查询已有任务结果
        """
        task_id = request.query_params.get('task_id')
            
        if not task_id:
            return Response({"msg": "缺少 task_id 参数"}, status=status.HTTP_400_BAD_REQUEST)
            
        return self._query_existing_task(task_id)
    
    def post(self, request):
        # 检查是否提供了 task_id 参数（用于直接访问已有结果）
        task_id = request.data.get('task_id') or request.query_params.get('task_id')
        
        if task_id:
            # 直接查询已有任务结果
            return self._query_existing_task(task_id)
        
        # 否则执行原有的新任务创建逻辑
        inputSequence = request.data.get('inputSequence')
        pam = request.data.get('pam', 'NGG')
        spacerLength = request.data.get('spacerLength')
        sgRNAModule = request.data.get('sgRNAModule')
        name_db = request.data.get('name_db')
        # Base Editor 特有参数
        base_editor_type = request.data.get('type')  # ABE / CBE / GBE / ABE+CBE / TBE
        base_editing_window = request.data.get('base_editing_window')  # 格式如 "14-17"
        
        # 使用全局锁防止同一参数的并发执行
        task_key = f"{inputSequence}_{pam}_{spacerLength}_{sgRNAModule}_{name_db}_{base_editor_type}_{base_editing_window}"
        with _task_execution_lock:
            return self._process_request(inputSequence, pam, spacerLength, sgRNAModule, name_db, base_editor_type, base_editing_window)

    def _process_request(self, inputSequence, pam, spacerLength, sgRNAModule, name_db, base_editor_type, base_editing_window):
        """
        实际处理Base Editor请求的核心逻辑
        包含完整的任务重试和并发处理逻辑
        """
        # 验证Base Editor参数
        if not base_editor_type or base_editor_type not in ['ABE', 'CBE', 'GBE', 'ABE+CBE', 'TBE']:
            return Response({"msg": "请输入正确的Base Editor类型 (ABE / CBE / GBE / ABE+CBE / TBE)"}, 
                           status=status.HTTP_400_BAD_REQUEST)
        
        if not base_editing_window:
            return Response({"msg": "请输入编辑窗口范围 (格式如: 14-17)"}, 
                           status=status.HTTP_400_BAD_REQUEST)
        
        # 验证编辑窗口格式
        try:
            start, end = map(int, base_editing_window.split('-'))
            if not (10 <= start <= end <= 20):
                return Response({"msg": "编辑窗口范围必须在10-20之间，且起始值不大于结束值"}, 
                               status=status.HTTP_400_BAD_REQUEST)
        except ValueError:
            return Response({"msg": "编辑窗口格式不正确 (应为如: 14-17)"}, 
                           status=status.HTTP_400_BAD_REQUEST)

        # 检查数据库中是否已存在相同且已完成的任务
        result_base_editor = models.result_base_editor_list.objects.filter(
            input_sequence=inputSequence,
            pam_type=pam,
            spacer_length=spacerLength,
            sgRNA_module=sgRNAModule,
            name_db=name_db,
            base_editor_type=base_editor_type,
            base_editing_window=base_editing_window,
            task_status="finished",
            sgRNA_with_JBrowse_json__isnull=False)
        
        if result_base_editor.first():
            task_record = result_base_editor.first()
            # 从文件中读取结果数据
            result_file_path = os.path.join(settings.BASE_DIR, task_record.sgRNA_with_JBrowse_json)
            if os.path.exists(result_file_path):
                with open(result_file_path, 'r') as f:
                    response_data = json.load(f)
                return Response(response_data, status=status.HTTP_200_OK)
            else:
                # 如果文件不存在，清除记录并重新处理
                self._safe_cleanup_task(task_record.task_id)

        # 检查是否已存在相同但失败的任务
        failed_base_editor = models.result_base_editor_list.objects.filter(
            input_sequence=inputSequence,
            pam_type=pam,
            spacer_length=spacerLength,
            sgRNA_module=sgRNAModule,
            name_db=name_db,
            base_editor_type=base_editor_type,
            base_editing_window=base_editing_window,
            task_status="failed")
        
        if failed_base_editor.exists():
            # 生成任务标识符用于重试计数
            task_identifier = f"baseeditor_{inputSequence}_{pam}_{spacerLength}_{sgRNAModule}_{name_db}_{base_editor_type}_{base_editing_window}"
            
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
            for failed_task in failed_base_editor:
                old_task_id = failed_task.task_id
                # 安全清理旧任务
                cleanup_result = self._safe_cleanup_task(old_task_id)
                if not cleanup_result['success']:
                    return Response({
                        "msg": "清理旧任务失败",
                        "error": cleanup_result['error']
                    }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
            
            # 继续执行下面的新任务创建逻辑

        # 检查是否已存在正在进行中或等待中的任务
        existing_base_editor = models.result_base_editor_list.objects.filter(
            input_sequence=inputSequence,
            pam_type=pam,
            spacer_length=spacerLength,
            sgRNA_module=sgRNAModule,
            name_db=name_db,
            base_editor_type=base_editor_type,
            base_editing_window=base_editing_window)
        
        pending_or_running_base_editor = existing_base_editor.filter(task_status__in=['pending', 'running'])
        if pending_or_running_base_editor.exists():
            # 返回最新的任务状态信息
            latest_task = pending_or_running_base_editor.latest('submit_time')
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
                "retry_info": self._get_retry_info(inputSequence, pam, spacerLength, sgRNAModule, name_db, base_editor_type, base_editing_window)
            }, status=status.HTTP_202_ACCEPTED)

        # 在创建新任务前再次清理可能的孤立数据
        self._cleanup_orphaned_data(inputSequence, pam, spacerLength, sgRNAModule, name_db, base_editor_type, base_editing_window)

        # 验证参数
        try:
            spacerLength = int(spacerLength)
        except (ValueError, TypeError):
            return Response({"msg": "请输入正确的范围"}, status=status.HTTP_400_BAD_REQUEST)

        # 创建新任务
        task_id = str(uuid.uuid4())
        
        # 创建任务记录
        base_editor_task_record = models.result_base_editor_list(
            task_id=task_id,
            input_sequence=inputSequence,
            pam_type=pam,
            spacer_length=spacerLength,
            sgRNA_module=sgRNAModule,
            name_db=name_db,
            base_editor_type=base_editor_type,
            base_editing_window=base_editing_window,
            task_status='pending'
        )
        base_editor_task_record.save()

        # 异步执行任务
        run_base_editor_analysis.delay(task_id, inputSequence, pam, spacerLength, sgRNAModule, 
                                      name_db, base_editor_type, base_editing_window)
        
        # 返回任务已提交的信息
        return Response({
            "msg": "任务已提交",
            "task_id": task_id,
            "status": "pending",
            "retry_info": self._get_retry_info(inputSequence, pam, spacerLength, sgRNAModule, name_db, base_editor_type, base_editing_window)
        }, status=status.HTTP_202_ACCEPTED)
    def _safe_cleanup_task(self, task_id):
        """
        安全地清理任务相关的数据库记录和文件
        使用锁防止并发问题
        """
        with _task_cleanup_lock:
            try:
                # 删除数据库记录
                try:
                    task_record = models.result_base_editor_list.objects.get(task_id=task_id)
                    task_record.delete()
                except models.result_base_editor_list.DoesNotExist:
                    # 记录不存在，但继续尝试清理文件
                    pass
                
                # 删除工作目录
                task_work_dir = os.path.join(settings.BASE_DIR, 'work', 'baseEditorTasks', task_id)
                task_tmp_dir = os.path.join(settings.BASE_DIR, 'work', 'baseEditorTmp', task_id)
                
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

    def _cleanup_orphaned_data(self, input_sequence, pam_type, spacer_length, sgRNA_module, name_db, base_editor_type, base_editing_window):
        """
        清理孤立的数据（有记录但无文件，或有文件但无记录）
        """
        with _task_cleanup_lock:
            # 查找可能存在的孤立记录
            orphaned_records = models.result_base_editor_list.objects.filter(
                input_sequence=input_sequence,
                pam_type=pam_type,
                spacer_length=spacer_length,
                sgRNA_module=sgRNA_module,
                name_db=name_db,
                base_editor_type=base_editor_type,
                base_editing_window=base_editing_window
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
                task_work_dir = os.path.join(settings.BASE_DIR, 'work', 'baseEditorTasks', record.task_id)
                task_tmp_dir = os.path.join(settings.BASE_DIR, 'work', 'baseEditorTmp', record.task_id)
                
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

    def _get_retry_info(self, input_sequence, pam_type, spacer_length, sgRNA_module, name_db, base_editor_type, base_editing_window):
        """
        获取重试相关信息
        """
        task_identifier = f"baseeditor_{input_sequence}_{pam_type}_{spacer_length}_{sgRNA_module}_{name_db}_{base_editor_type}_{base_editing_window}"
        retry_count = cache.get(task_identifier, 0)
        return {
            "retry_count": retry_count,
            "max_retries": MAX_RETRY_COUNT,
            "can_retry": retry_count < MAX_RETRY_COUNT
        }
    
    def _query_existing_task(self, task_id):
        """
        查询已有任务的结果
        """
        try:
            # 查询任务记录
            task_record = models.result_base_editor_list.objects.get(task_id=task_id)
            
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
            
            return Response(response_data, status=status.HTTP_200_OK)
            
        except models.result_base_editor_list.DoesNotExist:
            return Response({
                "msg": "任务不存在"
            }, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            return Response({
                "msg": f"查询失败：{str(e)}"
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class BaseEditorJbrowseAPI(APIView):
    def get(self, request):
        task_id = request.query_params.get('task_id')
        file_type = request.query_params.get('file_type')

        if not task_id or not file_type:
            return HttpResponseNotFound("Missing parameters")

        tmp_dir = os.path.join(settings.BASE_DIR, "work", "baseEditorTmp", task_id)
        os.makedirs(tmp_dir, exist_ok=True)

        try:
            base_editor_task_record = models.result_base_editor_list.objects.get(task_id=task_id)
        except models.result_base_editor_list.DoesNotExist:
            return HttpResponseNotFound("Task not found")

        fa = os.path.join(settings.BASE_DIR, f"database/TargetGenome/{base_editor_task_record.name_db}/{base_editor_task_record.name_db}.fa")
        fai = fa + ".fai"
        gff = os.path.join(tmp_dir, f"{base_editor_task_record.name_db}_{task_id}_sgRNA.gff3")
        gff_gz = gff + ".gz"
        gff_csi = gff_gz + ".csi"
        gff_tbi = gff_gz + ".tbi"

        file_paths = {
            "fa": fa,
            "fai": fai,
            "gff3.gz": gff_gz,
            "gff3.gz.csi": gff_csi,
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
        else:
            if all(os.path.exists(p) for p in [fa, fai, gff_gz]) and (os.path.exists(gff_csi) or os.path.exists(gff_tbi)):
                return FileResponse(open(file_paths[file_type], "rb"))

        # ========== 生成 GFF ==========
        result_file = os.path.join(settings.BASE_DIR, base_editor_task_record.sgRNA_with_JBrowse_json)
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
        else:
            if os.path.exists(file_paths[file_type]):
                return FileResponse(open(file_paths[file_type], "rb"))
            return HttpResponseNotFound("File generation failed")
