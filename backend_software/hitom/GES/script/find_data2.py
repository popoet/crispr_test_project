import os

# 读取fq1.txt文件内容
with open('fq1.txt', 'r') as file:
    lines = file.readlines()

# 打开list.txt文件准备写入
with open('list.txt', 'w') as outfile:
    for line in lines:
        # 去除行尾的换行符
        line = line.strip()
        # 提取样本名
        sample_name = os.path.basename(os.path.dirname(line))
        # 构建新路径，替换原始路径的根目录部分
        new_path_part1 = line
        # 构建第二个路径，假设第二个文件只是将_raw_1替换为_raw_2
        new_path_part2 = new_path_part1.replace('1.clean.fq.gz', '2.clean.fq.gz')
        # 写入到list.txt文件
        outfile.write(f"{sample_name}\t{new_path_part1}\t{new_path_part2}\n")
