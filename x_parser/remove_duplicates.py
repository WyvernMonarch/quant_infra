def remove_duplicates_from_file(input_file, output_file):
    """
    Reads a text file, removes duplicate lines, and writes the unique lines to a new file.

    Args:
        input_file (str): The path to the input text file.
        output_file (str): The path to the output text file where the unique lines will be saved.
    """
    try:
        with open(input_file, 'r') as infile:
            lines = infile.readlines()

        # Remove duplicates while preserving order
        unique_lines = list(dict.fromkeys(lines))

        with open(output_file, 'w') as outfile:
            outfile.writelines(unique_lines)

        print(f"Successfully removed duplicates. Unique lines are saved in '{output_file}'")

    except FileNotFoundError:
        print(f"Error: The file '{input_file}' was not found.")
    except Exception as e:
        print(f"An error occurred: {e}")

# --- How to use the script ---

# 1. Specify the name of your input file (must be in the same directory as the script)
input_filename = 'accounts_PYTHIA_.txt'

# 2. Specify the name for the output file that will contain the unique lines
output_filename = 'accounts_PYTHIA.txt'

# 4. Run the script
remove_duplicates_from_file(input_filename, output_filename)