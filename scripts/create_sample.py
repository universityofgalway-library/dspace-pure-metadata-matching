import json
import argparse
import pandas as pd
from pathlib import Path
from datetime import date

TODAY = date.today().isoformat()

# Define all possible dc.type values
doc_types = [
    "journal article",
    "review article",
    "review",
    # "doctoral thesis",
    # "master thesis",
    "data management plan",
    "conference paper",
    "conference output",
    "conference poster",
    "book part",
    "book",
    "report",
    "conference proceedings",
    "working paper",
    "video",
    "interactive resource",
    "newspaper article",
    "book review",
    "other"
]

def norm(s):
    return s.strip().lower() if isinstance(s, str) else ""

def parse_csv_authors(author_str):
    """
    Parses:
    'Surname, Name; Surname1, Name1'
    -> ['Surname, Name', 'Surname1, Name1']
    """
    if not author_str:
        return []
    return [a.strip() for a in str(author_str).split(';') if a.strip()]

def json_author_name_pairs(author):
    """
    Returns all possible (first, last) name combinations for one JSON author,
    matching the logic in match_records.py's find_person_match
    """
    firsts = [author.get("firstName", "")]
    lasts = [author.get("lastName", "")]

    firsts.extend(author.get("alternativeFirstName", []) or [])
    lasts.extend(author.get("alternativeLastName", []) or [])

    pairs = set()
    for f in firsts:
        for l in lasts:
            if f and l:
                # Add both orders: (first, last) and (last, first)
                pairs.add((norm(f), norm(l)))
                pairs.add((norm(l), norm(f)))
    
    return pairs

def publication_is_valid(row, author_lookup):
    csv_authors = parse_csv_authors(row['dc.contributor.author'])

    if not csv_authors:
        return False

    for author_name in csv_authors:
        # Parse the name in the same way as find_person_match does
        first, last = "", ""
        if "," in author_name:
            parts = [p.strip() for p in author_name.split(",", 1)]
            last = parts[0]
            first = parts[1] if len(parts) > 1 else ""
        else:
            parts = author_name.split()
            if len(parts) >= 2:
                first = " ".join(parts[:-1])
                last = parts[-1]
            else:
                first = author_name
                last = ""
        
        # Check if this name combination exists in the lookup AND is valid
        name_key = (norm(first), norm(last))
        if not author_lookup.get(name_key, False):
            return False

    return True

def has_non_empty_fields(row, required_fields):
    """
    Check if all required fields in the row are non-empty.
    A field is considered empty if it's None, NaN, empty string, or whitespace only.
    """
    if not required_fields:
        return True
    
    for field in required_fields:
        if field not in row.index:
            return False
        value = row[field]
        # Check if value is null, NaN, or empty/whitespace string
        if pd.isna(value) or (isinstance(value, str) and not value.strip()):
            return False
    return True

def add_date_suffix(filepath, date_str):
    """
    Add date suffix before file extension.
    Example: 'output.csv' -> 'output_2025-01-09.csv'
    """
    path = Path(filepath)
    stem = path.stem
    suffix = path.suffix
    parent = path.parent
    
    new_name = f"{stem}_{date_str}{suffix}"
    return parent / new_name

def main():
    # Set up argument parser
    parser = argparse.ArgumentParser(
        description='Sample CSV records by document type with optional author validation'
    )
    parser.add_argument(
        '--input-file',
        default='./dspace_data/all_data_test/enriched_dspace_test_metadata_2026-02-13.csv',
        help='Path to input CSV file (default: ./dspace_data/all_data_test/enriched_dspace_test_metadata_2026-02-13.csv)'
    )
    parser.add_argument(
        '--output-file',
        default=f'./dspace_data/test_samples/dspace_test_sample_{TODAY}.csv',
        help='Path to output CSV file (default: ./dspace_data/test_samples/dspace_test_sample.csv)'
    )
    parser.add_argument(
        '--authors-json',
        default='./author_matching/2026-02-26/updated_merged_all_authors_2026-02-26.json',
        help='Path to authors JSON file (default: ./author_matching/2026-02-26/updated_merged_all_authors_2026-02-26.json)'
    )
    parser.add_argument(
        '--random-state',
        default=42,
        type=int,
        help='Random state for sampling the dataset (default: 42)'
    )
    parser.add_argument(
        '--filter-authors',
        action='store_true',
        default=True,
        help='Filter to only include records with existing authors (default: True)'
    )
    parser.add_argument(
        '--no-filter-authors',
        action='store_false',
        dest='filter_authors',
        help='Do not filter by existing authors, create sample regardless of authors'
    )
    parser.add_argument(
        '--required-fields',
        nargs='*',
        default=[],
        help='List of field names that must be non-empty (e.g., --required-fields dc.title dc.date.issued)'
    )
    parser.add_argument(
        '--sample-size',
        type=int,
        default=5,
        help='Number of records to sample per document type (default: 5)'
    )
    
    args = parser.parse_args()
    
    # Add date suffix to output file
    output_file = add_date_suffix(args.output_file, TODAY)
    
    print(f"Input file: {args.input_file}")
    print(f"Output file: {output_file}")
    print(f"Authors JSON: {args.authors_json}")
    print(f"Filter existing authors: {args.filter_authors}")
    print(f"Required non-empty fields: {args.required_fields if args.required_fields else 'None'}")
    print(f"Sample size per type: {args.sample_size}\n")
    
    # Load authors from JSON file only if filtering is enabled
    author_lookup = {}
    if args.filter_authors:
        with open(args.authors_json, encoding="utf-8") as f:
            json_authors = json.load(f)

        # Build lookup: (first, last) -> is_valid_author (has internal OR external UUIDs)
        for author in json_authors:
            # Author is valid only if they have at least one internal UUID OR at least one external UUID
            has_internal = bool(author.get("internal"))
            has_external = bool(author.get("external"))
            is_valid = has_internal or has_external
            
            for name_pair in json_author_name_pairs(author):
                # Only add to lookup if this is a valid author
                if is_valid:
                    author_lookup[name_pair] = True
                # If already marked as valid, keep it valid (don't overwrite True with False)
                elif name_pair not in author_lookup:
                    author_lookup[name_pair] = False

    # Read the CSV file
    df = pd.read_csv(args.input_file, encoding="utf-8", sep=',')

    # Check if dc.type column exists
    if 'dc.type' not in df.columns:
        raise ValueError("Column 'dc.type' not found in the CSV file")

    if 'dc.contributor.author' not in df.columns:
        raise ValueError("Column 'dc.contributor.author' not found in the CSV file")

    # Validate that required fields exist in the dataframe
    if args.required_fields:
        missing_fields = [f for f in args.required_fields if f not in df.columns]
        if missing_fields:
            raise ValueError(f"Required fields not found in CSV: {missing_fields}")

    # Filter publications based on author validity (only if filtering is enabled)
    if args.filter_authors:
        df_filtered = df[df.apply(lambda row: publication_is_valid(row, author_lookup), axis=1)]
        print(f"Filtered dataset: {len(df_filtered)} records with existing authors")
    else:
        df_filtered = df
        print(f"Using full dataset: {len(df_filtered)} records (no author filtering)")

    # Create an empty list to store sampled dataframes
    sampled_dfs = []

    # Sample up to the specified number of records for each document type
    for doc_type in doc_types:
        # Filter records of this type
        type_df = df_filtered[df_filtered['dc.type'] == doc_type]
        
        if len(type_df) == 0:
            continue
        
        # If required fields are specified, prioritize records with those fields
        if args.required_fields:
            # Get records with all required fields non-empty
            priority_df = type_df[type_df.apply(lambda row: has_non_empty_fields(row, args.required_fields), axis=1)]
            priority_count = len(priority_df)
            
            if priority_count >= args.sample_size:
                # Enough priority records, sample from them
                sampled = priority_df.sample(n=args.sample_size, random_state=args.random_state)
                print(f"{doc_type}: sampled {args.sample_size} records (all with required fields)")
            else:
                # Not enough priority records, take all priority records and fill with random
                remaining_needed = args.sample_size - priority_count
                
                # Get records that don't have all required fields
                non_priority_df = type_df[~type_df.apply(lambda row: has_non_empty_fields(row, args.required_fields), axis=1)]
                
                # Sample from non-priority records
                additional_count = min(remaining_needed, len(non_priority_df))
                
                if additional_count > 0:
                    additional_sampled = non_priority_df.sample(n=additional_count, random_state=args.random_state)
                    sampled = pd.concat([priority_df, additional_sampled], ignore_index=True)
                    print(f"{doc_type}: sampled {len(sampled)} records ({priority_count} with required fields, {additional_count} without)")
                else:
                    sampled = priority_df
                    print(f"{doc_type}: sampled {len(sampled)} records ({priority_count} with required fields, none available without)")
        else:
            # No required fields, sample normally
            sample_size = min(args.sample_size, len(type_df))
            sampled = type_df.sample(n=sample_size, random_state=args.random_state)
            print(f"{doc_type}: sampled {sample_size} records")
        
        sampled_dfs.append(sampled)

    # Combine all sampled dataframes
    if sampled_dfs:
        result_df = pd.concat(sampled_dfs, ignore_index=True)
        
        # Write to new CSV file
        result_df.to_csv(output_file, index=False)
        print(f"\nTotal records in sample: {len(result_df)}")
        print(f"Output written to: {output_file}")
    else:
        print("No matching records found for any document type")

if __name__ == "__main__":
    main()