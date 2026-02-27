#!/bin/bash

#下机数据路径
DATA_DIR=$1
upload_data=$2

start=$3
end=$4
WORK_DIR=$5

thread=5
memory='20G'


# 定义各个工作目录相对于基础路径的位置
WORKDIR_01="$WORK_DIR/01qc"
WORKDIR_02="$WORK_DIR/02flash"
WORKDIR_03="$WORK_DIR/03CRISPResso"
WORKDIR_04="$WORK_DIR/04result"


# 将所有要创建的工作目录放入数组
workdirs=(
 "${WORKDIR_01}"
 "${WORKDIR_02}"
 "${WORKDIR_03}"
 "${WORKDIR_04}"
)

if [[ "$DATA_DIR" == *.zip ]]; then
   if [ ! -d ${DATA_DIR%.zip} ]; then
       # 获取文件的目录路径
       dir_path=$(dirname "$DATA_DIR")
       # 解压到原目录
       unzip "$DATA_DIR" -d "${DATA_DIR%.zip}"
       mv ${DATA_DIR%.zip}/*/* ${DATA_DIR%.zip}/
   fi
   DATA_DIR="${DATA_DIR%.zip}"
fi

# 创建工作目录并检查是否成功
for dir in "${workdirs[@]}"; do
 if ! mkdir -p "$dir"; then
   echo "Error: Failed to create directory $dir" >&2
   exit 1
 fi
done

# 创建日志文件
mkdir -p "${WORK_DIR}/logs"


# Step1
cd "${WORKDIR_01}" && \
{
    find "${DATA_DIR}" -name '*1.fq.gz' > fq1.txt && \
    python /home/Project/MDBProject/GES/script/find_data.py && \
    awk '{print "sh /mhds/hd6platform/biohuaxing/zhangxueke/Project/CRISPRone/backend/CRISPRapi/coneapi/software/hitom/GES/script/fastp.sh  " $1 " " $2 " " $3}' list.txt > run_fastp.sh && \
    sh run_fastp.sh && \
    touch run_fastp.done
} > "${WORK_DIR}/logs/step1.log" 2>&1


# Step2
cd "${WORKDIR_02}" && \
{
    awk '{print "sh /mhds/hd6platform/biohuaxing/zhangxueke/Project/CRISPRone/backend/CRISPRapi/coneapi/software/hitom/GES/script/step01flash.sh  "$1" "$2" "$3""}' "${WORKDIR_01}/list.txt" > run_flash.sh && \
    sh run_flash.sh && \
    touch run_flash.done
} > "${WORK_DIR}/logs/step2.log" 2>&1


# Step3
cd "${WORKDIR_03}" && \
{
    find "${WORKDIR_02}" -name '*out.extendedFrags.fastq.gz' > fq1.txt && \
    python /home/Project/MDBProject/GES/script/find_data3.py fq1.txt "${upload_data}" && \
    awk '{print "sh /mhds/hd6platform/biohuaxing/zhangxueke/Project/CRISPRone/backend/CRISPRapi/coneapi/software/hitom/GES/script/CRISPResso2_CRISPR.sh "$1" "$2" "$3" "$4" "}' list.txt > run_CRISPResso2_CRISPR.sh && \
    sh run_CRISPResso2_CRISPR.sh && \
    touch run_flash.done
} > "${WORK_DIR}/logs/step3.log" 2>&1


# Step4
# cd "${WORKDIR_04}" && \
# {
    # find "${WORKDIR_03}" -name '*Alleles_frequency_table.zip' -exec sh -c 'for f; do unzip -o -d "$(dirname "$f")" "$f"; done' sh {} + && \
    # find "${WORKDIR_03}" -name '*Alleles_frequency_table.txt' > fq1.txt && \
    # python /mhds/hd6platform/biohuaxing/zhangxueke/Project/CRISPRone/backend/CRISPRapi/coneapi/software/hitom/GES/script/find_data4.py && \
    # python /mhds/hd6platform/biohuaxing/zhangxueke/Project/CRISPRone/backend/CRISPRapi/coneapi/software/hitom/GES/script/find_bin.py "${upload_data}" "${start}" "${end}" > range.txt && \
    # bash /mhds/hd6platform/biohuaxing/zhangxueke/Project/CRISPRone/backend/CRISPRapi/coneapi/software/hitom/GES/script/result.sh && \
    # rm -rf *.txt && \
    # touch result.done
# } > "${WORK_DIR}/logs/step4.log" 2>&1
cd "${WORKDIR_04}" && \
{
    find "${WORKDIR_03}" -name '*Alleles_frequency_table.zip' -exec sh -c 'for f; do unzip -o -d "$(dirname "$f")" "$f"; done' sh {} + && \
    find "${WORKDIR_03}" -name '*Alleles_frequency_table.txt' > fq1.txt && \
    python /mhds/hd6platform/biohuaxing/zhangxueke/Project/CRISPRone/backend/CRISPRapi/coneapi/software/hitom/GES/script/find_data4.py && \
    python /mhds/hd6platform/biohuaxing/zhangxueke/Project/CRISPRone/backend/CRISPRapi/coneapi/software/hitom/GES/script/find_bin.py "${upload_data}" "${start}" "${end}" > range.txt && \
    bash /mhds/hd6platform/biohuaxing/zhangxueke/Project/CRISPRone/backend/CRISPRapi/coneapi/software/hitom/GES/script/result.sh && \
    rm -rf *.txt && \
    zip result.zip *_output.xls && \
    touch result.done
} > "${WORK_DIR}/logs/step4.log" 2>&1