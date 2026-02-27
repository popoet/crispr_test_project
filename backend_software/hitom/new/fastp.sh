#!/bin/sh


samid=$1
fq1=$2
fq2=$3

if [ ! -d ${samid} ];then
	mkdir ${samid}
fi


cd ${samid}
fastp \
	-i ${fq1} -I ${fq2} -w 16 \
	-o ${samid}_1.clean.fq.gz \
	-O ${samid}_2.clean.fq.gz
