import os
import subprocess
import pandas as pd
from celery import shared_task
from django.utils import timezone
from django.conf import settings
from .models import EditAnalysisTasks


@shared_task
def runEditAnalysis(task_id, task_dir, start, end, fq_files_path, target_file_path):
    task = EditAnalysisTasks.objects.get(task_id=task_id)

    command = f"bash {settings.BASE_DIR}/software/hitom/MDBP.sh {fq_files_path} {target_file_path} {start} {end} {task_dir}"

    try:
        subprocess.run(command, shell=True, check=True, capture_output=True, text=True, cwd=task_dir)

        result_dir = os.path.join(task_dir, "04result")
        result_files = [f for f in os.listdir(result_dir) if f.endswith("_output.xls")]
        file_names = [f[:-11] for f in result_files]

        zip_path = os.path.join(result_dir, "result.zip")
        result_data = {"files": file_names}
        if os.path.exists(zip_path):
            result_data["package"] = "result.zip"

        if not file_names and not os.path.exists(zip_path):
            task.result_data = {"reason": "No result files were generated"}
            task.status = "failure"
        else:
            task.result_data = result_data
            task.status = "success"

    except Exception as e:
        print(f"分析出错: {str(e)}")
        task.result_data = {"reason": "Analysis error"}
        task.status = "failure"

    task.time_of_completion = timezone.now()
    task.save()




# @shared_task
# def runEditAnalysis(task_id, task_dir, start, end, fq_files_path, target_file_path):
#     task = EditAnalysisTasks.objects.get(task_id=task_id)
#
#     command = f"bash {settings.BASE_DIR}/software/hitom/MDBP.sh {fq_files_path} {target_file_path} {start} {end} {task_dir}"
#
#     try:
#         subprocess.run(command, shell=True, check=True, capture_output=True, text=True, cwd=task_dir)
#
#         result_dir = os.path.join(task_dir, "04result")
#         result_files = [f for f in os.listdir(result_dir) if f.endswith("_output.xls")]
#         #
#         # result_data = {}
#         # has_error = False
#         #
#         # for file_path in result_files:
#         #     filename = os.path.basename(file_path)
#         #     name_without_ext = os.path.splitext(filename)[0]
#         #     try:
#         #         df = pd.read_csv(
#         #             file_path,
#         #             sep="\t",
#         #             encoding="utf-8",
#         #             dtype=str,
#         #             na_filter=False
#         #         )
#         #         records = df.to_dict(orient="records")
#         #         result_data[name_without_ext] = records
#         #     except Exception as e:
#         #         has_error = True
#         #         print(f"读取文件出错: {file_path}")
#         #         print(str(e))
#         #         result_data[name_without_ext] = {"error": "读取失败"}
#         #
#         # if not result_data:
#         #     task.result_data = {"reason": "没有生成任何结果文件"}
#         #     task.status = "failure"
#         # elif has_error:
#         #     task.result_data = result_data
#         #     task.status = "partial_success"
#         # else:
#         #     task.result_data = result_data
#         #     task.status = "success"
#         ## file_names = [os.path.splitext(f)[0] for f in result_files]
#         file_names = [f[:-11] for f in result_files]
#
#         zip_path = os.path.join(result_dir, "result.zip")
#         result_data = {"files": file_names}
#         if os.path.exists(zip_path):
#             result_data["package"] = "result.zip"
#
#         if not file_names and not os.path.exists(zip_path):
#             task.result_data = {"reason": "No result files were generated"}
#             task.status = "failure"
#         else:
#             task.result_data = result_data
#             task.status = "success"
#
#     except Exception as e:
#         print(f"分析出错: {str(e)}")
#         task.result_data = {"reason": "Analysis error"}
#         task.status = "failure"
#
#     task.time_of_completion = timezone.now()
#     task.save()