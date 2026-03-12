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
from .models import result_base_editor_list

# 配置日志
logger = logging.getLogger(__name__)

# 标准遗传密码子表 (Standard Genetic Code)
CODON_TABLE = {
    # 第一个碱基：A
    'AAA': 'K', 'AAC': 'N', 'AAG': 'K', 'AAT': 'N',
    'ACA': 'T', 'ACC': 'T', 'ACG': 'T', 'ACT': 'T',
    'AGA': 'R', 'AGC': 'S', 'AGG': 'R', 'AGT': 'S',
    'ATA': 'I', 'ATC': 'I', 'ATG': 'M', 'ATT': 'I',

    # 第一个碱基：C
    'CAA': 'Q', 'CAC': 'H', 'CAG': 'Q', 'CAT': 'H',
    'CCA': 'P', 'CCC': 'P', 'CCG': 'P', 'CCT': 'P',
    'CGA': 'R', 'CGC': 'R', 'CGG': 'R', 'CGT': 'R',
    'CTA': 'L', 'CTC': 'L', 'CTG': 'L', 'CTT': 'L',

    # 第一个碱基：G
    'GAA': 'E', 'GAC': 'D', 'GAG': 'E', 'GAT': 'D',
    'GCA': 'A', 'GCC': 'A', 'GCG': 'A', 'GCT': 'A',
    'GGA': 'G', 'GGC': 'G', 'GGG': 'G', 'GGT': 'G',
    'GTA': 'V', 'GTC': 'V', 'GTG': 'V', 'GTT': 'V',

    # 第一个碱基：T
    'TAA': '*', 'TAC': 'Y', 'TAG': '*', 'TAT': 'Y',
    'TCA': 'S', 'TCC': 'S', 'TCG': 'S', 'TCT': 'S',
    'TGA': '*', 'TGC': 'C', 'TGG': 'W', 'TGT': 'C',
    'TTA': 'L', 'TTC': 'F', 'TTG': 'L', 'TTT': 'F'
}

# 氨基酸理化性质分类（按极性、电荷等）
AA_PROPERTIES = {
    # 非极性（疏水）氨基酸
    'A': 'nonpolar', 'I': 'nonpolar', 'L': 'nonpolar', 'M': 'nonpolar',
    'F': 'nonpolar', 'W': 'nonpolar', 'P': 'nonpolar', 'V': 'nonpolar', 'G': 'nonpolar',

    # 极性不带电荷（亲水）氨基酸
    'S': 'polar', 'T': 'polar', 'C': 'polar', 'Y': 'polar', 'N': 'polar', 'Q': 'polar',

    # 带正电荷（碱性）氨基酸
    'K': 'positive', 'R': 'positive', 'H': 'positive',

    # 带负电荷（酸性）氨基酸
    'D': 'negative', 'E': 'negative',

    # 终止密码子
    '*': 'stop'
}

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

def setup_task_logger(task_path, task_id):
    """为每个任务设置独立的日志文件"""
    # 创建日志目录
    log_dir = os.path.join(task_path, 'logs')
    os.makedirs(log_dir, exist_ok=True)
    
    # 创建任务特定的日志记录器
    logger_name = f"base_editor_task_{task_id}"
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
        'NYN': ['spacerpam', 20]
    }
    sgRNAModule, spacerLength = pam_dict[pamType]
    return sgRNAModule, spacerLength

def form2Database(task_id, inputSequence, pam, spacerLength, sgRNAModule, name_db, base_editor_type, base_editing_window):
    conda_env_path = settings.CONDA_ENV_PATH
    # 确保应用的任务目录和临时目录存在
    tasks_base_dir = os.path.join(settings.BASE_DIR, 'work', 'baseEditorTasks')
    tmp_base_dir = os.path.join(settings.BASE_DIR, 'work', 'baseEditorTmp')
    os.makedirs(tasks_base_dir, exist_ok=True)
    os.makedirs(tmp_base_dir, exist_ok=True)
    
    task_path = os.path.join(settings.BASE_DIR, 'work', 'baseEditorTasks', str(task_id))
    os.makedirs(task_path, exist_ok=True)
    
    # 为当前任务设置日志记录器
    task_logger = setup_task_logger(task_path, task_id)
    
    try:
        blastb_software = settings.BLAST_SOFTWARE
        fa_path = os.path.join(settings.BASE_DIR, 'database', 'TargetGenome', name_db, f'{name_db}.fa')
        gff_path = os.path.join(settings.BASE_DIR, 'database', 'TargetGenomeGff', name_db, f'{name_db}.gff')
        gff_pkl_path = os.path.join(settings.BASE_DIR, 'database', 'TargetGenomeGff', name_db, f'{name_db}.gff.pkl')
        batmis_path = settings.BATMIS_BIN

        # 0、获取或创建任务记录
        try:
            base_editor_task_record = result_base_editor_list.objects.get(task_id=task_id)
        except result_base_editor_list.DoesNotExist:
            base_editor_task_record = result_base_editor_list(
                task_id=task_id, 
                input_sequence=inputSequence, 
                pam_type=pam,
                spacer_length=spacerLength, 
                sgRNA_module=sgRNAModule, 
                name_db=name_db,
                base_editor_type=base_editor_type,
                base_editing_window=base_editing_window,
                task_status='running', 
                sequence_position='')
            base_editor_task_record.save()

        # 1、识别输入序列类型
        input_type = input_sequence_to_fasta_sequence_position(inputSequence, task_logger)
        # 3、获取序列和位置信息
        fasta_sequence, fasta_sequence_position = input_type_to_sequence_and_position(
            task_path, blastb_software, input_type, fa_path, gff_pkl_path, task_id, task_logger)
        fasta_sequence_position_json = json.dumps(fasta_sequence_position)
        if fasta_sequence:
            base_editor_task_record.sequence_position = fasta_sequence_position_json
            base_editor_task_record.save()
        else:
            base_editor_task_record.task_status = 'failed'
            base_editor_task_record.log = 'empty BLAST result'
            base_editor_task_record.save()
            task_logger.error(f"任务 {task_id} 失败: 未能获取序列和位置信息")
            return "未能获取序列和位置信息"

        # 4、生成目标序列
        target_start, target_end, target_seq, target_seq_reverse = sequence_and_position_to_target_seq(
            fa_path, fasta_sequence_position, spacerLength, task_logger)
        # 5、获取目标区域基因家族信息
        family_records = target_to_ontarget(gff_path, gff_pkl_path, fasta_sequence_position['seqid'], 
                                          target_start, target_end, task_logger)
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
            base_editor_task_record.task_status = 'failed'
            base_editor_task_record.log = f"错误：目标区域包含 {len(unique_gene_ids)} 个不同基因（{unique_gene_ids.tolist()}），超过最大允许数量（2）。"
            base_editor_task_record.save()
            task_logger.error(f"任务 {task_id} 失败: 基因数量超限")
            return "目标区域包含基因数量超过限制（最多2个）"

        # 6、生成 sgRNA 候选列表
        sgRNA_dataframe, sgRNA_json = generate_sgRNA_dataframe(family_records, target_seq, target_seq_reverse, target_start,
                                                               target_end, fasta_sequence_position['seqid'], pam,
                                                               spacerLength, sgRNAModule, task_path, task_logger)
                
        # 6.5、根据输入类型决定是否过滤 sgRNA
        # 自定义序列输入（input_type['seq']）不过滤外显子，保留整个序列上的所有 sgRNA
        # 基因 ID 或位置范围输入需要过滤，只保留外显子上的 sgRNA
        if input_type['seq']:
            task_logger.info("自定义序列输入，保留整个序列上的所有 sgRNA（不过滤外显子）")
        else:
            task_logger.info("基因 ID 或位置范围输入，过滤只保留外显子上的 sgRNA")
            sgRNA_dataframe = filter_sgRNA_by_exon(sgRNA_dataframe, family_records, task_logger)
                
        # 重新生成 JSON
        sgRNA_json = sgRNA_dataframe.to_json(orient='records')
        json_handle = json.loads(sgRNA_json)
        json_handle = {'total': len(json_handle), 'rows': json_handle}
        with open('{}/Guide.json'.format(task_path), 'w') as file_handle:
            json.dump(json_handle, file_handle)
        # 7、运行 BATMAN 工具进行 off-target 分析
        sam_file, intersect_file = run_batman(conda_env_path, task_path, fa_path, gff_path, task_id, batmis_path, task_logger)
        sam_pandas, intersect_pandas = intersect_to_pandas(fa_path, sam_file, intersect_file, spacerLength, pam, task_logger)
        # 8、将 off-target 数据转换为 JSON
        guide_json = sam_intersect_pandas_to_json(sam_pandas, intersect_pandas, task_path, task_logger)

        # 9、对每个sgRNA进行碱基编辑分析
        guide_json = perform_base_editing_analysis(guide_json, fa_path, base_editor_type, base_editing_window, 
                                                 fasta_sequence_position['seqid'], target_start, target_end, task_logger)

        # 10、添加 JBrowse 可视化信息并保存到文件
        json_file_path = add_Jbrowse_to_json(task_id, task_path, fasta_sequence_position_json, guide_json, 
                                       name_db, base_editor_type, base_editing_window, task_logger)
        return json_file_path
    except Exception as e:
        # 记录错误日志
        error_msg = f"任务 {task_id} 执行过程中发生错误: {str(e)}"
        task_logger.error(error_msg, exc_info=True)
        try:
            base_editor_task_record = result_base_editor_list.objects.get(task_id=task_id)
            base_editor_task_record.task_status = 'failed'
            base_editor_task_record.log = error_msg
            base_editor_task_record.save()
        except result_base_editor_list.DoesNotExist:
            pass
        return error_msg
    finally:
        # 清理日志处理器，防止文件句柄泄露
        logger_name = f"base_editor_task_{task_id}"
        task_logger = logging.getLogger(logger_name)
        for handler in task_logger.handlers[:]:
            handler.close()
            task_logger.removeHandler(handler)

def perform_base_editing_analysis(guide_json, fa_path, base_editor_type, base_editing_window, seqid, target_start, target_end, task_logger=None):
    """
    对每个sgRNA进行碱基编辑分析，增加TBE（T→G/A）完整支持。
    """
    try:
        window_start, window_end = map(int, base_editing_window.split('-'))
        with pysam.FastaFile(fa_path) as genome_handle:
            target_sequence = genome_handle.fetch(seqid, target_start, target_end).upper()

        edit_rules = {
            'ABE': {'A': 'G'},           # A to G
            'CBE': {'C': 'T'},           # C to T
            'GBE': {'C': 'G'},           # C to G
            'ABE+CBE': {'A': 'G', 'C': 'T'},  # A to G and C to T
            'TBE': {'T': ['G', 'A']}     # T to G or T to A
        }
        edit_rule = edit_rules.get(base_editor_type, {})

        for row in guide_json['rows']:
            sgRNA_seq = row['sgRNA_seq']
            sgRNA_position = row['sgRNA_position']
            sgRNA_strand = row['sgRNA_strand']
            pos = int(sgRNA_position.split(':')[1])

            # 编辑窗口坐标（根据正反链）
            if sgRNA_strand == "5'------3'":
                window_start_pos = pos - target_start + window_start - 1
                window_end_pos = pos - target_start + window_end
            else:
                window_start_pos = pos - target_start - window_end + 1
                window_end_pos = pos - target_start - window_start + 2

            window_start_pos = max(0, window_start_pos)
            window_end_pos = min(len(target_sequence), window_end_pos)
            window_seq = target_sequence[window_start_pos:window_end_pos]

            # 编辑窗口内执行碱基替换
            if base_editor_type == 'TBE':
                # 生成所有可能的替换组合（T→G 和 T→A）
                possible_edits = []
                for replacement in ['G', 'A']:
                    edited_seq = window_seq.replace('T', replacement)
                    aa_change_info = analyze_amino_acid_changes(window_seq, edited_seq, base_editor_type)
                    possible_edits.append({
                        'replacement': f"T→{replacement}",
                        'edited_seq': edited_seq,
                        'aa_change': aa_change_info['aa_change'],
                        'property_change': aa_change_info['property_change']
                    })
                # 合并结果描述
                merged_edited_seqs = [e['edited_seq'] for e in possible_edits]
                merged_aa_changes = [e['aa_change'] for e in possible_edits]
                merged_prop_changes = [e['property_change'] for e in possible_edits]

                base_edit_info = {
                    'editing_window': f"{window_start}-{window_end}",
                    'original_window_seq': window_seq,
                    'edited_window_seq': " | ".join(merged_edited_seqs),
                    'amino_acid_change': " | ".join(set(merged_aa_changes)),
                    'property_change': " | ".join(set(merged_prop_changes)),
                    'edit_type': base_editor_type
                }

            else:
                # 普通编辑类型（单一替换）
                edited_seq = window_seq
                for original_base, edited_base in edit_rule.items():
                    if original_base in window_seq:
                        edited_seq = edited_seq.replace(original_base, edited_base.lower())
                edited_seq = edited_seq.upper()
                aa_change_info = analyze_amino_acid_changes(window_seq, edited_seq, base_editor_type)

                base_edit_info = {
                    'editing_window': f"{window_start}-{window_end}",
                    'original_window_seq': window_seq,
                    'edited_window_seq': edited_seq,
                    'amino_acid_change': aa_change_info['aa_change'],
                    'property_change': aa_change_info['property_change'],
                    'edit_type': base_editor_type
                }

            row['base_editing_info'] = base_edit_info

        return guide_json

    except Exception as e:
        if task_logger:
            task_logger.error(f"碱基编辑分析过程中发生错误: {str(e)}", exc_info=True)
        for row in guide_json['rows']:
            row['base_editing_info'] = {
                'editing_window': base_editing_window,
                'original_window_seq': '',
                'edited_window_seq': '',
                'amino_acid_change': 'Analysis error',
                'property_change': 'Analysis error',
                'edit_type': base_editor_type
            }
        return guide_json


def analyze_amino_acid_changes(original_seq, edited_seq, base_editor_type):
    """
    分析氨基酸变化，兼容TBE的混合碱基序列。
    """
    try:
        # 处理非标准字符
        original_seq = re.sub(r'[^ATCG]', 'N', original_seq.upper())
        edited_seq = re.sub(r'[^ATCG]', 'N', edited_seq.upper())

        # 截取到3的倍数
        original_seq = original_seq[:len(original_seq) - len(original_seq) % 3]
        edited_seq = edited_seq[:len(edited_seq) - len(edited_seq) % 3]

        original_aa = ""
        edited_aa = ""

        for i in range(0, len(original_seq), 3):
            codon_original = original_seq[i:i+3]
            codon_edited = edited_seq[i:i+3]
            original_aa += CODON_TABLE.get(codon_original, "X")
            edited_aa += CODON_TABLE.get(codon_edited, "X")

        property_changes = []
        for i, (orig_aa, edit_aa) in enumerate(zip(original_aa, edited_aa)):
            if orig_aa != edit_aa:
                orig_prop = AA_PROPERTIES.get(orig_aa, 'unknown')
                edit_prop = AA_PROPERTIES.get(edit_aa, 'unknown')
                if orig_prop != edit_prop:
                    property_changes.append(f"{orig_aa}{i+1}{edit_aa}({orig_prop}→{edit_prop})")
                else:
                    property_changes.append(f"{orig_aa}{i+1}{edit_aa}")

        aa_change = f"{original_aa}→{edited_aa}" if original_aa != edited_aa else "No change"
        property_change = "; ".join(property_changes) if property_changes else "No change"

        return {
            'aa_change': aa_change,
            'property_change': property_change
        }
    except Exception:
        return {'aa_change': 'Analysis error', 'property_change': 'Analysis error'}



def add_Jbrowse_to_json(task_id, task_path, sequence_position, guide_json, name_db, base_editor_type, base_editing_window, task_logger=None):
    try:
        base_editor_task_record = result_base_editor_list.objects.get(task_id=task_id)
        
        # 准备碱基编辑信息列表
        base_editing_info_list = []
        for row in guide_json['rows']:
            if 'base_editing_info' in row:
                base_editing_info = row['base_editing_info'].copy()
                # 添加sgRNA相关信息到碱基编辑信息中
                base_editing_info.update({
                    'sgRNA_id': row['sgRNA_id'],
                    'sgRNA_seq': row['sgRNA_seq'],
                    'sgRNA_position': row['sgRNA_position'],
                    'sgRNA_strand': row['sgRNA_strand'],
                    'sgRNA_GC': row['sgRNA_GC'],
                    'sgRNA_family': row.get('sgRNA_family', ''),
                    'sgRNA_type': row.get('sgRNA_type', ''),
                    'offtarget_num': row.get('offtarget_num', 0),
                    'offtarget_json': row.get('offtarget_json', None)
                })
                base_editing_info_list.append(base_editing_info)

        json_handle = {
            "TableData": {
                "base_editor_type": base_editor_type,
                "base_editing_window": base_editing_window,
                "base_editing_info": {
                    "rows": base_editing_info_list,
                    "total": len(base_editing_info_list)
                }
            },
        }
        
        # 保存结果到JSON文件
        json_file_path = f'{task_path}/Guide.json3'
        with open(json_file_path, 'w') as file_handle:
            json.dump(json_handle, file_handle)
        
        # 返回相对路径
        relative_path = os.path.relpath(json_file_path, settings.BASE_DIR)
        return relative_path
    except Exception as e:
        if task_logger:
            task_logger.error(f"任务 {task_id} 添加结果信息时发生错误: {str(e)}", exc_info=True)
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
                # 修复：确保intersect_target_pandas始终被定义
                if not intersect_target_tmp_pandas.empty:
                    intersect_target_pandas = intersect_target_tmp_pandas[
                        intersect_target_tmp_pandas['type'] == 'gene'].drop_duplicates()
                else:
                    # 创建一个空的DataFrame，但具有必要的列结构
                    intersect_target_pandas = pd.DataFrame(columns=intersect_target_tmp_pandas.columns)
                
                intersect_target_pandas.to_csv(f'{task_path}/intersect_target_pandas_1.csv')
                if not intersect_target_pandas.empty:
                    intersect_target_pandas['types_list'] = intersect_target_tmp_pandas.groupby('family').apply(
                        lambda x: sorted(x.type.unique().tolist()))
                else:
                    intersect_target_pandas['types_list'] = []

                intersect_target_pandas.to_csv(f'{task_path}/intersect_target_pandas_2.csv')
                if not intersect_target_pandas.empty:
                    intersect_target_pandas['types'] = intersect_target_pandas.apply(
                        lambda row: 'intron' if len(row.types_list) == 2 else ', '.join(row.types_list).replace(', gene, mRNA',
                                                                                                                ''), axis=1)
                else:
                    intersect_target_pandas['types'] = []
                    
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
def filter_sgRNA_by_exon(sgRNA_dataframe, family_records, task_logger=None):
    """
    过滤 sgRNA，只保留完全位于外显子（exon）区域内的 sgRNA
    
    规则：
    1. sgRNA 必须完全在一个或多个外显子区域内（可以正好在边界上）
    2. 如果 sgRNA 超出外显子边界（即使只超出 1bp），也会被过滤掉
    
    参数:
        sgRNA_dataframe: 包含 sgRNA 信息的 DataFrame
        family_records: 基因家族记录 DataFrame
        task_logger: 日志记录器
    
    返回:
        过滤后的 sgRNA_dataframe
    """
    try:
        if sgRNA_dataframe.empty:
            return sgRNA_dataframe
        
        # 筛选出 featuretype 为'exon'的记录
        exon_records = family_records[family_records['featuretype'] == 'exon']
        
        if exon_records.empty:
            if task_logger:
                task_logger.warning("没有找到外显子记录，所有 sgRNA 将被过滤")
            # 如果没有外显子记录，返回空的 DataFrame
            return sgRNA_dataframe.iloc[0:0]
        
        # 创建一个列表来存储符合条件的 sgRNA 索引
        valid_indices = []
        filtered_count = 0
        
        # 遍历每个 sgRNA
        for idx, row in sgRNA_dataframe.iterrows():
            # 解析 sgRNA 位置
            seqid, pos_str = row['sgRNA_position'].split(':')
            sgRNA_start = int(pos_str)
            sgRNA_end = sgRNA_start + len(row['sgRNA_seq']) - 1
            
            # 检查该 sgRNA 是否完全在任何一个外显子区域内
            is_in_exon = False
            for _, exon_row in exon_records.iterrows():
                if exon_row['seqid'] == seqid:
                    exon_start = exon_row['start']
                    exon_end = exon_row['end']
                    
                    # 判断 sgRNA 是否完全在外显子内（包括正好在边界上）
                    # sgRNA_start >= exon_start 且 sgRNA_end <= exon_end
                    if sgRNA_start >= exon_start and sgRNA_end <= exon_end:
                        is_in_exon = True
                        break
            
            if is_in_exon:
                valid_indices.append(idx)
            else:
                filtered_count += 1
        
        # 过滤 sgRNA，只保留完全在外显子内的
        filtered_sgRNA = sgRNA_dataframe.loc[valid_indices].reset_index(drop=True)
        
        if task_logger:
            task_logger.info(f"sgRNA 过滤：原始 {len(sgRNA_dataframe)} 条，过滤后 {len(filtered_sgRNA)} 条，过滤掉 {filtered_count} 条（只保留完全在外显子内的 sgRNA）")
            if filtered_count > 0:
                task_logger.info(f"被过滤的 sgRNA 原因：超出外显子边界")
        
        return filtered_sgRNA
        
    except Exception as e:
        if task_logger:
            task_logger.error(f"过滤 sgRNA 时发生错误：{str(e)}", exc_info=True)
        # 如果出错，返回原始数据（不中断流程）
        return sgRNA_dataframe


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