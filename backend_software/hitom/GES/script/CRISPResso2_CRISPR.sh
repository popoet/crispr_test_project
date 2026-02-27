#!/bin/sh



samid=$1
fq=$2
amplicon_seq=$3
guide_seq=$4

if [ ! -d ${samid} ];then
	mkdir ${samid}
fi

/home/zhangxueke/.conda/envs/crispresso2/bin/CRISPResso \
	-r1 ${fq} --name ${samid} \
	--output_folder ${samid} \
	-a ${amplicon_seq} -g ${guide_seq}
