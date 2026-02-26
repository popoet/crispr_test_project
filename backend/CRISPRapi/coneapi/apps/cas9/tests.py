import shlex
import subprocess
import re
import json
import os
import time
import pandas as pd
import pysam
from Bio import SeqIO
from Bio.Seq import Seq
from Bio.SeqRecord import SeqRecord
import pybedtools
from pybedtools import BedTool
from pandarallel import pandarallel

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

def form2Database(task_id, inputSequence, pam, spacerLength, sgRNAModule, name_db):
    # # 1、创建任务记录：
    # start_time = time.time()
    # cas9_task_record = result_cas9_list(task_id=task_id, input_sequence=inputSequence, pam_type=pam,
    #                                     spacer_length=spacerLength, sgRNA_module=sgRNAModule, name_db=name_db,
    #                                     task_status='running', sequence_position='')
    # cas9_task_record.save()
    # print(f"Create record: {time.time() - start_time:.2f} seconds")
    # 2、解析输入序列类型
    step_time = time.time()
    input_type = input_sequence_to_fasta_sequence_position(inputSequence)
    # print(f"Parse input sequence: {time.time() - step_time:.2f} seconds")
    # print(input_type)
    # 3、获取序列和位置信息
    fasta_sequence, fasta_sequence_position = input_type_to_sequence_and_position(input_type, name_db, task_id)
    fasta_sequence_position_json = json.dumps(fasta_sequence_position)
    # if fasta_sequence:
    #     cas9_task_record.sequence_position = fasta_sequence_position_json
    #     cas9_task_record.save()
    # else:
    #     cas9_task_record.task_status = 'failed'
    #     cas9_task_record.log = 'empty BLAST result'
    #     cas9_task_record.save()
    #     return 0
    # print(fasta_sequence)
    # print(fasta_sequence_position)
    # print(fasta_sequence_position_json)
    # print(f"Parse input sequence: {time.time() - step_time:.2f} seconds")
    # # 4、生成目标序列
    # step_time = time.time()
    # # target_start
    # # 扩展后的起始位置
    # # target_end
    # # 扩展后的结束位置
    # # target_seq
    # # 正向扩展区域的 DNA 序列
    # # target_seq_reverse
    # 反向互补的 DNA 序列
    target_start, target_end, target_seq, target_seq_reverse = sequence_and_position_to_target_seq(name_db,
                                                                                                   fasta_sequence_position,
                                                                                                   spacerLength)
    # print(target_start, target_end, target_seq, target_seq_reverse)
    # print(f"Generate target sequence: {time.time() - step_time:.2f} seconds")
    # 5、获取目标区域基因家族信息
    step_time = time.time()
    print(name_db, fasta_sequence_position['seqid'], target_start, target_end)
    family_records = target_to_ontarget(name_db, fasta_sequence_position['seqid'], target_start, target_end)
    # print(f"Get family records: {time.time() - step_time:.2f} seconds")
    # 6、生成 sgRNA 候选列表
    step_time = time.time()
    sgRNA_dataframe, sgRNA_json = generate_sgRNA_dataframe(family_records, target_seq, target_seq_reverse, target_start,
                                                           target_end, fasta_sequence_position['seqid'], pam,
                                                           spacerLength, sgRNAModule, task_id)
    # print(sgRNA_dataframe, sgRNA_json)
    # print(f"Generate sgRNA dataframe and JSON: {time.time() - step_time:.2f} seconds")
    # 7、运行 BATMAN 工具进行 off-target 分析
    step_time = time.time()
    sam_file, intersect_file = run_batman(f'/mhds/hd6platform/biohuaxing/zhangxueke/Project/CRISPRone/backend/CRISPRapi/coneapi/apps/cas9/t/task_output/{task_id}', name_db, task_id)
    sam_pandas, intersect_pandas = intersect_to_pandas(sam_file, intersect_file, spacerLength, name_db, pam)
    print(sam_pandas, intersect_pandas)
    print(f"Run BATMAN and intersection analysis: {time.time() - step_time:.2f} seconds")
    # 8、将 off-target 数据转换为 JSON
    step_time = time.time()
    guide_json = sam_intersect_pandas_to_json(sam_pandas, intersect_pandas, f'/mhds/hd6platform/biohuaxing/zhangxueke/Project/CRISPRone/backend/CRISPRapi/coneapi/apps/cas9/t/task_output/{task_id}')
    # print(guide_json)
    print(f"Convert intersection data to JSON: {time.time() - step_time:.2f} seconds")

    # 9、将 JSON 保存到数据库，添加 JBrowse 可视化信息
    # step_time = time.time()
    # cas9_task_record.sgRNA_json = guide_json
    # cas9_task_record.task_status = 'finished'
    # cas9_task_record.save()
    # print(f"Total time: {time.time() - start_time:.2f} seconds")

    # step_time = time.time()
    # add_Jbrowse_to_json(task_id, guide_json)
    # print(f"JSON3 is generated: {time.time() - step_time:.2f} seconds")

    return 0


def add_Jbrowse_to_json(task_id, guide_json):
    cas9_task_record = result_cas9_list.objects.get(task_id=task_id)
    sequence_position = json.loads(cas9_task_record.sequence_position)
    json_handle = {"TableData": {"json_data": guide_json},
                   "JbrowseInfo": {
                       "assembly": {
                           "name": cas9_task_record.name_db,
                           "fasta": f"http://crisprall.hzau.edu.cn/CRISPRone_data/genome_files/{cas9_task_record.name_db}.fa",
                           "fai": f"http://crisprall.hzau.edu.cn/CRISPRone_data/genome_files/{cas9_task_record.name_db}.fa.fai"
                       },
                       "tracks": {
                           "name": cas9_task_record.name_db,
                           "gff3_gz": f"http://crisprall.hzau.edu.cn/cas9_Jbrowse_API?task_id={task_id}&file_type=gff3.gz",
                           "gff3_tbi": f"http://crisprall.hzau.edu.cn/cas9_Jbrowse_API?task_id={task_id}&file_type=gff3.gz.csi"
                       },
                       "position": f"{sequence_position['seqid']}:{sequence_position['start']}..{sequence_position['end']}"
                   }}
    cas9_task_record.sgRNA_with_JBrowse_json = json_handle
    cas9_task_record.save()
    cas9_task_record.refresh_from_db()
    print(f'json_handle:{json_handle}')
    print(f'cas9_task_record.sgRNA_with_JBrowse_json:{cas9_task_record.sgRNA_with_JBrowse_json}')
    with open(f'/tmp/CRISPRone/{task_id}/Guide.json3', 'w') as file_handle:
        json.dump(json_handle, file_handle)


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

def sam_intersect_pandas_to_json(sam_pandas, intersect_pandas, task_path):
    print(f"sam_pandas_name\n{sam_pandas.columns}")
    print(f"sam_pandas_10\n{sam_pandas.head(10)}")
    print(f"intersect_pandas_name\n{intersect_pandas.columns}")
    print(f"sam_pandas_shape\n{sam_pandas.shape}")
    print(f"intersect_pandas_10\n{intersect_pandas.head(10)}")
    print(f"intersect_pandas_shape\n{intersect_pandas.shape}")
    intersect_pandas.to_csv(f'{task_path}/intersect_pandas.csv')
    intersect_pandas.to_pickle(f'{task_path}/intersect_pandas.plk')
    sam_pandas.to_csv(f'{task_path}/sam_pandas.csv')
    sam_pandas.to_pickle(f'{task_path}/sam_pandas.plk')
    # def merge_extract(row):
    #     if len(sam_pandas.loc[(row['seqid'], row['sgRNA_start'])]) != 10:
    #         print(f"seqid={row['seqid']}, sgRNA_start={row['sgRNA_start']}")
    #         print(f"sam_pandas.loc[(row['seqid'], row['sgRNA_start'])]\n{sam_pandas.loc[(row['seqid'], row['sgRNA_start'])]}")
    #     return sam_pandas.loc[(row['seqid'], row['sgRNA_start'])].iloc[0]
    sam_pandas_reset = sam_pandas.reset_index(drop=True)
    merged_pandas = intersect_pandas.merge(sam_pandas_reset, left_on=['seqid', 'sgRNA_start'],
                                           right_on=['rname', 'pos_0_base'], how='left')
    # merged_pandas.to_csv(f'{task_path}/merged_pandas.csv')
    # merged_pandas['family'] = merged_pandas['attributes'].str.extract(r'Family=([^;]+)', expand=False)
    merged_pandas['family'] = merged_pandas['attributes'].apply(sam_intersect_pandas_to_json_extract_family)
    merged_pandas.to_csv(f'{task_path}/merged_pandas.csv')
    # if len(sam_pandas) < 1000:
    #     intersect_pandas[
    #         ['qname', 'flag', 'rname', 'pos', 'seq', 'NM', 'MD', 'pos_end', 'rseq', 'pos_0_base']
    #         ] = intersect_pandas.apply(lambda row: merge_extract(row), axis=1, result_type='expand')
    #     intersect_pandas['family'] = intersect_pandas['attributes'].str.extract(r'Family=([^;]+)', expand=False)
    # else:
    #     intersect_pandas[
    #         ['qname', 'flag', 'rname', 'pos', 'seq', 'NM', 'MD', 'pos_end', 'rseq', 'pos_0_base']
    #         ] = intersect_pandas.apply(lambda row: merge_extract(row), axis=1, result_type='expand')
    #     intersect_pandas['family'] = intersect_pandas['attributes'].str.extract(r'Family=([^;]+)', expand=False)
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
            # 返回的是多行、family 为索引的 Series，pandas 无法把它按顺序贴进 intersect_target_pandas 的每一行。
            intersect_target_pandas['types_list'] = intersect_target_tmp_pandas.groupby('family').apply(
                lambda x: sorted(x.type.unique().tolist()))
            # 你需要将 groupby().apply() 的结果转为 DataFrame，然后用 merge 或 map 的方式加回去。
            # family_to_types = intersect_target_tmp_pandas.groupby('family')['type'].apply(
            #     lambda x: sorted(x.unique().tolist()))
            #
            # # # 先 reset_index 再使用 'family' 列
            # # intersect_target_pandas.reset_index(level='family', inplace=True)
            # intersect_target_pandas['types_list'] = intersect_target_pandas['family'].map(family_to_types)

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


def intersect_to_pandas(sam_file, intersect_file, spacerLength, name_db, pam):
    def fetch_rseq(row):
        print(f"Fetching sequence for row: rname={row['rname']}, pos={row['pos'] - 1}, pos_end={row['pos_end'] - 1}")
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
    genome_handle = pysam.FastaFile(f'/mhds/hd6platform/biohuaxing/zhangxueke/Project/CRISPRone/backend/CRISPRapi/coneapi/database/TargetGenome/{name_db}.fa')
    sam_pandas['NM'] = sam_pandas['NM'].str.replace('NM:i:', '')
    sam_pandas['MD'] = sam_pandas['MD'].str.replace('MD:Z:', '')
    sam_pandas['pos_end'] = sam_pandas['pos'] + spacerLength
    # sam_pandas['rseq'] = sam_pandas.apply(lambda row: genome_handle.fetch(row['rname'], row['pos']-1, row['pos_end']-1 + pam_length), axis=1, result_type='expand')
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


def run_batman(task_path, name_db, task_id):
    command_envir = "export LD_LIBRARY_PATH=$CONDA_PREFIX/lib:$LD_LIBRARY_PATH"
    command_batman = f'/mhds/hd6platform/biohuaxing/zhangxueke/Project/CRISPRone/backend/CRISPRapi/coneapi/software/batmis/bin/batman -q {task_path}/Guide.fasta -g /mhds/hd6platform/biohuaxing/zhangxueke/Project/CRISPRone/backend/CRISPRapi/coneapi/database/TargetGenome/{name_db}.fa -n 5 -mall -l /dev/null -o {task_path}/{task_id}.bin 1> /dev/null'
    command_batdecode = f'/mhds/hd6platform/biohuaxing/zhangxueke/Project/CRISPRone/backend/CRISPRapi/coneapi/software/batmis/bin/batdecode -i {task_path}/{task_id}.bin -g /mhds/hd6platform/biohuaxing/zhangxueke/Project/CRISPRone/backend/CRISPRapi/coneapi/database/TargetGenome/{name_db}.fa -o {task_path}/{task_id}.txt'
    command_samtools = f'~/.conda/envs/crispr/bin/samtools view -bS {task_path}/{task_id}.txt > {task_path}/{task_id}.bam'
    command_bedtools = f'~/.conda/envs/crispr/bin/bedtools intersect -a {task_path}/{task_id}.bam -b /mhds/hd6platform/biohuaxing/zhangxueke/Project/CRISPRone/backend/CRISPRapi/coneapi/database/TargetGenomeGff/{name_db}.gff -wo -bed > {task_path}/{task_id}.intersect'
    print("command_envir:", command_envir)
    print("command_batman:", command_batman)
    print("command_batdecode:", command_batdecode)
    print("command_samtools:", command_samtools)
    print("command_bedtools:", command_bedtools)
    os.system(command_envir)
    os.system(command_batman)
    os.system(command_batdecode)
    os.system(command_samtools)
    os.system(command_bedtools)
    return f'{task_path}/{task_id}.txt', f'{task_path}/{task_id}.intersect'


# 根据目标 DNA 序列（正向和反向互补）识别潜在的 sgRNA 位点，并将这些候选位点整理成结构化数据（DataFrame 和 JSON），供后续分析使用。
def generate_sgRNA_dataframe(family_records, target_seq, target_seq_reverse, target_start, target_end, target_seqid,
                             pam, spacerLength, sgRNAModule, task_id):
    task_path = f'/mhds/hd6platform/biohuaxing/zhangxueke/Project/CRISPRone/backend/CRISPRapi/coneapi/apps/cas9/t/task_output/{task_id}'

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
    print("家族记录：", family_records)
    print("pam_regex：", pam_regex)
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


def target_to_ontarget(name_db, target_seqid, target_start, target_end):
    # base_path = f"/mhds/hd6platform/biohuaxing/zhangxueke/Project/CRISPRone/backend/CRISPRapi/coneapi/database/TargetGenome/{name_db}"
    # gff_path = f"{base_path}.gff"
    # pickle_path = f"{base_path}.gff.pkl"
    gff_path = '/mhds/hd6platform/biohuaxing/zhangxueke/Project/CRISPRone/backend/CRISPRapi/coneapi/database/TargetGenomeGff/Gossypium_hirsutum_T2T-Jin668_HZAU_genome.gff'
    pickle_path = '/mhds/hd6platform/biohuaxing/zhangxueke/Project/CRISPRone/backend/CRISPRapi/coneapi/database/TargetGenomeGff/Gossypium_hirsutum_T2T-Jin668_HZAU_genome.gff.pkl'
    gff_bed = pybedtools.BedTool(gff_path)
    target_bed = pybedtools.BedTool(f"{target_seqid}\t{target_start}\t{target_end}\t.\t.\t+", from_string=True)
    overlaps = gff_bed.intersect(target_bed, wa=True, u=True)
    df = pd.read_pickle(pickle_path)
    family_records = pd.DataFrame()
    for feature in overlaps:
        matches = df[
            (df['seqid'] == feature.chrom) & (df['start'] == int(feature.start) + 1) & (df['end'] == int(feature.end))]
        family_records = pd.concat([family_records, matches], ignore_index=True)
    return family_records


def sequence_and_position_to_target_seq(name_db, fasta_sequence_position, spacer_length):
    with pysam.FastaFile(f"/mhds/hd6platform/biohuaxing/zhangxueke/Project/CRISPRone/backend/CRISPRapi/coneapi/database/TargetGenome/{name_db}.fa") as genome_handle:
        seqid = fasta_sequence_position['seqid']
        start = fasta_sequence_position['start']
        end = fasta_sequence_position['end']
        chromosome_length = genome_handle.get_reference_length(seqid)
        target_start = max(start - spacer_length, 1)
        target_end = min(end + spacer_length, chromosome_length)
        print(f'seqid: {seqid}, target_start: {target_start}, target_end: {target_end}')
        target_seq = genome_handle.fetch(seqid, target_start, target_end)
        target_seq_reverse = str(Seq(target_seq).reverse_complement())
    return target_start, target_end, target_seq, target_seq_reverse


def create_regex_patterns(pam, spacerLength, sgRNAModule='spacerpam'):
    pam_regex = ''.join(IUPAC_dict[nuc] for nuc in pam if nuc in IUPAC_dict)
    if sgRNAModule == 'spacerpam':
        return rf'\w{{{spacerLength}}}{pam_regex}'
    elif sgRNAModule == 'pamspacer':
        return rf'{pam_regex}\w{{{spacerLength}}}'


# 位置（position）：如 chr1:1000-2000
# FASTA 格式序列（seq）：如 >example\nATCGATCG... 或纯序列 ATCGATCG...
# 基因位点（locus）：如 Ghir_A01G000010
def input_sequence_to_fasta_sequence_position(inputSequence):
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


# input_type_to_sequence_and_position
#     │
#     └─── if input_type['seq']:
#             ├── 写入 FASTA 文件
#             ├── BLASTn 比对
#             └── 解析输出 → 获取 seqid/start/end
#
#     └─── elif input_type['position']:
#             ├── 解析 chr:start-end
#             └── pysam 提取序列
#
#     └─── elif input_type['locus']:
#             ├── 读取 GFF.pkl
#             ├── 查找基因位置
#             └── pysam 提取序列
def input_type_to_sequence_and_position(input_type, name_db, task_id):
    import os
    import pandas as pd
    from Bio import SeqIO
    import pysam
    task_path = f'/mhds/hd6platform/biohuaxing/zhangxueke/Project/CRISPRone/backend/CRISPRapi/coneapi/work/gsuserzxktest'
    os.makedirs(task_path, exist_ok=True)
    if input_type['seq']:
        seq = input_type['seq']
        if not seq.startswith(">"):
            seq = f'>{task_id}\n{seq}'
        seq_path = os.path.join(task_path, f'{task_id}.fasta')
        with open(seq_path, 'w') as seq_file:
            seq_file.write(seq)
        blastn_command = [
            "/home/Project/SugarcaneProject/sugarcaneapi/software/blast/bin/blastn",
            "-query", seq_path,
            "-db", f"/mhds/hd6platform/biohuaxing/zhangxueke/Project/CRISPRone/backend/CRISPRapi/coneapi/database/TargetGenome/{name_db}.fa",
            "-perc_identity", "100", "-max_target_seqs", "1", "-qcov_hsp_perc", "100",
            "-out", f"{task_path}/{task_id}.blastn.out6",
            "-outfmt",
            "6 qseqid sseqid pident length mismatch gapopen qstart qend sstart send evalue bitscore qlen qcovhsp",
            "-num_threads", "12"
        ]
        try:
            result = subprocess.run(blastn_command, check=True, text=True, capture_output=True)
            print(result.stdout)
        except subprocess.CalledProcessError as e:
            print(f"BLASTn failed: {e.stderr}")
        with open(f"{task_path}/{task_id}.blastn.out6") as blastn_out_file:
            first_line = blastn_out_file.readline().strip()
            if first_line:
                first_record = first_line.split('\t')
                seqid = first_record[1]
                start = int(first_record[8])
                end = int(first_record[9])
                blastnfmt6_100_dict = {'seqid': seqid, 'start': start, 'end': end}
                sequence = str(next(SeqIO.parse(f'{task_path}/{task_id}.fasta', 'fasta')).seq)
                return sequence, blastnfmt6_100_dict
            else:
                return 0, 1
    if input_type['position']:
        seqid, position_range = input_type['position'].split(':')
        start, end = position_range.split('-')
        start, end = int(start), int(end)
        sequence = pysam.FastaFile(f'/mhds/hd6platform/biohuaxing/zhangxueke/Project/CRISPRone/backend/CRISPRapi/coneapi/database/TargetGenome/{name_db}.fa').fetch(seqid, start, end)
        blastnfmt6_100_dict = {'seqid': seqid, 'start': start, 'end': end}
        return sequence, blastnfmt6_100_dict
    if input_type['locus']:
        genome_handle = pysam.FastaFile(f"/mhds/hd6platform/biohuaxing/zhangxueke/Project/CRISPRone/backend/CRISPRapi/coneapi/database/TargetGenome/{name_db}.fa")
        gff_pandas = pd.read_pickle(f"/mhds/hd6platform/biohuaxing/zhangxueke/Project/CRISPRone/backend/CRISPRapi/coneapi/database/TargetGenomeGff/{name_db}.gff.pkl")
        locus = gff_pandas.loc[gff_pandas['ID'] == input_type['locus'], ['seqid', 'start', 'end']].iloc[0].tolist()
        seqid, start, end = locus[0], int(locus[1]), int(locus[2])
        return genome_handle.fetch(seqid, start, end), {"seqid": seqid, "start": start, "end": end}

    return 0, 1


# @shared_task
# def cas9_task_process(task_id, inputSequence, pam, spacerLength, sgRNAModule, name_db):
#     if result_cas9_list.objects.filter(task_id=task_id).exists() and result_cas9_list.objects.get(
#             task_id=task_id).task_status == 'finished':
#         # form2Database(task_id, inputSequence, pam, spacerLength, sgRNAModule, name_db)
#         return 0
#     elif result_cas9_list.objects.filter(task_id=task_id).exists() and result_cas9_list.objects.get(
#             task_id=task_id).task_status == 'failed':
#         return result_cas9_list.objects.get(task_id=task_id).log
#     else:
#         form2Database(task_id, inputSequence, pam, spacerLength, sgRNAModule, name_db)
#         return 0


if __name__ == '__main__':
    inputSequence = 'Ghjin_A01:20000-23000'
    pam_type = 'NGG'
    target_genome = 'Gossypium_hirsutum_T2T-Jin668_HZAU_genome'

    task_id = 'test4'
    pam = 'NGG'
    spacerLength = 20
    sgRNAModule = 'spacerpam'
    name_db = 'Gossypium_hirsutum_T2T-Jin668_HZAU_genome'

    form2Database(task_id, inputSequence, pam, spacerLength, sgRNAModule, name_db)
