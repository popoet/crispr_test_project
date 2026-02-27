#!/usr/bin/env python3
import os
import sys
import re

def extract_flash_stats(log_file):
    stats = {
        'Total pairs': None,
        'Combined pairs': None,
        'Uncombined pairs': None,
        'Percent combined': None
    }
    
    try:
        with open(log_file, 'r') as f:
            for line in f:
                if '[FLASH] Read combination statistics:' in line:
                    # Read the next 4 lines which contain the stats
                    for _ in range(4):
                        line = next(f)
                        if 'Total pairs' in line:
                            stats['Total pairs'] = re.search(r'\d+', line).group()
                        elif 'Combined pairs' in line:
                            stats['Combined pairs'] = re.search(r'\d+', line).group()
                        elif 'Uncombined pairs' in line:
                            stats['Uncombined pairs'] = re.search(r'\d+', line).group()
                        elif 'Percent combined' in line:
                            stats['Percent combined'] = re.search(r'\d+\.\d+%', line).group()
                    break
    except Exception as e:
        print(f"Error processing {log_file}: {str(e)}")
    
    return stats

def main():
    if len(sys.argv) != 2:
        print("Usage: python flash_count.py flash_log.list")
        sys.exit(1)
    
    log_list_file = sys.argv[1]
    output_file = "flash_stats_summary.txt"
    
    try:
        with open(log_list_file, 'r') as f:
            log_files = [line.strip() for line in f if line.strip()]
    except FileNotFoundError:
        print(f"Error: File {log_list_file} not found.")
        sys.exit(1)
    
    # Prepare header and data rows
    header = ["Sample_name", "Total pairs", "Combined pairs", "Uncombined pairs", "Percent combined"]
    data_rows = []
    
    for log_file in log_files:
        if not os.path.exists(log_file):
            print(f"Warning: Log file {log_file} not found. Skipping.")
            continue
        
        # Extract sample name from path
        sample_name = os.path.basename(os.path.dirname(log_file))
        stats = extract_flash_stats(log_file)
        
        if all(value is not None for value in stats.values()):
            data_rows.append([
                sample_name,
                stats['Total pairs'],
                stats['Combined pairs'],
                stats['Uncombined pairs'],
                stats['Percent combined']
            ])
        else:
            print(f"Warning: Could not extract all statistics from {log_file}")
    
    # Write all data to output file
    with open(output_file, 'w') as f:
        # Write header
        f.write("\t".join(header) + "\n")
        
        # Write data rows
        for row in data_rows:
            f.write("\t".join(row) + "\n")
    
    print(f"All statistics have been saved to {output_file}")

if __name__ == "__main__":
    main()
