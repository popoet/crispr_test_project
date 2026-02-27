#!/bin/bash

# 读取range.txt中的范围
while IFS=$'\t' read -r sample start end; do
    # 根据sample名称定义输出文件
    output_file="${sample}_output.xlsx"
    
    # 清空或创建对应的输出文件
    > "$output_file"  # 清空文件内容或创建新文件
    
    # 对于每个样本，从Alleles_frequency_table.files.txt中获取对应的文件路径
    while IFS=$'\t' read -r sample_path path; do
        if [[ "$sample" == "$sample_path" ]]; then
            # 输出变量值以供调试
            echo "Processing $sample with range $start to $end using file: $path"
            
            # 调用Python脚本并将结果追加到对应的输出文件中
            python3 /home/export/online3/caohaitao/test/02assembly_contig/mutation_extraction/report.py "$path" "$start" "$end" "$output_file"
            break
        fi
    done < list.txt
done < range.txt
