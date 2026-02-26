import os
import uuid
import shutil

import pandas as pd
from django.conf import settings
from django.http import FileResponse
from django.utils import timezone
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from .models import EditAnalysisFiles, EditAnalysisTasks
from .tasks import runEditAnalysis


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

        # 查任务
        task = EditAnalysisTasks.objects.filter(
            fq_files_md5=fq_md5,
            target_file_md5=target_md5,
            start=start,
            end=end
        ).first()

        if task:
            if task.status == "success" and task.result_data:
                return Response({"status": "success", "task_id": str(task.task_id), "data": task.result_data})
            elif task.status == "partial_success" and task.result_data:
                return Response({"status": "partial_success", "task_id": str(task.task_id), "data": task.result_data})
            elif task.status == "analysis":
                return Response({"status": "analysis", "task_id": str(task.task_id), "message": "Task is being analyzed"})
            elif task.status == "failure":
                reason = task.result_data.get("reason") if isinstance(task.result_data, dict) else None
                return Response({
                    "status": "failure",
                    "task_id": str(task.task_id),
                    "reason": reason or "Analysis error"
                })

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

        return Response({"status": "analysis",
                         "task_id": str(task.task_id),
                         "message": "Task submitted, currently being analyzed",
                         "create_time": task.create_time.isoformat(),
                         "estimated_time": get_estimated_time(task)})


# # 接口2，文件上传
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
                if not EditAnalysisFiles.objects.filter(file_md5=fq_md5).exists():
                    fq_dir = os.path.join(settings.BASE_DIR, f"work/editAnalysis/EA_files/{fq_md5}/")
                    fq_path = os.path.join(fq_dir, fq_file.name)

                    try:
                        os.makedirs(fq_dir, exist_ok=True)
                        with open(fq_path, "wb+") as f:
                            for chunk in fq_file.chunks():
                                f.write(chunk)
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
                        print(f"fq文件上传失败: {str(e)}")
                        results["fq_files"] = "Upload failed"
                else:
                    results["fq_files"] = "Upload successful"

        # 上传 target 文件
        if target_file and target_md5:
            if not EditAnalysisFiles.objects.filter(file_md5=target_md5).exists():
                target_dir = os.path.join(settings.BASE_DIR, f"work/editAnalysis/EA_files/{target_md5}/")
                target_path = os.path.join(target_dir, target_file.name)

                try:
                    os.makedirs(target_dir, exist_ok=True)
                    with open(target_path, "wb+") as f:
                        for chunk in target_file.chunks():
                            f.write(chunk)
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
                    print(f"target文件上传失败: {str(e)}")
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
            })

        if task.status == "analysis":
            return Response({"status": "analysis", "task_id": str(task.task_id), "message": "Task is being analyzed"})
        elif task.status == "success":
            return Response({"status": "success", "task_id": str(task.task_id), "data": task.result_data})
        elif task.status == "partial_success":
            return Response({"status": "partial_success", "task_id": str(task.task_id), "data": task.result_data})
        elif task.status == "failure":
            reason = task.result_data.get("reason") if isinstance(task.result_data, dict) else None
            return Response({"status": "failure", "task_id": str(task.task_id), "reason": reason or "Unknown error"})

        return Response({"status": "unknown", "task_id": str(task.task_id)}, status=500)


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
    测试用：删除任务及对应文件夹
    """
    def post(self, request):
        task_id = request.data.get("task_id")
        if not task_id:
            return Response({"error": "Missing task_id parameter"}, status=400)

        try:
            task = EditAnalysisTasks.objects.get(task_id=task_id)
        except EditAnalysisTasks.DoesNotExist:
            return Response({"error": "Task does not exist"}, status=404)

        # 删除任务文件夹
        task_dir = os.path.join(settings.BASE_DIR, f"work/editAnalysis/EA_tasks/{task_id}/")
        if os.path.exists(task_dir):
            try:
                shutil.rmtree(task_dir)
            except Exception as e:
                print(f"删除任务文件夹出错: {str(e)}")
                return Response({"error": f"Failed to delete task folder"}, status=500)

        # 删除数据库记录
        task.delete()

        return Response({"status": "success", "message": f"Task {task_id} has been deleted"})




