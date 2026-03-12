import os
import uuid
import shutil
import threading

import pandas as pd
from django.conf import settings
from django.core.cache import cache
from django.http import FileResponse
from django.utils import timezone
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from .models import EditAnalysisFiles, EditAnalysisTasks
from .tasks import runEditAnalysis

# 用于并发控制的锁
_task_cleanup_lock = threading.Lock()
_task_execution_lock = threading.Lock()

# 重试限制配置
MAX_RETRY_COUNT = 3  # 最大重试次数
RETRY_CACHE_TIMEOUT = 3600  # 重试计数缓存时间（秒）


# 计算预估时间
def get_estimated_time(task):
    fq_file = EditAnalysisFiles.objects.filter(file_md5=task.fq_files_md5).first()
    target_file = EditAnalysisFiles.objects.filter(file_md5=task.target_file_md5).first()
    file_size_total = (fq_file.file_size if fq_file else 0) + (target_file.file_size if target_file else 0)

    base_size = 9380
    base_time = 25
    estimated_time = int(file_size_total / base_size * base_time) if file_size_total > 0 else None
    return estimated_time


# 接口1，任务执行
class EditAnalysisView(APIView):
    def post(self, request):
        fq_md5 = request.data.get("fq_files_md5")
        target_md5 = request.data.get("target_file_md5")
        start = request.data.get("start")
        end = request.data.get("end")

        if not fq_md5 or not target_md5 or start is None or end is None:
            return Response({"error": "Missing parameter"}, status=status.HTTP_400_BAD_REQUEST)

        # 使用全局锁防止同一参数的并发执行
        task_key = f"{fq_md5}_{target_md5}_{start}_{end}"
        with _task_execution_lock:
            return self._process_request(fq_md5, target_md5, start, end)

    def _process_request(self, fq_md5, target_md5, start, end):
        """
        实际处理请求的核心逻辑
        包含完整的任务重试和并发处理逻辑
        """
        # 检查数据库中是否已存在相同且已完成的任务
        completed_task = EditAnalysisTasks.objects.filter(
            fq_files_md5=fq_md5,
            target_file_md5=target_md5,
            start=start,
            end=end,
            status__in=["success", "partial_success"]
        ).first()
        
        if completed_task and completed_task.result_data:
            return Response({
                "status": completed_task.status,
                "task_id": str(completed_task.task_id),
                "data": completed_task.result_data,
                "message": "任务已完成，返回缓存结果"
            })

        # 检查是否已存在相同但失败的任务
        failed_tasks = EditAnalysisTasks.objects.filter(
            fq_files_md5=fq_md5,
            target_file_md5=target_md5,
            start=start,
            end=end,
            status="failure"
        )
        
        if failed_tasks.exists():
            # 生成任务标识符用于重试计数
            task_identifier = f"edit_{fq_md5}_{target_md5}_{start}_{end}"
            
            # 从Redis获取重试次数
            retry_count = cache.get(task_identifier, 0)
            
            # 如果超过最大重试次数，返回错误信息而不是无限重试
            if retry_count >= MAX_RETRY_COUNT:
                return Response({
                    "status": "failure",
                    "error": f"任务已达到最大重试次数({MAX_RETRY_COUNT}次)，请检查输入参数或联系管理员",
                    "retry_count": retry_count,
                    "max_retries": MAX_RETRY_COUNT
                }, status=status.HTTP_429_TOO_MANY_REQUESTS)
            
            # 增加重试计数
            cache.set(task_identifier, retry_count + 1, timeout=RETRY_CACHE_TIMEOUT)
            
            # 自动清理所有失败的任务并重新执行
            for failed_task in failed_tasks:
                old_task_id = str(failed_task.task_id)
                # 安全清理旧任务
                cleanup_result = self._safe_cleanup_task(old_task_id)
                if not cleanup_result['success']:
                    return Response({
                        "status": "failure",
                        "error": "清理旧任务失败",
                        "details": cleanup_result['error']
                    }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
            
            # 继续执行下面的新任务创建逻辑

        # 检查是否已存在正在进行中或等待中的任务
        existing_task = EditAnalysisTasks.objects.filter(
            fq_files_md5=fq_md5,
            target_file_md5=target_md5,
            start=start,
            end=end,
            status="analysis"
        ).first()
        
        if existing_task:
            # 返回任务状态信息
            return Response({
                "status": "analysis",
                "task_id": str(existing_task.task_id),
                "message": "任务正在分析中",
                "create_time": existing_task.create_time.isoformat(),
                "estimated_time": get_estimated_time(existing_task),
                "retry_info": self._get_retry_info(fq_md5, target_md5, start, end)
            }, status=status.HTTP_202_ACCEPTED)

        # 在创建新任务前清理可能的孤立数据
        self._cleanup_orphaned_data(fq_md5, target_md5, start, end)

        # 查文件
        fq_file = EditAnalysisFiles.objects.filter(file_md5=fq_md5).first()
        target_file = EditAnalysisFiles.objects.filter(file_md5=target_md5).first()

        if not fq_file and not target_file:
            return Response({"error": "fq 文件和 target 文件都不存在，需要上传"}, status=status.HTTP_400_BAD_REQUEST)
        if not fq_file:
            return Response({"error": "fq 文件不存在，需要上传"}, status=status.HTTP_400_BAD_REQUEST)
        if not target_file:
            return Response({"error": "target 文件不存在，需要上传"}, status=status.HTTP_400_BAD_REQUEST)

        # 创建任务
        task_id = uuid.uuid4()
        # 确保应用的顶层目录存在
        ea_base_dir = os.path.join(settings.BASE_DIR, 'work', 'editAnalysis')
        ea_tasks_dir = os.path.join(ea_base_dir, 'EA_tasks')
        ea_files_dir = os.path.join(ea_base_dir, 'EA_files')
        os.makedirs(ea_base_dir, exist_ok=True)
        os.makedirs(ea_tasks_dir, exist_ok=True)
        os.makedirs(ea_files_dir, exist_ok=True)
        
        task_dir = os.path.join(settings.BASE_DIR, f"work/editAnalysis/EA_tasks/{task_id}/")
        os.makedirs(task_dir, exist_ok=True)

        task = EditAnalysisTasks.objects.create(
            task_id=task_id,
            fq_files_md5=fq_md5,
            target_file_md5=target_md5,
            start=start,
            end=end,
            status="analysis"
        )

        fq_files_path = os.path.join(settings.BASE_DIR, f"work/editAnalysis/EA_files/{fq_md5}/{fq_file.file_name}")
        target_file_path = os.path.join(settings.BASE_DIR, f"work/editAnalysis/EA_files/{target_md5}/{target_file.file_name}")

        runEditAnalysis.delay(str(task.task_id), task_dir, start, end, fq_files_path, target_file_path)

        return Response({
            "status": "analysis",
            "task_id": str(task.task_id),
            "message": "任务已提交，正在分析中",
            "create_time": task.create_time.isoformat(),
            "estimated_time": get_estimated_time(task),
            "retry_info": self._get_retry_info(fq_md5, target_md5, start, end)
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
                    task_record = EditAnalysisTasks.objects.get(task_id=task_id)
                    task_record.delete()
                except EditAnalysisTasks.DoesNotExist:
                    # 记录不存在，但继续尝试清理文件
                    pass
                
                # 删除工作目录
                task_work_dir = os.path.join(settings.BASE_DIR, 'work', 'editAnalysis', 'EA_tasks', task_id)
                
                # 安全删除目录
                if os.path.exists(task_work_dir):
                    try:
                        shutil.rmtree(task_work_dir)
                    except Exception as e:
                        # 记录警告但不中断流程
                        print(f"警告：删除目录 {task_work_dir} 失败: {str(e)}")
                
                return {'success': True}
                
            except Exception as e:
                return {'success': False, 'error': str(e)}

    def _cleanup_orphaned_data(self, fq_md5, target_md5, start, end):
        """
        清理孤立的数据（有记录但无文件，或有文件但无记录）
        """
        with _task_cleanup_lock:
            # 查找可能存在的孤立记录
            orphaned_records = EditAnalysisTasks.objects.filter(
                fq_files_md5=fq_md5,
                target_file_md5=target_md5,
                start=start,
                end=end
            )
            
            for record in orphaned_records:
                # 检查工作目录是否存在
                task_work_dir = os.path.join(settings.BASE_DIR, 'work', 'editAnalysis', 'EA_tasks', str(record.task_id))
                
                if not os.path.exists(task_work_dir):
                    # 目录不存在，可能是孤立记录
                    if record.status in ['analysis']:
                        # 对于未完成的任务，如果目录不存在则标记为失败
                        record.status = 'failure'
                        record.result_data = {'reason': '任务目录丢失，可能被意外删除'}
                        record.save()
                        print(f"标记丢失任务为失败: {record.task_id}")
                    elif record.status == 'failure':
                        # 失败任务且目录不存在，可以安全删除记录
                        print(f"清理失败任务记录: {record.task_id}")
                        record.delete()

    def _get_retry_info(self, fq_md5, target_md5, start, end):
        """
        获取重试相关信息
        """
        task_identifier = f"edit_{fq_md5}_{target_md5}_{start}_{end}"
        retry_count = cache.get(task_identifier, 0)
        return {
            "retry_count": retry_count,
            "max_retries": MAX_RETRY_COUNT,
            "can_retry": retry_count < MAX_RETRY_COUNT
        }


# 接口2，文件上传
# class FileUploadView(APIView):
#     """
#     接口2：文件上传
#     """
#
#     def post(self, request):
#         fq_file = request.FILES.get("fq_files")
#         fq_md5 = request.data.get("fq_files_md5")
#         target_file = request.FILES.get("target_file")
#         target_md5 = request.data.get("target_file_md5")
#
#         results = {}
#
#         # 上传 fq 文件
#         if fq_file and fq_md5:
#             if not fq_file.name.endswith(".zip"):
#                 results["fq_files"] = "上传失败，只能上传zip文件"
#             else:
#                 if not EditAnalysisFiles.objects.filter(file_md5=fq_md5).exists():
#                     fq_dir = os.path.join(settings.BASE_DIR, f"work/editAnalysis/EA_files/{fq_md5}/")
#                     os.makedirs(fq_dir, exist_ok=True)
#                     fq_path = os.path.join(fq_dir, fq_file.name)
#                     with open(fq_path, "wb+") as f:
#                         for chunk in fq_file.chunks():
#                             f.write(chunk)
#                     EditAnalysisFiles.objects.create(
#                         file_type="fq_files",
#                         file_name=fq_file.name,
#                         file_size=fq_file.size,
#                         file_md5=fq_md5,
#                     )
#                 results["fq_files"] = "上传成功"
#
#         # 上传 target 文件
#         if target_file and target_md5:
#             if not EditAnalysisFiles.objects.filter(file_md5=target_md5).exists():
#                 target_dir = os.path.join(settings.BASE_DIR, f"work/editAnalysis/EA_files/{target_md5}/")
#                 os.makedirs(target_dir, exist_ok=True)
#                 target_path = os.path.join(target_dir, target_file.name)
#                 with open(target_path, "wb+") as f:
#                     for chunk in target_file.chunks():
#                         f.write(chunk)
#                 EditAnalysisFiles.objects.create(
#                     file_type="target_file",
#                     file_name=target_file.name,
#                     file_size=target_file.size,
#                     file_md5=target_md5,
#                 )
#             results["target_file"] = "上传成功"
#
#         if not results:
#             return Response({"error": "没有文件上传"}, status=status.HTTP_400_BAD_REQUEST)
#
#         return Response(results, status=status.HTTP_200_OK)


# 接口3，任务查询
# 接口2，文件上传
class FileUploadView(APIView):
    """
    接口2：文件上传
    """

    def post(self, request):
        fq_file = request.FILES.get("fq_files")
        fq_md5 = request.data.get("fq_files_md5")
        target_file = request.FILES.get("target_file")
        target_md5 = request.data.get("target_file_md5")

        results = {}

        # 上传 fq 文件
        if fq_file and fq_md5:
            if not fq_file.name.endswith(".zip"):
                results["fq_files"] = "pload failed, only zip files are allowed"
            else:
                # 检查数据库中是否存在该 MD5 记录
                fq_record_exists = EditAnalysisFiles.objects.filter(file_md5=fq_md5).exists()
                        
                # 如果数据库记录不存在，或者记录存在但文件已丢失，需要重新上传
                need_upload = not fq_record_exists
                        
                if fq_record_exists:
                    # 数据库记录存在，检查文件是否真的存在
                    expected_path = os.path.join(settings.BASE_DIR, f"work/editAnalysis/EA_files/{fq_md5}/", fq_file.name)
                    if not os.path.exists(expected_path):
                        need_upload = True  # 文件丢失，需要重新上传
                        
                if need_upload:
                    # 确保应用的顶层目录存在
                    ea_base_dir = os.path.join(settings.BASE_DIR, 'work', 'editAnalysis')
                    ea_files_dir = os.path.join(ea_base_dir, 'EA_files')
                    os.makedirs(ea_base_dir, exist_ok=True)
                    os.makedirs(ea_files_dir, exist_ok=True)
                            
                    fq_dir = os.path.join(settings.BASE_DIR, f"work/editAnalysis/EA_files/{fq_md5}/")
                    fq_path = os.path.join(fq_dir, fq_file.name)
        
                    try:
                        os.makedirs(fq_dir, exist_ok=True)
                        with open(fq_path, "wb+") as f:
                            for chunk in fq_file.chunks():
                                f.write(chunk)
                                
                        # 如果之前有旧记录，先删除（避免重复）
                        if fq_record_exists:
                            EditAnalysisFiles.objects.filter(file_md5=fq_md5).delete()
                                
                        EditAnalysisFiles.objects.create(
                            file_type="fq_files",
                            file_name=fq_file.name,
                            file_size=fq_file.size,
                            file_md5=fq_md5,
                        )
                        results["fq_files"] = "Upload successful"
                    except Exception as e:
                        # 上传失败时删除部分上传的文件
                        if os.path.exists(fq_path):
                            os.remove(fq_path)
                        if os.path.exists(fq_dir) and not os.listdir(fq_dir):
                            os.rmdir(fq_dir)  # 只删除空目录
                        print(f"fq 文件上传失败：{str(e)}")
                        results["fq_files"] = "Upload failed"
                else:
                    results["fq_files"] = "Upload successful"

        # 上传 target 文件
        if target_file and target_md5:
            # 检查数据库中是否存在该 MD5 记录
            target_record_exists = EditAnalysisFiles.objects.filter(file_md5=target_md5).exists()
                    
            # 如果数据库记录不存在，或者记录存在但文件已丢失，需要重新上传
            need_upload = not target_record_exists
                    
            if target_record_exists:
                # 数据库记录存在，检查文件是否真的存在
                expected_path = os.path.join(settings.BASE_DIR, f"work/editAnalysis/EA_files/{target_md5}/", target_file.name)
                if not os.path.exists(expected_path):
                    need_upload = True  # 文件丢失，需要重新上传
                    
            if need_upload:
                # 确保应用的顶层目录存在
                ea_base_dir = os.path.join(settings.BASE_DIR, 'work', 'editAnalysis')
                ea_files_dir = os.path.join(ea_base_dir, 'EA_files')
                os.makedirs(ea_base_dir, exist_ok=True)
                os.makedirs(ea_files_dir, exist_ok=True)
                        
                target_dir = os.path.join(settings.BASE_DIR, f"work/editAnalysis/EA_files/{target_md5}/")
                target_path = os.path.join(target_dir, target_file.name)
        
                try:
                    os.makedirs(target_dir, exist_ok=True)
                    with open(target_path, "wb+") as f:
                        for chunk in target_file.chunks():
                            f.write(chunk)
                            
                    # 如果之前有旧记录，先删除（避免重复）
                    if target_record_exists:
                        EditAnalysisFiles.objects.filter(file_md5=target_md5).delete()
                            
                    EditAnalysisFiles.objects.create(
                        file_type="target_file",
                        file_name=target_file.name,
                        file_size=target_file.size,
                        file_md5=target_md5,
                    )
                    results["target_file"] = "Upload successful"
                except Exception as e:
                    # 上传失败时删除部分上传的文件
                    if os.path.exists(target_path):
                        os.remove(target_path)
                    if os.path.exists(target_dir) and not os.listdir(target_dir):
                        os.rmdir(target_dir)  # 只删除空目录
                    print(f"target 文件上传失败：{str(e)}")
                    results["target_file"] = "Upload failed"
            else:
                results["target_file"] = "Upload successful"

        if not results:
            return Response({"error": "No file uploaded"}, status=status.HTTP_400_BAD_REQUEST)

        return Response(results, status=status.HTTP_200_OK)


class TaskResultView(APIView):
    def get(self, request, task_id):
        try:
            task = EditAnalysisTasks.objects.get(task_id=task_id)
        except EditAnalysisTasks.DoesNotExist:
            return Response({"error": "Task does not exist"}, status=404)

        # 获取重试信息
        retry_info = None
        if task.status == "failure":
            # 从任务参数重建任务标识符
            task_identifier = f"edit_{task.fq_files_md5}_{task.target_file_md5}_{task.start}_{task.end}"
            retry_count = cache.get(task_identifier, 0)
            retry_info = {
                "retry_count": retry_count,
                "max_retries": MAX_RETRY_COUNT,
                "can_retry": retry_count < MAX_RETRY_COUNT
            }

        if task.status == "analysis":
            task_dir = os.path.join(settings.BASE_DIR, f"work/editAnalysis/EA_tasks/{task_id}/")
            logs_dir = os.path.join(task_dir, "logs")

            current_step = 0
            if os.path.exists(logs_dir):
                for i in range(1, 5):
                    if os.path.exists(os.path.join(logs_dir, f"step{i}.log")):
                        current_step = i

            # 已经耗时（直接用 create_time）
            elapsed_time = int((timezone.now() - task.create_time).total_seconds())

            return Response({
                "status": "analysis",
                "task_id": str(task.task_id),
                "message": "Task is being analyzed",
                "current_step": current_step,
                "elapsed_time": elapsed_time,
                "create_time": task.create_time.isoformat(),
                "estimated_time": get_estimated_time(task),
                "retry_info": retry_info
            })

        if task.status == "analysis":
            return Response({
                "status": "analysis", 
                "task_id": str(task.task_id), 
                "message": "Task is being analyzed",
                "retry_info": retry_info
            })
        elif task.status == "success":
            return Response({
                "status": "success", 
                "task_id": str(task.task_id), 
                "data": task.result_data,
                "retry_info": retry_info
            })
        elif task.status == "partial_success":
            return Response({
                "status": "partial_success", 
                "task_id": str(task.task_id), 
                "data": task.result_data,
                "retry_info": retry_info
            })
        elif task.status == "failure":
            reason = task.result_data.get("reason") if isinstance(task.result_data, dict) else None
            return Response({
                "status": "failure", 
                "task_id": str(task.task_id), 
                "reason": reason or "Unknown error",
                "retry_info": retry_info
            })

        return Response({
            "status": "unknown", 
            "task_id": str(task.task_id),
            "retry_info": retry_info
        }, status=500)


# 接口4，任务文件预览
class ResultFileContentView(APIView):
    """
    根据 task_id 和 文件名 获取文件内容
    """
    def get(self, request, task_id, filename):
        try:
            task = EditAnalysisTasks.objects.get(task_id=task_id)
        except EditAnalysisTasks.DoesNotExist:
            return Response({"error": "Task does not exist"}, status=404)

        if task.status != "success":
            return Response({"error": "Task did not complete successfully"}, status=400)

        task_dir = os.path.join(settings.BASE_DIR, f"work/editAnalysis/EA_tasks/{task_id}/")
        result_dir = os.path.join(task_dir, "04result")

        # 如果请求的是 result.zip -> 下载
        if filename == "result.zip":
            zip_path = os.path.join(result_dir, "result.zip")
            if not os.path.exists(zip_path):
                return Response({"error": "result.zip file does not exist"}, status=404)

            response = FileResponse(open(zip_path, "rb"), as_attachment=True)
            response["Content-Disposition"] = f'attachment; filename="{filename}"'
            return response


        file_path = os.path.join(result_dir, f"{filename}_output.xls")

        if not os.path.exists(file_path):
            return Response({"error": "File does not exist"}, status=404)

        try:
            df = pd.read_csv(
                file_path,
                sep="\t",
                encoding="utf-8",
                dtype=str,
                na_filter=False
            )
            records = df.to_dict(orient="records")
            return Response({"filename": filename, "data": records})
        except Exception as e:
            print(f"读取文件出错: {file_path}")
            return Response({"error": f"Failed to read file, maybe it is empty."}, status=500)


# 接口5，任务删除
class DeleteTaskView(APIView):
    """
    安全删除任务及对应文件夹
    使用统一的安全清理机制
    """
    def post(self, request):
        task_id = request.data.get("task_id")
        if not task_id:
            return Response({"error": "Missing task_id parameter"}, status=400)

        # 使用安全清理方法
        cleanup_result = EditAnalysisView()._safe_cleanup_task(task_id)
        
        if cleanup_result['success']:
            return Response({
                "status": "success", 
                "message": f"Task {task_id} has been deleted successfully"
            })
        else:
            return Response({
                "status": "error",
                "error": "Failed to delete task",
                "details": cleanup_result['error']
            }, status=500)




