#!/bin/bash


# 定义基础路径
BASE_DIR='/home/export/online3/caohaitao/test/02assembly_contig/mutation_extraction'

#定义下机数据路径
fq_path="${BASE_DIR}/raw_data"

upload_data="${BASE_DIR}/target_data.txt"

thread=4
memory='50G'

start=0 
end=0
#过滤Alleles_frequency_table.txt中深度低于该该值
Threshold=0

# 定义各个工作目录相对于基础路径的位置
WORKDIR_01="${BASE_DIR}/01qc"
WORKDIR_02="${BASE_DIR}/02flash"
WORKDIR_03="${BASE_DIR}/03CRISPResso"
WORKDIR_04="${BASE_DIR}/04result"


# 将所有要创建的工作目录放入数组
workdirs=(
  "${WORKDIR_01}"
  "${WORKDIR_02}"
  "${WORKDIR_03}"
  "${WORKDIR_04}"
)

# 创建工作目录并检查是否成功
for dir in "${workdirs[@]}"; do
  if ! mkdir -p "$dir"; then
    echo "Error: Failed to create directory $dir" >&2
    exit 1
  fi
done

# 输出成功信息
echo "All directories have been created successfully."



# Step1
cat <<EOT > ${WORKDIR_01}/01.sh
#!/bin/sh
cd ${WORKDIR_01}
find  ${fq_path}  -name '*1.fq.gz' > fq1.txt &&  \
python /home/export/online3/caohaitao/test/02assembly_contig/mutation_extraction/find_data.py &&  \
cat  list.txt|awk '{print "sh /home/export/online3/caohaitao/test/02assembly_contig/mutation_extraction/fastp.sh  "\$1" "\$2" "\$3""}' > run_fastp.sh && \
/GFPS8p/biohuaxing/Software/perl-5.18.4/perl /GFPS8p/biohuaxing/Software/slurm_duty/slurm_Duty.pl --interval 30 --maxjob 500 --convert no  --lines 1 --partition q_cn,q_fat --reslurm --mem ${memory} --cpu ${thread} run_fastp.sh
EOT


#step2
cat <<EOT > ${WORKDIR_02}/01.sh
#!/bin/bash
cd ${WORKDIR_02}
find ${WORKDIR_01} -name '*1.clean.fq.gz' > fq1.txt && \
python /home/export/online3/caohaitao/test/02assembly_contig/mutation_extraction/find_data2.py &&  \
cat  list.txt|awk '{print "sh /home/export/online3/caohaitao/test/02assembly_contig/mutation_extraction/step01flash.sh  "\$1" "\$2" "\$3""}' > run_flash.sh && \
/GFPS8p/biohuaxing/Software/perl-5.18.4/perl /GFPS8p/biohuaxing/Software/slurm_duty/slurm_Duty.pl --interval 30 --maxjob 500 --convert no  --lines 1 --partition q_cn,q_fat --reslurm --mem ${memory} --cpu ${thread} run_flash.sh && \
find ${WORKDIR_02} -name 'flash.log' > flash_log.list && \
python /home/export/online3/caohaitao/test/02assembly_contig/mutation_extraction/flash_count.py flash_log.list
EOT

#step3
cat <<EOT > ${WORKDIR_03}/01.sh
#!/bin/bash
cd ${WORKDIR_03}
find ${WORKDIR_02} -name '*out.extendedFrags.fastq.gz' > fq1.txt && \
python /home/export/online3/caohaitao/test/02assembly_contig/mutation_extraction/find_data3.py fq1.txt ${upload_data} && \
cat  list.txt|awk '{print "sh /home/export/online3/caohaitao/test/02assembly_contig/mutation_extraction/CRISPResso2_CRISPR.sh "\$1" "\$2" "\$3" "\$4" "}' > run_CRISPResso2_CRISPR.sh && \
/GFPS8p/biohuaxing/Software/perl-5.18.4/perl /GFPS8p/biohuaxing/Software/slurm_duty/slurm_Duty.pl --interval 30 --maxjob 500 --convert no  --lines 1 --partition q_cn,q_fat --reslurm --mem ${memory} --cpu ${thread} run_CRISPResso2_CRISPR.sh
EOT

#step4
cat <<EOT > ${WORKDIR_04}/01.sh
#!/bin/bash
cd ${WORKDIR_04}
find ${WORKDIR_03} -name '*Alleles_frequency_table.zip' -exec sh -c 'unzip -d "\$(dirname {})" "{}"' \; && \
find  ${WORKDIR_03} -name '*Alleles_frequency_table.txt' > fq0.txt && \
python /home/export/online3/caohaitao/test/02assembly_contig/mutation_extraction/filter_Alleles_frequency_table_txt.py fq0.txt ${Threshold} && \
find  ${WORKDIR_03} -name '*Alleles_frequency_table_filter.txt' > fq1.txt && \
python /home/export/online3/caohaitao/test/02assembly_contig/mutation_extraction/find_data4.py && \
python /home/export/online3/caohaitao/test/02assembly_contig/mutation_extraction/find_bin.py ${upload_data} ${start} ${end} > range.txt && \
bash /home/export/online3/caohaitao/test/02assembly_contig/mutation_extraction/result.sh && \
find ${WORKDIR_04} -name '*_output.xlsx' > input.list && \
python /home/export/online3/caohaitao/test/02assembly_contig/mutation_extraction/output_count.py input.list /output_count.tsv && \
rm -rf *.txt 
EOT



cat <<EOT > run_02.sh
#!/bin/bash

date +"%D %T -> Start Mapping for 01.qc.sh" && \
sh ${WORKDIR_01}/01.sh && \
date +"%D %T -> Finish Mapping for 01.qc.sh" && \

date +"%D %T -> Start Mapping for 02.flash.sh" && \
sh ${WORKDIR_02}/01.sh && \
date +"%D %T -> Finish Mapping for 02.flash.sh" && \

date +"%D %T -> Start Mapping for 03CRISPResso.sh" && \
sh ${WORKDIR_03}/01.sh && \
date +"%D %T -> Finish Mapping for 03CRISPResso.sh" && \

date +"%D %T -> Start Mapping for 04result.sh" && \
sh ${WORKDIR_04}/01.sh && \
date +"%D %T -> Finish Mapping for 04result.sh"

EOT

chmod +x pre_01.sh
chmod +x run_02.sh

