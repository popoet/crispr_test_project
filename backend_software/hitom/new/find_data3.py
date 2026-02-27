import os
import argparse

# 创建解析器
parser = argparse.ArgumentParser(description="Process fq1.txt and target_data.txt to generate list.txt")
parser.add_argument('fq1_file', help="Path to the fq1.txt file")
parser.add_argument('target_data_file', help="Path to the target_data.txt file")

# 解析命令行参数
args = parser.parse_args()

# 读取fq1.txt文件内容
fq1_paths = {}
with open(args.fq1_file, 'r') as file:
    for line in file:
        # 去除行尾的换行符
        line = line.strip()
        # 提取样本名
        sample_name = os.path.basename(os.path.dirname(line))
        # 构建新路径，替换原始路径的根目录部分
        fq1_paths[sample_name] = line

# 读取target_data.txt文件内容并构建输出
output_lines = []
with open(args.target_data_file, 'r') as file:
    next(file)  # 跳过表头
    for line in file:
        parts = line.strip().split('\t')
        samid, amplicon_seq, guide_seq = parts
        if samid in fq1_paths:
            output_line = f"{samid}\t{fq1_paths[samid]}\t{amplicon_seq}\t{guide_seq}\n"
            output_lines.append(output_line)

# 写入到list.txt文件
with open('list.txt', 'w') as outfile:
    outfile.writelines(output_lines)

