import os
import subprocess
import uuid
import json
from datetime import timedelta

from django.conf import settings
from django.http import FileResponse, HttpResponseNotFound, HttpResponse
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView
from . import cas12, models
from .tasks import run_cas12a_analysis, run_cas12b_analysis

class Cas12aExecuteView(APIView):
    """
    Cas12a执行
    """

    def post(self, request):
        inputSequence = request.data.get('inputSequence')
        pam = request.data.get('pam', 'TTTN')  # CAS12a默认PAM为TTTN
        spacerLength = request.data.get('spacerLength', 23)  # CAS12a默认spacerLength为23
        sgRNAModule = request.data.get('sgRNAModule', 'spacerpam')
        name_db = request.data.get('name_db')

        # 检查数据库中是否已存在相同且已完成的任务
        result_cas12a = models.result_cas12a_list.objects.filter(input_sequence=inputSequence,
                                                                 pam_type=pam,
                                                                 spacer_length=spacerLength,
                                                                 sgRNA_module=sgRNAModule,
                                                                 name_db=name_db,
                                                                 task_status="finished",
                                                                 sgRNA_with_JBrowse_json__isnull=False)
        if result_cas12a.first():
            # 从文件中读取结果数据
            result_file_path = os.path.join(settings.BASE_DIR, result_cas12a.first().sgRNA_with_JBrowse_json)
            if os.path.exists(result_file_path):
                with open(result_file_path, 'r') as f:
                    response_data = json.load(f)
                return Response(response_data, status=status.HTTP_200_OK)
            else:
                # 如果文件不存在，清除记录并重新处理
                result_cas12a.first().delete()

        # 检查是否已存在相同但失败的任务
        failed_cas12a = models.result_cas12a_list.objects.filter(input_sequence=inputSequence,
                                                                 pam_type=pam,
                                                                 spacer_length=spacerLength,
                                                                 sgRNA_module=sgRNAModule,
                                                                 name_db=name_db,
                                                                 task_status="failed")
        if failed_cas12a.first():
            # 删除失败的任务记录，准备重新创建
            failed_cas12a.first().delete()

        # 检查是否已存在正在进行中或等待中的任务
        existing_cas12a = models.result_cas12a_list.objects.filter(input_sequence=inputSequence,
                                                                   pam_type=pam,
                                                                   spacer_length=spacerLength,
                                                                   sgRNA_module=sgRNAModule,
                                                                   name_db=name_db)
        pending_or_running_cas12a = existing_cas12a.filter(task_status__in=['pending', 'running'])
        if pending_or_running_cas12a.first():
            # 返回任务正在处理中的信息和预估完成时间
            task_record = pending_or_running_cas12a.first()
            # 简单估算：假设任务需要5分钟完成
            estimated_completion = task_record.submit_time + timedelta(minutes=5)
            now = timezone.now()
            remaining_time = estimated_completion - now if estimated_completion > now else timedelta(minutes=1)
            
            return Response({
                "msg": "任务正在分析中",
                "task_id": task_record.task_id,
                "estimated_completion": estimated_completion.isoformat(),
                "remaining_time_seconds": remaining_time.total_seconds()
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
        else:
            if os.path.exists(file_paths[file_type]):
                return FileResponse(open(file_paths[file_type], "rb"))
            return HttpResponseNotFound("File generation failed")


class Cas12bExecuteView(APIView):
    """
    Cas12b执行
    """

    def post(self, request):
        inputSequence = request.data.get('inputSequence')
        pam = request.data.get('pam', 'TTN')  # CAS12b默认PAM为TTN
        spacerLength = request.data.get('spacerLength', 20)  # CAS12b默认spacerLength为20
        sgRNAModule = request.data.get('sgRNAModule', 'spacerpam')
        name_db = request.data.get('name_db')

        # 检查数据库中是否已存在相同且已完成的任务
        result_cas12b = models.result_cas12b_list.objects.filter(input_sequence=inputSequence,
                                                                 pam_type=pam,
                                                                 spacer_length=spacerLength,
                                                                 sgRNA_module=sgRNAModule,
                                                                 name_db=name_db,
                                                                 task_status="finished",
                                                                 sgRNA_with_JBrowse_json__isnull=False)
        if result_cas12b.first():
            # 从文件中读取结果数据
            result_file_path = os.path.join(settings.BASE_DIR, result_cas12b.first().sgRNA_with_JBrowse_json)
            if os.path.exists(result_file_path):
                with open(result_file_path, 'r') as f:
                    response_data = json.load(f)
                return Response(response_data, status=status.HTTP_200_OK)
            else:
                # 如果文件不存在，清除记录并重新处理
                result_cas12b.first().delete()

        # 检查是否已存在相同但失败的任务
        failed_cas12b = models.result_cas12b_list.objects.filter(input_sequence=inputSequence,
                                                                 pam_type=pam,
                                                                 spacer_length=spacerLength,
                                                                 sgRNA_module=sgRNAModule,
                                                                 name_db=name_db,
                                                                 task_status="failed")
        if failed_cas12b.first():
            # 删除失败的任务记录，准备重新创建
            failed_cas12b.first().delete()

        # 检查是否已存在正在进行中或等待中的任务
        existing_cas12b = models.result_cas12b_list.objects.filter(input_sequence=inputSequence,
                                                                   pam_type=pam,
                                                                   spacer_length=spacerLength,
                                                                   sgRNA_module=sgRNAModule,
                                                                   name_db=name_db)
        pending_or_running_cas12b = existing_cas12b.filter(task_status__in=['pending', 'running'])
        if pending_or_running_cas12b.first():
            # 返回任务正在处理中的信息和预估完成时间
            task_record = pending_or_running_cas12b.first()
            # 简单估算：假设任务需要5分钟完成
            estimated_completion = task_record.submit_time + timedelta(minutes=5)
            now = timezone.now()
            remaining_time = estimated_completion - now if estimated_completion > now else timedelta(minutes=1)
            
            return Response({
                "msg": "任务正在分析中",
                "task_id": task_record.task_id,
                "estimated_completion": estimated_completion.isoformat(),
                "remaining_time_seconds": remaining_time.total_seconds()
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
            "status": "pending"
        }, status=status.HTTP_202_ACCEPTED)


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
        else:
            if os.path.exists(file_paths[file_type]):
                return FileResponse(open(file_paths[file_type], "rb"))
            return HttpResponseNotFound("File generation failed")