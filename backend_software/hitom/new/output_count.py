#!/usr/bin/env python3
import pandas as pd
import sys
import os

def calculate_indel_ratio(input_file):
    """计算单个文件的插入/删除比例"""
    try:
        # 读取Excel文件
        df = pd.read_excel(input_file)
        
        # 检查数据列
        if df.shape[1] < 4:
            print(f"错误：文件 {input_file} 列数不足！需要至少4列数据。")
            return None

        # 提取关键列
        reads = df.iloc[:, 1]    # 第2列是支持reads数目
        variants = df.iloc[:, 3] # 第4列是变异类型

        # 计算总reads数（分母）
        total_reads = reads.sum()
        
        # 计算有插入或删除的reads数（分子）
        indel_mask = variants.str.contains(r'[ID]', na=False)
        indel_reads = reads[indel_mask].sum()
        
        # 计算比例
        ratio = indel_reads / total_reads if total_reads > 0 else 0
        
        # 从完整路径中提取样本ID部分（去掉"_output"）
        filename = os.path.basename(input_file)  # 获取文件名部分
        sample_id = filename.split('_')[0]  # 分割并取第一部分
        
        return {
            'sample': sample_id,
            'indel_reads': indel_reads,
            'total_reads': total_reads,
            'ratio': ratio
        }

    except Exception as e:
        print(f"处理文件 {input_file} 时出错: {str(e)}")
        return None

def process_files(input_list, output_file):
    """处理多个文件并将结果保存到输出文件"""
    try:
        # 读取输入文件列表
        with open(input_list, 'r') as f:
            files = [line.strip() for line in f if line.strip()]
        
        results = []
        for file in files:
            print(f"正在分析文件: {file}")
            result = calculate_indel_ratio(file)
            if result:
                results.append(result)
        
        # 将结果写入输出文件
        with open(output_file, 'w') as f:
            # 写入表头
            f.write("sample\t插入/删除的reads数\t总reads数\t插入/删除比例\n")
            
            # 写入每个样本的结果
            for res in results:
                line = f"{res['sample']}\t{res['indel_reads']:,}\t{res['total_reads']:,}\t{res['ratio']:.2%}\n"
                f.write(line)
        
        print(f"分析完成，结果已保存到: {output_file}")
        return True
    
    except Exception as e:
        print(f"处理过程中出错: {str(e)}")
        return False

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("使用方法: python script.py input.list output_count.txt")
        sys.exit(1)
    
    input_list = sys.argv[1]
    output_file = sys.argv[2]
    process_files(input_list, output_file)
