#!/bin/bash

date +"%D %T -> Start Mapping for 01.qc.sh" && sh /home/export/online3/caohaitao/test/02assembly_contig/mutation_extraction/01qc/01.sh && date +"%D %T -> Finish Mapping for 01.qc.sh" && 
date +"%D %T -> Start Mapping for 02.flash.sh" && sh /home/export/online3/caohaitao/test/02assembly_contig/mutation_extraction/02flash/01.sh && date +"%D %T -> Finish Mapping for 02.flash.sh" && 
date +"%D %T -> Start Mapping for 03CRISPResso.sh" && sh /home/export/online3/caohaitao/test/02assembly_contig/mutation_extraction/03CRISPResso/01.sh && date +"%D %T -> Finish Mapping for 03CRISPResso.sh" && 
date +"%D %T -> Start Mapping for 04result.sh" && sh /home/export/online3/caohaitao/test/02assembly_contig/mutation_extraction/04result/01.sh && date +"%D %T -> Finish Mapping for 04result.sh"

