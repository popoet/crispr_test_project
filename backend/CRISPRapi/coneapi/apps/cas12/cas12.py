import subprocess
import re
import json
import os
import logging
import pandas as pd
import pysam
from Bio import SeqIO
from Bio.Seq import Seq
from Bio.SeqRecord import SeqRecord
import pybedtools
from django.conf import settings
from pybedtools import BedTool
from pandarallel import pandarallel
from .models import result_cas12a_list, result_cas12b_list

# 配置日志
logger = logging.getLogger(__name__)

IUPAC_dict = {
    'A': 'A',
    'T': 'T',
    'C': 'C',
    'G': 'G',
    'R': '[A|G]',
    'Y': '[C|T]',
    'S': '[G|C]',
    'W': '[A|T]',
    'K': '[G|T]',
    'M': '[A|C]',
    'B': '[C|G|T]',
    'D': '[A|G|T]',
    'H': '[A|C|T]',
    'V': '[A|C|G]',
    'N': '[A|T|C|G]'
}

def setup_task_logger(task_path, task_id, task_type="cas12"):
    """为每个任务设置独立的日志文件"""
    # 创建日志目录
    log_dir = os.path.join(task_path, 'logs')
    os.makedirs(log_dir, exist_ok=True)
    
    # 创建任务特定的日志记录器
    logger_name = f"{task_type}_task_{task_id}"
    task_logger = logging.getLogger(logger_name)
    task_logger.setLevel(logging.DEBUG)
    
    # 清理已有的处理器避免重复添加
    for handler in task_logger.handlers[:]:
        handler.close()
        task_logger.removeHandler(handler)
    
    # 创建文件处理器
    log_file = os.path.join(log_dir, f'task_{task_id}.log')
    file_handler = logging.FileHandler(log_file, encoding='utf-8')
    file_handler.setLevel(logging.DEBUG)
    
    # 创建日志格式
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    file_handler.setFormatter(formatter)
    
    # 添加处理器到日志记录器
    task_logger.addHandler(file_handler)
    task_logger.propagate = False  # 防止日志传播到父级记录器
    
    return task_logger

def initial_sgRNA(pamType):
    pam_dict = {
        'NGG': ['spacerpam', 20],
        'NG': ['spacerpam', 20],
        'NNG': ['spacerpam', 20],
        'NGN': ['spacerpam', 20],
        'NNGT': ['spacerpam', 20],
        'NAA': ['spacerpam', 20],
        'NNGRRT': ['spacerpam', 21],
        'NNGRRT-20': ['spacerpam', 20],
        'NGK': ['spacerpam', 20],
        'NNNRRT': ['spacerpam', 21],
        'NNNRRT-20': ['spacerpam', 20],
        'NGA': ['spacerpam', 20],
        'NNNNCC': ['spacerpam', 24],
        'NGCG': ['spacerpam', 20],
        'NNAGAA': ['spacerpam', 20],
        'NGGNG': ['spacerpam', 20],
        'NNNNGMTT': ['spacerpam', 20],
        'NNNNACA': ['spacerpam', 20],
        'NNNNRYAC': ['spacerpam', 22],
        'NNNVRYAC': ['spacerpam', 22],
        'TTCN': ['pamspacer', 20],
        'YTTV': ['pamspacer', 20],
        'NNNNCNAA': ['spacerpam', 20],
        'NNN': ['spacerpam', 20],
        'NRN': ['spacerpam', 20],
        'NYN': ['spacerpam', 20],
        'TTTN': ['spacerpam', 23],  # CAS12a 默认参数
        'TTN': ['spacerpam', 20],   # CAS12b 默认参数
    }
    sgRNAModule, spacerLength = pam_dict.get(pamType, ['spacerpam', 20])
    return sgRNAModule, spacerLength

def form12aDatabase(task_id, inputSequence, pam, spacerLength, sgRNAModule, name_db):
    conda_env_path = settings.CONDA_ENV_PATH
    task_path = os.path.join(settings.BASE_DIR, 'work', 'cas12aTasks', str(task_id))
    os.makedirs(task_path, exist_ok=True)
    
    # 为当前任务设置日志记录器
    task_logger = setup_task_logger(task_path, task_id, "cas12a")
    
    try:
        blastb_software = settings.BLAST_SOFTWARE
        fa_path = os.path.join(settings.BASE_DIR, 'database', 'TargetGenome', name_db, f'{name_db}.fa')
        gff_path = os.path.join(settings.BASE_DIR, 'database', 'TargetGenomeGff', name_db, f'{name_db}.gff')
        gff_pkl_path = os.path.join(settings.BASE_DIR, 'database', 'TargetGenomeGff', name_db, f'{name_db}.gff.pkl')
        batmis_path = settings.BATMIS_BIN

        # 0、获取或创建任务记录
        try:
            cas12a_task_record = result_cas12a_list.objects.get(task_id=task_id)
        except result_cas12a_list.DoesNotExist:
            cas12a_task_record = result_cas12a_list(task_id=task_id, input_sequence=inputSequence, pam_type=pam,
                                                spacer_length=spacerLength, sgRNA_module=sgRNAModule, name_db=name_db,
                                                task_status='running', sequence_position='')
            cas12a_task_record.save()

        # 1、识别输入序列类型
        input_type = input_sequence_to_fasta_sequence_position(inputSequence, task_logger)
        # 3、获取序列和位置信息
        fasta_sequence, fasta_sequence_position = input_type_to_sequence_and_position(task_path, blastb_software, input_type, fa_path, gff_pkl_path, task_id, task_logger)
        fasta_sequence_position_json = json.dumps(fasta_sequence_position)
        if fasta_sequence:
            cas12a_task_record.sequence_position = fasta_sequence_position_json
            cas12a_task_record.save()
        else:
            cas12a_task_record.task_status = 'failed'
            cas12a_task_record.log = 'empty BLAST result'
            cas12a_task_record.save()
            task_logger.error(f"任务 {task_id} 失败: 未能获取序列和位置信息")
            return "未能获取序列和位置信息"

        # # 4、生成目标序列
        target_start, target_end, target_seq, target_seq_reverse = sequence_and_position_to_target_seq(fa_path,
                                                                                                       fasta_sequence_position,
                                                                                                       spacerLength, task_logger)
        # 5、获取目标区域基因家族信息
        family_records = target_to_ontarget(gff_path, gff_pkl_path, fasta_sequence_position['seqid'], target_start, target_end, task_logger)
        task_logger.info("5、获取目标区域基因家族信息:")
        task_logger.info(family_records)
        # === 提取唯一的 gene ID ===
        # Step 1: 筛选 featuretype 为 'gene' 的行
        gene_rows = family_records[family_records['featuretype'] == 'gene']

        # Step 2: 获取这些行的 ID 列，并去重
        unique_gene_ids = gene_rows['ID'].drop_duplicates().values

        task_logger.info(f"检测到的唯一基因 ID: {unique_gene_ids.tolist()}")
        # print("Unique gene IDs:", unique_gene_ids)

        # Step 3: 判断数量
        if len(unique_gene_ids) > 2:
            cas12a_task_record.task_status = 'failed'
            cas12a_task_record.log = f"错误：目标区域包含 {len(unique_gene_ids)} 个不同基因（{unique_gene_ids.tolist()}），超过最大允许数量（2）。"
            cas12a_task_record.save()
            task_logger.error(f"任务 {task_id} 失败: 基因数量超限")
            return "目标区域包含基因数量超过限制（最多2个）"

        # 6、生成 sgRNA 候选列表
        sgRNA_dataframe, sgRNA_json = generate_sgRNA_dataframe(family_records, target_seq, target_seq_reverse, target_start,
                                                               target_end, fasta_sequence_position['seqid'], pam,
                                                               spacerLength, sgRNAModule, task_path, task_logger)
        # 7、运行 BATMAN 工具进行 off-target 分析
        sam_file, intersect_file = run_batman(conda_env_path, task_path, fa_path, gff_path, task_id, batmis_path, task_logger)
        sam_pandas, intersect_pandas = intersect_to_pandas(fa_path, sam_file, intersect_file, spacerLength, pam, task_logger)
        # 8、将 off-target 数据转换为 JSON
        guide_json = sam_intersect_pandas_to_json(sam_pandas, intersect_pandas, task_path, task_logger)

        # 9、添加 JBrowse 可视化信息并保存到文件
        json_file_path = add_Jbrowse_to_json(task_id, task_path, fasta_sequence_position_json, guide_json, name_db, task_logger, "cas12a")
        return json_file_path
    except Exception as e:
        # 记录错误日志
        error_msg = f"任务 {task_id} 执行过程中发生错误: {str(e)}"
        task_logger.error(error_msg, exc_info=True)
        try:
            cas12a_task_record = result_cas12a_list.objects.get(task_id=task_id)
            cas12a_task_record.task_status = 'failed'
            cas12a_task_record.log = error_msg
            cas12a_task_record.save()
        except result_cas12a_list.DoesNotExist:
            pass
        return error_msg
    finally:
        # 清理日志处理器，防止文件句柄泄露
        logger_name = f"cas12a_task_{task_id}"
        task_logger = logging.getLogger(logger_name)
        for handler in task_logger.handlers[:]:
            handler.close()
            task_logger.removeHandler(handler)


def form12bDatabase(task_id, inputSequence, pam, spacerLength, sgRNAModule, name_db):
    conda_env_path = settings.CONDA_ENV_PATH
    task_path = os.path.join(settings.BASE_DIR, 'work', 'cas12bTasks', str(task_id))
    os.makedirs(task_path, exist_ok=True)
    
    # 为当前任务设置日志记录器
    task_logger = setup_task_logger(task_path, task_id, "cas12b")
    
    try:
        blastb_software = settings.BLAST_SOFTWARE
        fa_path = os.path.join(settings.BASE_DIR, 'database', 'TargetGenome', name_db, f'{name_db}.fa')
        gff_path = os.path.join(settings.BASE_DIR, 'database', 'TargetGenomeGff', name_db, f'{name_db}.gff')
        gff_pkl_path = os.path.join(settings.BASE_DIR, 'database', 'TargetGenomeGff', name_db, f'{name_db}.gff.pkl')
        batmis_path = settings.BATMIS_BIN

        # 0、获取或创建任务记录
        try:
            cas12b_task_record = result_cas12b_list.objects.get(task_id=task_id)
        except result_cas12b_list.DoesNotExist:
            cas12b_task_record = result_cas12b_list(task_id=task_id, input_sequence=inputSequence, pam_type=pam,
                                                spacer_length=spacerLength, sgRNA_module=sgRNAModule, name_db=name_db,
                                                task_status='running', sequence_position='')
            cas12b_task_record.save()

        # 1、识别输入序列类型
        input_type = input_sequence_to_fasta_sequence_position(inputSequence, task_logger)
        # 3、获取序列和位置信息
        fasta_sequence, fasta_sequence_position = input_type_to_sequence_and_position(task_path, blastb_software, input_type, fa_path, gff_pkl_path, task_id, task_logger)
        fasta_sequence_position_json = json.dumps(fasta_sequence_position)
        if fasta_sequence:
            cas12b_task_record.sequence_position = fasta_sequence_position_json
            cas12b_task_record.save()
        else:
            cas12b_task_record.task_status = 'failed'
            cas12b_task_record.log = 'empty BLAST result'
            cas12b_task_record.save()
            task_logger.error(f"任务 {task_id} 失败: 未能获取序列和位置信息")
            return "未能获取序列和位置信息"

        # # 4、生成目标序列
        target_start, target_end, target_seq, target_seq_reverse = sequence_and_position_to_target_seq(fa_path,
                                                                                                       fasta_sequence_position,
                                                                                                       spacerLength, task_logger)
        # 5、获取目标区域基因家族信息
        family_records = target_to_ontarget(gff_path, gff_pkl_path, fasta_sequence_position['seqid'], target_start, target_end, task_logger)
        task_logger.info("5、获取目标区域基因家族信息:")
        task_logger.info(family_records)
        # === 提取唯一的 gene ID ===
        # Step 1: 筛选 featuretype 为 'gene' 的行
        gene_rows = family_records[family_records['featuretype'] == 'gene']

        # Step 2: 获取这些行的 ID 列，并去重
        unique_gene_ids = gene_rows['ID'].drop_duplicates().values

        task_logger.info(f"检测到的唯一基因 ID: {unique_gene_ids.tolist()}")
        # print("Unique gene IDs:", unique_gene_ids)

        # Step 3: 判断数量
        if len(unique_gene_ids) > 2:
            cas12b_task_record.task_status = 'failed'
            cas12b_task_record.log = f"错误：目标区域包含 {len(unique_gene_ids)} 个不同基因（{unique_gene_ids.tolist()}），超过最大允许数量（2）。"
            cas12b_task_record.save()
            task_logger.error(f"任务 {task_id} 失败: 基因数量超限")
            return "目标区域包含基因数量超过限制（最多2个）"

        # 6、生成 sgRNA 候选列表
        sgRNA_dataframe, sgRNA_json = generate_sgRNA_dataframe(family_records, target_seq, target_seq_reverse, target_start,
                                                               target_end, fasta_sequence_position['seqid'], pam,
                                                               spacerLength, sgRNAModule, task_path, task_logger)
        # 7、运行 BATMAN 工具进行 off-target 分析
        sam_file, intersect_file = run_batman(conda_env_path, task_path, fa_path, gff_path, task_id, batmis_path, task_logger)
        sam_pandas, intersect_pandas = intersect_to_pandas(fa_path, sam_file, intersect_file, spacerLength, pam, task_logger)
        # 8、将 off-target 数据转换为 JSON
        guide_json = sam_intersect_pandas_to_json(sam_pandas, intersect_pandas, task_path, task_logger)

        # 9、添加 JBrowse 可视化信息并保存到文件
        json_file_path = add_Jbrowse_to_json(task_id, task_path, fasta_sequence_position_json, guide_json, name_db, task_logger, "cas12b")
        return json_file_path
    except Exception as e:
        # 记录错误日志
        error_msg = f"任务 {task_id} 执行过程中发生错误: {str(e)}"
        task_logger.error(error_msg, exc_info=True)
        try:
            cas12b_task_record = result_cas12b_list.objects.get(task_id=task_id)
            cas12b_task_record.task_status = 'failed'
            cas12b_task_record.log = error_msg
            cas12b_task_record.save()
        except result_cas12b_list.DoesNotExist:
            pass
        return error_msg
    finally:
        # 清理日志处理器，防止文件句柄泄露
        logger_name = f"cas12b_task_{task_id}"
        task_logger = logging.getLogger(logger_name)
        for handler in task_logger.handlers[:]:
            handler.close()
            task_logger.removeHandler(handler)


def add_Jbrowse_to_json(task_id, task_path, sequence_position, guide_json, name_db, task_logger=None, cas_type="cas12"):
    try:
        if cas_type == "cas12a":
            cas12_task_record = result_cas12a_list.objects.get(task_id=task_id)
        else:
            cas12_task_record = result_cas12b_list.objects.get(task_id=task_id)
            
        sequence_position = json.loads(cas12_task_record.sequence_position)
        json_handle = {"TableData": {"json_data": guide_json},
                       "JbrowseInfo": {
                           "assembly": {
                               "name": name_db,
                               "fasta": f"{settings.ADDR}/api/cas12/{cas_type}/{cas_type}_Jbrowse_API/?task_id={task_id}&file_type=fa",
                               "fai": f"{settings.ADDR}/api/cas12/{cas_type}/{cas_type}_Jbrowse_API/?task_id={task_id}&file_type=fai"
                           },
                           "tracks": {
                               "name": name_db,
                               "gff3_gz": f"{settings.ADDR}/api/cas12/{cas_type}/{cas_type}_Jbrowse_API/?task_id={task_id}&file_type=gff3.gz",
                               "gff3_tbi": f"{settings.ADDR}/api/cas12/{cas_type}/{cas_type}_Jbrowse_API/?task_id={task_id}&file_type=gff3.gz.csi"
                           },
                           "position": f"{sequence_position['seqid']}:{sequence_position['start']}..{sequence_position['end']}"
                       }}
        
        # 保存结果到JSON文件
        json_file_path = f'{task_path}/Guide.json3'
        with open(json_file_path, 'w') as file_handle:
            json.dump(json_handle, file_handle)
        
        # 返回相对路径
        relative_path = os.path.relpath(json_file_path, settings.BASE_DIR)
        return relative_path
    except Exception as e:
        if task_logger:
            task_logger.error(f"任务 {task_id} 添加JBrowse信息时发生错误: {str(e)}", exc_info=True)
        raise e


def sam_intersect_pandas_to_json_extract_family(attr):
    match = re.search(r'ID=([^;]+)', attr)
    if match:
        id_val = match.group(1)
        parts = id_val.split('.')
        # 至少包含两部分，如 ['Ghjin_D11', 'g52869']
        if len(parts) >= 2:
            return '.'.join(parts[:2])
        else:
            return id_val
    return None

def sam_intersect_pandas_to_json(sam_pandas, intersect_pandas, task_path, task_logger=None):
    try:
        intersect_pandas.to_csv(f'{task_path}/intersect_pandas.csv')
        intersect_pandas.to_pickle(f'{task_path}/intersect_pandas.plk')
        sam_pandas.to_csv(f'{task_path}/sam_pandas.csv')
        sam_pandas.to_pickle(f'{task_path}/sam_pandas.plk')
        sam_pandas_reset = sam_pandas.reset_index(drop=True)
        merged_pandas = intersect_pandas.merge(sam_pandas_reset, left_on=['seqid', 'sgRNA_start'],
                                               right_on=['rname', 'pos_0_base'], how='left')
        merged_pandas['family'] = merged_pandas['attributes'].apply(sam_intersect_pandas_to_json_extract_family)
        merged_pandas.to_csv(f'{task_path}/merged_pandas.csv')
        merged_pandas.set_index(['seq', 'family'], inplace=True, drop=True)
        intersect_pandas = merged_pandas
        with open(f'{task_path}/Guide.json') as guide_json_handle:
            guide_json = json.load(guide_json_handle)
            for target_seq in intersect_pandas.index.get_level_values(0).unique():
                intersect_target_tmp_pandas = intersect_pandas.loc[target_seq]
                intersect_target_tmp_pandas.to_csv(f'{task_path}/intersect_target_tmp_pandas.csv')
                intersect_target_pandas = intersect_target_tmp_pandas[
                    intersect_target_tmp_pandas['type'] == 'gene'].drop_duplicates()
                intersect_target_pandas.to_csv(f'{task_path}/intersect_target_pandas_1.csv')
                intersect_target_pandas['types_list'] = intersect_target_tmp_pandas.groupby('family').apply(
                    lambda x: sorted(x.type.unique().tolist()))

                intersect_target_pandas.to_csv(f'{task_path}/intersect_target_pandas_2.csv')
                intersect_target_pandas['types'] = intersect_target_pandas.apply(
                    lambda row: 'intron' if len(row.types_list) == 2 else ', '.join(row.types_list).replace(', gene, mRNA',
                                                                                                            ''), axis=1)
                intersect_target_pandas.to_csv(f'{task_path}/intersect_target_pandas_3.csv')
                intersect_target_pandas.reset_index(level='family', inplace=True)
                intersect_target_json = intersect_target_pandas.to_json(orient='records')
                json_handle = json.loads(intersect_target_json)
                json_handle = {'total': len(json_handle), 'rows': json_handle}
                for index, item in enumerate(guide_json['rows']):
                    if target_seq == item['sgRNA_seq']:
                        guide_json['rows'][index]['offtarget_num'] = json_handle['total']
                        guide_json['rows'][index]['offtarget_json'] = json_handle
                        break
                else:
                    guide_json['rows'][index]['offtarget_num'] = 0
                    guide_json['rows'][index]['offtarget_json'] = None
        with open(f'{task_path}/Guide.json2', 'w') as guide_json_handle:
            json.dump(guide_json, guide_json_handle, indent=4)
        return guide_json
    except Exception as e:
        if task_logger:
            task_logger.error(f"处理sam/intersect数据时发生错误: {str(e)}", exc_info=True)
        raise e


def intersect_to_pandas(fa_path, sam_file, intersect_file, spacerLength, pam, task_logger=None):
    try:
        def fetch_rseq(row):
            return genome_handle.fetch(row['rname'], row['pos'] - 1, row['pos_end'] - 1 + pam_length)

        pam_length = sum(1 for nuc in pam if nuc in IUPAC_dict)
        spacerLength = int(spacerLength)
        sam_pandas = pd.read_csv(
            sam_file,
            header=None,
            sep='\t',
            comment='@',
            names=['qname', 'flag', 'rname', 'pos', 'mapq', 'cigar', 'rnext', 'pnext', 'tlen', 'seq', 'qual', 'NM', 'MD']
        )
        genome_handle = pysam.FastaFile(fa_path)
        sam_pandas['NM'] = sam_pandas['NM'].str.replace('NM:i:', '')
        sam_pandas['MD'] = sam_pandas['MD'].str.replace('MD:Z:', '')
        sam_pandas['pos_end'] = sam_pandas['pos'] + spacerLength
        sam_pandas['rseq'] = sam_pandas.apply(fetch_rseq, axis=1, result_type='expand')
        sam_pandas['pos_0_base'] = sam_pandas['pos'] - 1
        sam_pandas.set_index(['rname', 'pos_0_base'], inplace=True, drop=False)
        sam_pandas.sort_index(inplace=True)
        intersect_pandas = pd.read_csv(
            intersect_file,
            header=None,
            sep='\t'
        )
        intersect_pandas.drop([3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 21, 13, 17, 19], axis=1, inplace=True)
        intersect_pandas.columns = ['seqid', 'sgRNA_start', 'sgRNA_end', 'type', 'start', 'end', 'strand', 'attributes']
        sam_pandas.drop(['mapq', 'cigar', 'rnext', 'pnext', 'tlen', 'qual'], axis=1, inplace=True)
        return sam_pandas, intersect_pandas
    except Exception as e:
        if task_logger:
            task_logger.error(f"读取pandas数据时发生错误: {str(e)}", exc_info=True)
        raise e


def run_batman(conda_env_path, task_path, fa_path, gff_path, task_id, batmis_path, task_logger=None):
    try:
        # 重定向batman命令的输出到日志文件
        log_file = os.path.join(task_path, 'logs', f'task_{task_id}.log')
        
        command_batman = f'export LD_LIBRARY_PATH={settings.CONDA_ENV_PATH}/lib:$LD_LIBRARY_PATH && {batmis_path}/batman -q {task_path}/Guide.fasta -g {fa_path} -n 5 -mall -l {log_file} -o {task_path}/{task_id}.bin 2>> {log_file}'
        command_batdecode = f'export LD_LIBRARY_PATH={settings.CONDA_ENV_PATH}/lib:$LD_LIBRARY_PATH && {batmis_path}/batdecode -i {task_path}/{task_id}.bin -g {fa_path} -o {task_path}/{task_id}.txt 2>> {log_file}'
        command_samtools = f'{settings.CONDA_ENV_BIN_PATH}/samtools view -bS {task_path}/{task_id}.txt > {task_path}/{task_id}.bam 2>> {log_file}'
        command_bedtools = f'{settings.CONDA_ENV_BIN_PATH}/bedtools intersect -a {task_path}/{task_id}.bam -b {gff_path} -wo -bed > {task_path}/{task_id}.intersect 2>> {log_file}'
        
        if task_logger:
            task_logger.info(f"执行命令: {command_batman}")
        os.system(command_batman)
        if task_logger:
            task_logger.info(f"执行命令: {command_batdecode}")
        os.system(command_batdecode)
        if task_logger:
            task_logger.info(f"执行命令: {command_samtools}")
        os.system(command_samtools)
        if task_logger:
            task_logger.info(f"执行命令: {command_bedtools}")
        os.system(command_bedtools)
        return f'{task_path}/{task_id}.txt', f'{task_path}/{task_id}.intersect'
    except Exception as e:
        if task_logger:
            task_logger.error(f"运行BATMAN工具时发生错误: {str(e)}", exc_info=True)
        raise e


# 根据目标 DNA 序列（正向和反向互补）识别潜在的 sgRNA 位点，并将这些候选位点整理成结构化数据（DataFrame 和 JSON），供后续分析使用。
def generate_sgRNA_dataframe(family_records, target_seq, target_seq_reverse, target_start, target_end, target_seqid,
                             pam, spacerLength, sgRNAModule, task_path, task_logger=None):
    try:
        def ontarget_apply():
            confirmed_records = family_records[family_records['interval'].apply(
                lambda row: row.overlaps(pd.Interval(sgRNA_position_start, sgRNA_position_end)))]
            if confirmed_records.empty:
                return family_records.iloc[0, -1], 'intron'
            else:
                return confirmed_records.iloc[0, -1], ", ".join(confirmed_records['ID'].unique().tolist())

        sgRNA_seqrecords = []
        sgRNA_dataframe = pd.DataFrame(
            columns=['sgRNA_id', 'sgRNA_position', 'sgRNA_strand', 'sgRNA_seq', 'sgRNA_seq_html', 'sgRNA_GC',
                     'sgRNA_family', 'sgRNA_type'])
        pam_regex = create_regex_patterns(pam, spacerLength, sgRNAModule)
        for idx, sgRNA in enumerate(re.finditer(pam_regex, target_seq)):
            sgRNA_seq = sgRNA.group()
            sgRNA_seq_html = str(
                "<span style='font-weight:900'>" + sgRNA_seq + '</span>' + '</br>' + '|' * len(sgRNA_seq) + '</br>' + Seq(
                    sgRNA_seq).complement())
            sgRNA_id = 'Guide_' + str(idx)
            sgRNA_GC = str('{:.2f}'.format((sgRNA_seq.count('C') + sgRNA_seq.count('G')) / len(sgRNA_seq) * 100)) + '%'
            sgRNA_position_start = target_start + sgRNA.start()
            sgRNA_position_end = target_start + sgRNA.end() - 1
            sgRNA_position = str(target_seqid) + ':' + str(sgRNA_position_start)
            sgRNA_family, sgRNA_type = ontarget_apply()
            sgRNA_seqrecord = SeqRecord(Seq(sgRNA_seq), sgRNA_id, '', '')
            sgRNA_seqrecords.append(sgRNA_seqrecord)
            sgRNA_dataframe.loc[idx] = [sgRNA_id, sgRNA_position, "5'------3'", sgRNA_seq, sgRNA_seq_html, sgRNA_GC,
                                        sgRNA_family, sgRNA_type]
        sgRNA_reverse_seqrecords = []
        sgRNA_reverse_dataframe = pd.DataFrame(
            columns=['sgRNA_id', 'sgRNA_position', 'sgRNA_strand', 'sgRNA_seq', 'sgRNA_seq_html', 'sgRNA_GC',
                     'sgRNA_family', 'sgRNA_type'])
        for idx, sgRNA_reverse in enumerate(re.finditer(pam_regex, target_seq_reverse)):
            sgRNA_reverse_seq = sgRNA_reverse.group()
            sgRNA_reverse_seq_html = str(Seq(sgRNA_reverse_seq).complement()[::-1] + '</br>' + '|' * len(
                sgRNA_reverse_seq) + '</br>' + "<span style='font-weight:900'>" + sgRNA_reverse_seq[::-1] + '</span>')
            sgRNA_reverse_id = 'Guide_reverse_' + str(idx)
            sgRNA_reverse_GC = str('{:.2f}'.format(
                (sgRNA_reverse_seq.count('C') + sgRNA_reverse_seq.count('G')) / len(sgRNA_reverse_seq) * 100)) + '%'
            sgRNA_reverse_position_end = target_end - sgRNA_reverse.start() - 1
            sgRNA_reverse_position_start = target_end - sgRNA_reverse.end()
            sgRNA_reverse_position = target_seqid + ':' + str(sgRNA_reverse_position_end)
            sgRNA_reverse_family, sgRNA_reverse_type = ontarget_apply()
            sgRNA_reverse_seqrecord = SeqRecord(Seq(sgRNA_reverse_seq), sgRNA_reverse_id, '', '')
            sgRNA_reverse_seqrecords.append(sgRNA_reverse_seqrecord)
            sgRNA_reverse_dataframe.loc[idx] = [sgRNA_reverse_id, sgRNA_reverse_position, "3'------5'", sgRNA_reverse_seq,
                                                sgRNA_reverse_seq_html, sgRNA_reverse_GC, sgRNA_reverse_family,
                                                sgRNA_reverse_type]
        sgRNA_dataframe = pd.concat([sgRNA_dataframe, sgRNA_reverse_dataframe])
        sgRNA_dataframe.reset_index(inplace=True, drop=True)
        sgRNA_json = sgRNA_dataframe.to_json(orient='records')
        json_handle = json.loads(sgRNA_json)
        json_handle = {'total': len(json_handle), 'rows': json_handle}
        with open('{}/Guide.json'.format(task_path), 'w') as file_handle:
            json.dump(json_handle, file_handle)
        SeqIO.write(sgRNA_seqrecords + sgRNA_reverse_seqrecords, '{}/Guide.fasta'.format(task_path), 'fasta')
        return sgRNA_dataframe, sgRNA_json
    except Exception as e:
        if task_logger:
            task_logger.error(f"生成sgRNA数据时发生错误: {str(e)}", exc_info=True)
        raise e


def target_to_ontarget(gff_path, gff_pkl_path, target_seqid, target_start, target_end, task_logger=None):
    try:
        gff_bed = pybedtools.BedTool(gff_path)
        target_bed = pybedtools.BedTool(f"{target_seqid}\t{target_start}\t{target_end}\t.\t.\t+", from_string=True)
        overlaps = gff_bed.intersect(target_bed, wa=True, u=True)
        df = pd.read_pickle(gff_pkl_path)
        family_records = pd.DataFrame()
        for feature in overlaps:
            matches = df[
                (df['seqid'] == feature.chrom) & (df['start'] == int(feature.start) + 1) & (df['end'] == int(feature.end))]
            family_records = pd.concat([family_records, matches], ignore_index=True)
        return family_records
    except Exception as e:
        if task_logger:
            task_logger.error(f"获取目标区域基因家族信息时发生错误: {str(e)}", exc_info=True)
        raise e


def sequence_and_position_to_target_seq(fa_path, fasta_sequence_position, spacer_length, task_logger=None):
    try:
        with pysam.FastaFile(fa_path) as genome_handle:
            seqid = fasta_sequence_position['seqid']
            start = fasta_sequence_position['start']
            end = fasta_sequence_position['end']
            chromosome_length = genome_handle.get_reference_length(seqid)
            target_start = max(start - spacer_length, 1)
            target_end = min(end + spacer_length, chromosome_length)
            target_seq = genome_handle.fetch(seqid, target_start, target_end)
            target_seq_reverse = str(Seq(target_seq).reverse_complement())
        return target_start, target_end, target_seq, target_seq_reverse
    except Exception as e:
        if task_logger:
            task_logger.error(f"生成目标序列时发生错误: {str(e)}", exc_info=True)
        raise e


def create_regex_patterns(pam, spacerLength, sgRNAModule='spacerpam'):
    pam_regex = ''.join(IUPAC_dict[nuc] for nuc in pam if nuc in IUPAC_dict)
    if sgRNAModule == 'spacerpam':
        return rf'\w{{{spacerLength}}}{pam_regex}'
    elif sgRNAModule == 'pamspacer':
        return rf'{pam_regex}\w{{{spacerLength}}}'


# 位置（position）：如 chr1:1000-2000
# FASTA 格式序列（seq）：如 >example\nATCGATCG... 或纯序列 ATCGATCG...
# 基因位点（locus）：如 Ghir_A01G000010
def input_sequence_to_fasta_sequence_position(inputSequence, task_logger=None):
    try:
        import re
        input_type = {"locus": None, "position": None, "seq": None}
        normalized_seq = inputSequence.replace('\r\n', '\n').replace('\r', '\n')
        if re.search(r'^[\w.-]+:\d+-\d+$', inputSequence):
            input_type['position'] = inputSequence
        elif re.fullmatch(r'(>[^\n]*\n)?[ACGT\n]+', normalized_seq, re.IGNORECASE):
            input_type['seq'] = inputSequence
        else:
            input_type['locus'] = inputSequence
        return input_type
    except Exception as e:
        if task_logger:
            task_logger.error(f"识别输入序列类型时发生错误: {str(e)}", exc_info=True)
        raise e


def input_type_to_sequence_and_position(task_path, blastb_software, input_type, fa_path, gff_pkl_path, task_id, task_logger=None):
    try:
        import os
        import pandas as pd
        from Bio import SeqIO
        import pysam

        if input_type['seq']:
            seq = input_type['seq']
            if not seq.startswith(">"):
                seq = f'>{task_id}\n{seq}'
            seq_path = os.path.join(task_path, f'{task_id}.fasta')
            with open(seq_path, 'w') as seq_file:
                seq_file.write(seq)

            blastn_command = [
                blastb_software,
                "-query", seq_path,
                "-db", fa_path,
                "-perc_identity", "100", "-max_target_seqs", "1", "-qcov_hsp_perc", "100",
                "-out", f"{task_path}/{task_id}.blastn.out6",
                "-outfmt",
                "6 qseqid sseqid pident length mismatch gapopen qstart qend sstart send evalue bitscore qlen qcovhsp",
                "-num_threads", "12"
            ]
            if task_logger:
                task_logger.info(f"执行BLAST命令: {' '.join(blastn_command)}")
            try:
                result = subprocess.run(blastn_command, check=True, text=True, capture_output=True)
                if task_logger:
                    task_logger.info(result.stdout)
            except subprocess.CalledProcessError as e:
                if task_logger:
                    task_logger.error(f"BLASTn failed: {e.stderr}")
            with open(f"{task_path}/{task_id}.blastn.out6") as blastn_out_file:
                first_line = blastn_out_file.readline().strip()
                if first_line:
                    first_record = first_line.split('\t')
                    seqid = first_record[1]
                    # start = int(first_record[8])
                    pos1 = int(first_record[8])
                    # end = int(first_record[9])
                    pos2 = int(first_record[9])
                    start = min(pos1, pos2)
                    end = max(pos1, pos2)
                    blastnfmt6_100_dict = {'seqid': seqid, 'start': start, 'end': end}
                    sequence = str(next(SeqIO.parse(f'{task_path}/{task_id}.fasta', 'fasta')).seq)
                    return sequence, blastnfmt6_100_dict
                else:
                    return 0, 1
        if input_type['position']:
            seqid, position_range = input_type['position'].split(':')
            start, end = position_range.split('-')
            start, end = int(start), int(end)
            sequence = pysam.FastaFile(fa_path).fetch(seqid, start, end)
            blastnfmt6_100_dict = {'seqid': seqid, 'start': start, 'end': end}
            return sequence, blastnfmt6_100_dict
        if input_type['locus']:
            genome_handle = pysam.FastaFile(fa_path)
            gff_pandas = pd.read_pickle(gff_pkl_path)
            locus = gff_pandas.loc[gff_pandas['ID'] == input_type['locus'], ['seqid', 'start', 'end']].iloc[0].tolist()
            seqid, start, end = locus[0], int(locus[1]), int(locus[2])
            return genome_handle.fetch(seqid, start, end), {"seqid": seqid, "start": start, "end": end}
        return 0, 1
    except Exception as e:
        if task_logger:
            task_logger.error(f"获取序列和位置信息时发生错误: {str(e)}", exc_info=True)
        raise e