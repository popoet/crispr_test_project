import sys
import os

def process_file(input_file_path, threshold):
    # 确定输出文件路径
    dir_path = os.path.dirname(input_file_path)
    output_file_path = os.path.join(dir_path, "Alleles_frequency_table_filter.txt")
    
    # 读取输入文件
    with open(input_file_path, 'r') as f:
        lines = f.readlines()
    
    # 处理标题行
    header = lines[0]
    data_lines = lines[1:]
    
    # 筛选倒数第二列大于等于阈值的行
    filtered_lines = []
    total_reads = 0
    for line in data_lines:
        parts = line.strip().split('\t')
        if len(parts) < 9:  # 确保有足够的列
            continue
        try:
            reads = int(parts[-2])  # 倒数第二列是#Reads
        except ValueError:
            continue
        
        if reads >= threshold:
            filtered_lines.append(parts)
            total_reads += reads
    
    # 如果没有符合条件的行，直接写入空文件
    if not filtered_lines:
        with open(output_file_path, 'w') as f:
            f.write(header)
        return
    
    # 重新计算最后一列(%Reads)的值
    processed_lines = []
    for parts in filtered_lines:
        reads = int(parts[-2])
        percent_reads = (reads / total_reads) * 100
        parts[-1] = f"{percent_reads:.6f}"  # 更新最后一列
        processed_lines.append('\t'.join(parts) + '\n')
    
    # 写入输出文件
    with open(output_file_path, 'w') as f:
        f.write(header)
        f.writelines(processed_lines)

def main():
    # 设置默认阈值为5
    threshold = 5
    
    # 检查参数
    if len(sys.argv) < 2 or len(sys.argv) > 3:
        print("Usage: python 01.py fq1.txt [threshold]")
        print("Default threshold is 5")
        sys.exit(1)
    
    fq1_file = sys.argv[1]
    
    # 如果有第二个参数，使用它作为阈值
    if len(sys.argv) == 3:
        try:
            threshold = int(sys.argv[2])
        except ValueError:
            print("Error: Threshold must be an integer")
            sys.exit(1)
    
    # 读取fq1.txt中的文件列表
    with open(fq1_file, 'r') as f:
        file_paths = [line.strip() for line in f if line.strip()]
    
    # 处理每个文件
    for file_path in file_paths:
        if os.path.exists(file_path):
            process_file(file_path, threshold)
            print(f"Processed: {file_path} (Threshold: {threshold})")
        else:
            print(f"File not found: {file_path}")

if __name__ == "__main__":
    main()
