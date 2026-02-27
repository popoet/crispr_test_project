#!/bin/sh

samid=$1
fq1=$2
fq2=$3

if [ ! -d ${samid} ];then
 mkdir ${samid}
fi
cd ${samid}
/home/export/online3/caohaitao/conda3/envs/flash/bin/flash \
	${fq1} ${fq2} \
	-t 10 \
	--max-overlap 100 \
	--compress-prog=gzip --suffix=gz \
	--output-directory ${samid} >flash.log 2>&1
