import re
import sys

# Function to find all positions of guide_seq within amplicon_seq
def find_guide_seq_positions(amplicon_seq, guide_seq):
    positions = []
    for match in re.finditer(re.escape(guide_seq), amplicon_seq):
        start_pos = match.start()  # starting index of the match
        end_pos = match.end()  # ending index of the match (inclusive)
        positions.append((start_pos, end_pos))
    return positions

# Function to adjust positions with upstream and downstream ranges
def adjust_position_range(start, end, amplicon_len, upstream, downstream):
    # Adjust the start and end by the upstream and downstream values, ensuring they are within bounds
    adjusted_start = max(0, start - upstream)
    adjusted_end = min(amplicon_len - 1, end + downstream)
    return adjusted_start, adjusted_end

# Read the file and process each line
def process_file(filename, upstream, downstream):
    with open(filename, 'r') as file:
        # Skip the header line
        next(file)
        
        for line in file:
            # Split the line into columns
            columns = line.strip().split('\t')
            samid = columns[0]
            amplicon_seq = columns[1]
            guide_seq = columns[2]
            
            # Get the length of the amplicon sequence
            amplicon_len = len(amplicon_seq)
            
            # Find the positions of guide_seq within amplicon_seq
            positions = find_guide_seq_positions(amplicon_seq, guide_seq)
            
            # If there are any matches, print the adjusted start and end positions
            if positions:
                for start, end in positions:
                    adjusted_start, adjusted_end = adjust_position_range(start, end, amplicon_len, upstream, downstream)
                    print(f"{samid}\t{adjusted_start}\t{adjusted_end}")

# Main function to handle command-line arguments
def main():
    # Get the command-line arguments
    if len(sys.argv) != 4:
        print("Usage: python find_bin.py <filename> <upstream> <downstream>")
        sys.exit(1)
    
    filename = sys.argv[1]
    upstream = int(sys.argv[2])
    downstream = int(sys.argv[3])

    # Process the file with the given parameters
    process_file(filename, upstream, downstream)

if __name__ == "__main__":
    main()

