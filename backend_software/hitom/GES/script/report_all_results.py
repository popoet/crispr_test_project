import csv
import sys

def find_gap_regions(reference_sequence):

    gap_regions = []
    in_gap = False
    start = 0

    for i, char in enumerate(reference_sequence):
        if char == '-' and not in_gap:
            # 开始新的 gap 区间
            in_gap = True
            start = i
        elif char != '-' and in_gap:
            # 结束当前 gap 区间
            in_gap = False
            gap_regions.append((start, i - 1))

    # 如果最后是 gap，记录最后一个区间
    if in_gap:
        gap_regions.append((start, len(reference_sequence) - 1))

    return gap_regions


def adjust_range(reference_sequence, start, end):

    if start < 0 or start > end:
        raise ValueError("Invalid start or end positions.")

    # 获取所有连续的 gap 区间
    gap_regions = find_gap_regions(reference_sequence)
    #print(gap_regions)

    # 情况处理标志
    adjusted_start = start
    adjusted_end = end

    # 遍历每个 gap 区间并判断与 (start, end) 的关系
    for gap_start, gap_end in gap_regions:
        # 情况1：'-' 只出现在 start 之前
        #print(gap_start,gap_end)
        if gap_end <= start:
            adjusted_start += (gap_end - gap_start + 1)

            adjusted_end += (gap_end - gap_start + 1)
            #print(adjusted_start,adjusted_end)

        # 情况2：'-' 只出现在 (start, end) 范围内
        elif gap_start >= start and gap_end <= end:
            adjusted_end += (gap_end - gap_start + 1)

        # 情况3：连续的 '-' 越过 (start, end)
        elif gap_start <= start and gap_end >= end:
            total_gaps = sum(gap_end - gap_start + 1 for gap_start, gap_end in gap_regions)
            return start + total_gaps, end + total_gaps

        # 情况4：'-' 只出现在 end 之后
        elif gap_start > end:
            break

        # 情况5：部分 gap 在范围内，且 gap 延伸超过 end，需要逐步调整
        elif gap_start <= end < gap_end:
            non_gap_bases = 0
            for i in range(start, len(reference_sequence)):
                if reference_sequence[i] != '-':
                    non_gap_bases += 1
                if non_gap_bases >= (end - start + 1):
                    adjusted_end = i
                    break

    return adjusted_start, adjusted_end




def merge_variants(variant_types, variant_seqs, variant_positions):
    merged_types = []
    merged_seqs = []
    merged_positions = []

    i = 0
    while i < len(variant_types):
        current_type = variant_types[i]
        current_pos = variant_positions[i]
        current_seq = variant_seqs[i]

        if current_type == "SNP":
            # 合并连续 SNP
            start_pos = current_pos
            end_pos = current_pos
            snp_ref = current_seq[0]  # 参考碱基
            snp_alt = current_seq[-1]  # 变异碱基
            j = i + 1
            while j < len(variant_types) and variant_types[j] == "SNP" and variant_positions[j] == end_pos + 1:
                end_pos = variant_positions[j]
                snp_ref += variant_seqs[j][0]
                snp_alt += variant_seqs[j][-1]
                j += 1
            if start_pos == end_pos:
                merged_types.append("SNP")
                merged_seqs.append(current_seq)
                merged_positions.append(str(start_pos))
            else:
                merged_types.append("SNP")
                merged_seqs.append(f"{snp_ref}->{snp_alt}")
                merged_positions.append(f"{start_pos}-{end_pos}")
            i = j
        elif current_type.endswith("I") or current_type.endswith("D"):
            # 合并插入或缺失
            length = int(current_type[:-1])
            start_pos = current_pos
            end_pos = current_pos
            combined_seq = current_seq
            j = i + 1
            while j < len(variant_types) and variant_types[j] == current_type and variant_positions[j] == end_pos + 1:
                end_pos = variant_positions[j]
                combined_seq += variant_seqs[j]
                length += int(variant_types[j][:-1])
                j += 1
            merged_types.append(f"{length}{current_type[-1]}")
            merged_seqs.append(combined_seq)
            if start_pos == end_pos:
                merged_positions.append(str(start_pos))
            else:
                merged_positions.append(f"{start_pos}-{end_pos}")
            i = j
        else:
            # 其他类型直接记录
            merged_types.append(current_type)
            merged_seqs.append(current_seq)
            merged_positions.append(str(current_pos))
            i += 1

    # 如果变异类型包含多个 SNP，只保留一个
    if merged_types.count("SNP") > 1:
        merged_types = ["SNP"] + [t for t in merged_types if t != "SNP"]

    return merged_types, merged_seqs, merged_positions

def extract_variants_with_merge(input_file, output_file, start, end):
    # 计算总的 #Reads 数
    total_reads = 0
    rows = []
    with open(input_file, 'r') as infile:
        reader = csv.DictReader(infile, delimiter='\t')
        for row in reader:
            rows.append(row)
            total_reads += int(row['#Reads'])

    # 存储变异信息，以便后续合并
    variant_data = []

    for row in rows:
        aligned_seq = row['Aligned_Sequence']
        reference_seq = row['Reference_Sequence']
        num_reads = int(row['#Reads'])
        perc_reads = num_reads / total_reads * 100

        adjusted_start, adjusted_end = adjust_range(reference_seq, start, end)
        aligned_subseq = aligned_seq[adjusted_start:adjusted_end]
        reference_subseq = reference_seq[adjusted_start:adjusted_end]

        variant_types = []
        variant_seqs = []
        variant_positions = []
        amp_seq = aligned_subseq.upper()

        for i, (ref, align) in enumerate(zip(reference_subseq, aligned_subseq)):
            actual_position = adjusted_start + i
            if ref != align:
                if ref == "-":
                    variant_types.append("1I")
                    variant_seqs.append(align)
                    variant_positions.append(actual_position)
                    # print(variant_positions)
                    amp_seq = amp_seq[:i] + align.lower() + amp_seq[i+1:]
                    #print(amp_seq)
                elif align == "-":
                    variant_types.append("1D")
                    variant_seqs.append(ref)
                    variant_positions.append(actual_position)
                    amp_seq = amp_seq[:i] +'-' + amp_seq[i + 1:]
                else:
                    variant_types.append("SNP")
                    variant_seqs.append(f"{ref}->{align}")
                    variant_positions.append(actual_position)
                    amp_seq = amp_seq[:i] + align.lower() + amp_seq[i + 1:]

        merged_types, merged_seqs, merged_positions = merge_variants(
            variant_types, variant_seqs, variant_positions
        )

        if not merged_types:
            merged_types = ["WT"]
            merged_seqs = ["-"]
            merged_positions = ["未检测到变异"]

        perc_reads_display = f"{perc_reads:.2f}%" if perc_reads > 0 else "<0.2%"
        variant_type_str = ";".join(merged_types)
        variant_seq_str = ";".join(merged_seqs)
        variant_positions_str = ";".join(map(str, merged_positions))

        # Extract the reference sequence within the specified range (指定范围内的参考序列)
        ref_seq_in_range = reference_seq[adjusted_start:adjusted_end]
        aligned_seq_in_range = aligned_seq[adjusted_start:adjusted_end]

        variant_data.append({
            '变异编号': len(variant_data) + 1,
            '支持reads数目': num_reads,
            '变异比例': perc_reads_display,
            '变异类型': variant_type_str,
            '变异序列': variant_seq_str,
            '扩增片段序列': amp_seq,
            '变异位置': variant_positions_str,
            '指定范围内的参考序列': reference_seq,  # Add the reference sequence in the specified range
            #'比对序列': aligned_seq_in_range,
            '参考序列范围': f"{adjusted_start}-{adjusted_end}",  # Add reference sequence range:
            '比对序列范围': f"{adjusted_start}-{adjusted_end}"  # Add aligned sequence range
        })

    merged_variant_data = {}
    for variant in variant_data:
        amp_seq = variant['扩增片段序列']
        if amp_seq not in merged_variant_data:
            merged_variant_data[amp_seq] = {
                '支持reads数目': 0,
                '变异比例': 0.0,
                '变异类型': set(),
                '变异序列': set(),
                '变异位置': set(),
                '变异编号': variant['变异编号'],
                '指定范围内的参考序列': variant['指定范围内的参考序列'],  # Merge the reference sequence in the specified range
                #'比对序列':variant['比对序列'],
                '参考序列范围': variant['参考序列范围'],
                '比对序列范围': variant['比对序列范围']
            }

        merged_variant_data[amp_seq]['支持reads数目'] += int(variant['支持reads数目'])
        merged_variant_data[amp_seq]['变异比例'] += float(variant['变异比例'].replace('%', ''))
        merged_variant_data[amp_seq]['变异类型'].update(variant['变异类型'].split(';'))
        merged_variant_data[amp_seq]['变异序列'].update(variant['变异序列'].split(';'))
        merged_variant_data[amp_seq]['变异位置'].update(variant['变异位置'].split(';'))

    # 对结果按照“支持reads数目”从大到小排序
    sorted_variants = sorted(
        merged_variant_data.items(),
        key=lambda item: item[1]['支持reads数目'],
        reverse=True
    )

    with open(output_file, 'w', newline='') as outfile:
        fieldnames = [
            '变异编号', '支持reads数目', '变异比例', '变异类型', '变异序列', '扩增片段序列', '指定范围内的参考序列','比对序列',
            '参考序列范围', '比对序列范围'
        ]
        writer = csv.DictWriter(outfile, fieldnames=fieldnames, delimiter='\t')
        writer.writeheader()

        new_variant_index = 1
        for amp_seq, data in sorted_variants:
            writer.writerow({
                '变异编号': new_variant_index,
                '支持reads数目': data['支持reads数目'],
                '变异比例': f"{data['变异比例']:.2f}%" if data['变异比例'] > 0 else "<0.2%",
                '变异类型': ";".join(data['变异类型']),
                '变异序列': ";".join(data['变异序列']),
                '扩增片段序列': amp_seq,
                '指定范围内的参考序列': data['指定范围内的参考序列'],
                #'比对序列':data['比对序列'],
                '参考序列范围': data['参考序列范围'],
                '比对序列范围': data['比对序列范围']
            })
            new_variant_index += 1

    print(f"提取完成，变异结果已保存至 {output_file}")


if __name__ == "__main__":
    if len(sys.argv) != 5:
        print("用法: python report_all_results.py <input_file> <start> <end> <output_file>")
        sys.exit(1)

    input_file = sys.argv[1]
    start = int(sys.argv[2])
    end = int(sys.argv[3])
    output_file = sys.argv[4]

    extract_variants_with_merge(input_file, output_file, start, end)
