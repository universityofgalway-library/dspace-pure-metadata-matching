#!/usr/bin/env python3
"""
CSV Enrichment Script
Enrich a target CSV with journal/publisher data from either:
1. Another CSV file (matching by 'handle' column)
2. JSON file or API (matching by journal title)
"""

import csv
import json
import argparse
import sys
import os
import requests
import pandas as pd
from pathlib import Path
from dotenv import load_dotenv

# Mapping configuration for JSON/API mode
JSON_MAPPINGS = {
    "journal_uuid": "uuid",
    "publisher_uuid": "publisher.uuid",
    "journal_title": "titles.0.title"
}


def enrich_from_csv(source_path, target_path):
    """
    Enrich target CSV with data from source CSV based on handle matching.
    
    Args:
        source_path: Path to source CSV file
        target_path: Path to target CSV file
    
    Returns:
        Path to output file
    """
    print(f"Loading source file: {source_path}")
    source_df = pd.read_csv(source_path)
    
    print(f"Loading target file: {target_path}")
    target_df = pd.read_csv(target_path)
    
    # Columns to copy from source to target
    columns_to_copy = [
        'journal_title',
        'journal_issn(s)',
        'journal_uuid',
        'publisher_name',
        'publisher_uuid'
    ]
    
    # Verify that source has the required columns
    missing_cols = [col for col in columns_to_copy if col not in source_df.columns]
    if missing_cols:
        print(f"Warning: Source file missing columns: {missing_cols}")
        columns_to_copy = [col for col in columns_to_copy if col in source_df.columns]
    
    # Add missing columns to target if they don't exist
    for col in columns_to_copy:
        if col not in target_df.columns:
            target_df[col] = None
    
    # Create a lookup dictionary from source
    # Key: handle, Value: dict of column values
    source_lookup = {}
    for _, row in source_df.iterrows():
        handle = row['handle']
        if pd.notna(handle):  # Only process non-null handles
            source_lookup[handle] = {col: row.get(col) for col in columns_to_copy}
    
    # Track statistics
    total_rows = len(target_df)
    matched_rows = 0
    updated_rows = 0
    
    # Enrich target dataframe
    for idx, row in target_df.iterrows():
        handle = row['handle']
        
        if pd.notna(handle) and handle in source_lookup:
            matched_rows += 1
            source_data = source_lookup[handle]
            
            # Check if any data is actually being updated
            has_update = False
            for col in columns_to_copy:
                if pd.notna(source_data.get(col)):
                    target_df.at[idx, col] = source_data[col]
                    has_update = True
            
            if has_update:
                updated_rows += 1
    
    # Generate output filename
    target_path_obj = Path(target_path)
    output_filename = f"enriched_{target_path_obj.name}"
    output_path = target_path_obj.parent / output_filename
    
    # Save enriched file
    print(f"\nSaving enriched file: {output_path}")
    target_df.to_csv(output_path, index=False)
    
    # Print statistics
    print("\n" + "="*60)
    print("ENRICHMENT STATISTICS (CSV MODE)")
    print("="*60)
    print(f"Total rows in target file: {total_rows}")
    print(f"Rows with matching handles: {matched_rows}")
    print(f"Rows updated with new data: {updated_rows}")
    print(f"Rows not matched: {total_rows - matched_rows}")
    print(f"\nMatch rate: {matched_rows/total_rows*100:.2f}%")
    print(f"Update rate: {updated_rows/total_rows*100:.2f}%")
    print("="*60)
    
    return output_path


def get_api_data(api_key, base_url):
    """
    Fetch journal data from the API.
    
    Args:
        api_key: API key for authentication
        base_url: Base URL for the API
    
    Returns:
        List of journal items from the API response
    """
    url = f"{base_url}journals"
    headers = {
        'api-key': api_key,
        'Accept': 'application/json'
    }
    
    try:
        print(f"Fetching data from {url}...")
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        data = response.json()
        
        # Handle paginated responses or direct lists
        if isinstance(data, dict) and 'items' in data:
            return data['items']
        elif isinstance(data, list):
            return data
        else:
            print(f"Warning: Unexpected API response structure")
            return []
            
    except requests.exceptions.RequestException as e:
        print(f"Error fetching data from API: {e}")
        sys.exit(1)


def create_journal_lookup(data):
    """
    Create lookup dictionaries for journals.
    Matches by lowercase title and publisher uuid (if available).
    
    Returns:
        dict: Lookup dictionary
    """
    lookup = {}
    
    for item in data:
        if 'titles' in item and len(item['titles']) > 0:
            title = item['titles'][0]['title'].lower().strip()
            publisher_uuid = None
            
            if 'publisher' in item and 'uuid' in item['publisher']:
                publisher_uuid = item['publisher']['uuid']
            
            # Create composite key with title and publisher uuid
            if publisher_uuid:
                key = (title, publisher_uuid)
                lookup[key] = item
            
            # Also add just title as fallback
            if title not in lookup:
                lookup[title] = item
    
    return lookup


def extract_value(item, key_path):
    """
    Extract value from nested dictionary using dot notation.
    Supports array indexing (e.g., 'titles.0.title')
    
    Args:
        item: Dictionary to extract from
        key_path: Path to value (e.g., 'uuid' or 'titles.0.title')
    
    Returns:
        Extracted value or None
    """
    keys = key_path.split('.')
    value = item
    
    for key in keys:
        # Check if this is an array index
        if key.isdigit():
            index = int(key)
            if isinstance(value, list) and 0 <= index < len(value):
                value = value[index]
            else:
                return None
        elif isinstance(value, dict) and key in value:
            value = value[key]
        else:
            return None
    
    return value


def enrich_from_json(csv_file, data_source, test=False):
    """
    Enrich CSV columns with journal data from JSON file or API.
    
    Args:
        csv_file: Path to input CSV file
        data_source: Path to JSON file or 'api' to fetch from API
        test: Whether to use test/staging environment
    
    Returns:
        Path to output file
    """
    
    # Generate output filename
    directory = os.path.dirname(csv_file) or '.'
    filename = os.path.basename(csv_file)
    output_file = os.path.join(directory, f"enriched_{filename}")
    
    # Load data from JSON or API
    if data_source == 'api':
        load_dotenv()
        api_key = os.getenv('API_KEY')
        
        if not api_key:
            print("Error: API_KEY not found in .env file")
            sys.exit(1)
        
        base_url = 'https://galway-staging.elsevierpure.com/ws/api/' if test else 'https://research.universityofgalway.ie/ws/api/'
        json_data = get_api_data(api_key, base_url)
    else:
        # Load from JSON file
        try:
            with open(data_source, 'r', encoding='utf-8') as f:
                json_data = json.load(f)
        except FileNotFoundError:
            print(f"Error: JSON file '{data_source}' not found.")
            sys.exit(1)
        except json.JSONDecodeError as e:
            print(f"Error: Invalid JSON format in '{data_source}': {e}")
            sys.exit(1)
    
    # Create lookup
    lookup = create_journal_lookup(json_data)
    print(f"Loaded {len(json_data)} journals from {data_source}")
    
    # Process CSV
    try:
        with open(csv_file, 'r', encoding='utf-8') as infile:
            reader = csv.DictReader(infile)
            fieldnames = list(reader.fieldnames)
            
            # Add new columns if they don't exist
            for csv_col in JSON_MAPPINGS.keys():
                if csv_col not in fieldnames:
                    fieldnames.append(csv_col)
            
            rows = []
            matches = 0
            no_matches = 0
            updated = 0
            
            for row in reader:
                matched = False
                
                # Find title column (case-insensitive)
                title_col = None
                publisher_uuid_col = None
                
                for col in row.keys():
                    col_lower = col.lower()
                    if 'journal' in col_lower and 'title' in col_lower:
                        title_col = col
                    elif 'title' in col_lower and not title_col:
                        title_col = col
                    
                    if 'publisher' in col_lower and 'uuid' in col_lower:
                        publisher_uuid_col = col
                
                # Try to match journal
                if title_col and row.get(title_col):
                    title = row[title_col].lower().strip()
                    publisher_uuid = row.get(publisher_uuid_col, '').strip() if publisher_uuid_col else None
                    
                    # Try matching with both title and publisher uuid first
                    if publisher_uuid:
                        match_key = (title, publisher_uuid)
                        if match_key in lookup:
                            item = lookup[match_key]
                            matched = True
                    
                    # Fallback to title-only match
                    if not matched and title in lookup:
                        item = lookup[title]
                        matched = True
                
                # Populate columns if matched
                if matched:
                    has_update = False
                    for csv_col, json_key in JSON_MAPPINGS.items():
                        value = extract_value(item, json_key)
                        # Only update if value exists in JSON/API response
                        if value is not None and value != "":
                            row[csv_col] = value
                            has_update = True
                        # Keep existing value if present, otherwise ensure column exists
                        elif csv_col not in row:
                            row[csv_col] = ""
                    
                    matches += 1
                    if has_update:
                        updated += 1
                else:
                    # Ensure columns exist but don't overwrite existing values
                    for csv_col in JSON_MAPPINGS.keys():
                        if csv_col not in row:
                            row[csv_col] = ""
                    no_matches += 1
                
                rows.append(row)
        
        # Write updated CSV
        with open(output_file, 'w', encoding='utf-8', newline='') as outfile:
            writer = csv.DictWriter(outfile, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        
        total_rows = matches + no_matches
        
        print("\n" + "="*60)
        print("ENRICHMENT STATISTICS (JSON/API MODE)")
        print("="*60)
        print(f"Total rows in target file: {total_rows}")
        print(f"Rows with matching titles: {matches}")
        print(f"Rows updated with new data: {updated}")
        print(f"Rows not matched: {no_matches}")
        print(f"\nMatch rate: {matches/total_rows*100:.2f}%")
        print(f"Update rate: {updated/total_rows*100:.2f}%")
        print("="*60)
        
        return output_file
        
    except FileNotFoundError:
        print(f"Error: CSV file '{csv_file}' not found.")
        sys.exit(1)
    except Exception as e:
        print(f"Error processing CSV: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description="Enrich CSV with journal/publisher data from CSV, JSON, or API.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
MODES OF OPERATION:

1. CSV-to-CSV Mode (--mode csv):
   Matches rows by 'handle' column and copies journal/publisher data.
   
   Usage:
     python enrich_csv.py target.csv source.csv --mode csv
   
   Columns copied: journal_title, journal_issn(s), journal_uuid, 
                   publisher_name, publisher_uuid

2. JSON/API Mode (--mode json):
   Matches by journal title and populates journal/publisher data.
   
   Usage with JSON file:
     python enrich_csv.py target.csv journals.json --mode json
   
   Usage with API (production):
     python enrich_csv.py target.csv api --mode json
   
   Usage with API (test/staging):
     python enrich_csv.py target.csv api --mode json --test
   
   Columns populated: journal_uuid, publisher_uuid, journal_title

EXAMPLES:

  # Enrich from another CSV by matching handles
  python enrich_csv.py target.csv source.csv --mode csv
  
  # Enrich from JSON file by matching journal titles
  python enrich_csv.py target.csv journals.json --mode json
  
  # Enrich from production API
  python enrich_csv.py target.csv api --mode json
  
  # Enrich from staging API
  python enrich_csv.py target.csv api --mode json --test

OUTPUT:
  All modes create a file named 'enriched_<original_filename>.csv'
  in the same directory as the target file.
        """
    )
    
    parser.add_argument("target_file", help="Path to target CSV file to enrich")
    parser.add_argument("source", help="Path to source CSV/JSON file, or 'api' for API mode")
    parser.add_argument("--mode", choices=['csv', 'json'], required=True,
                       help="Enrichment mode: 'csv' (match by handle) or 'json' (match by title)")
    parser.add_argument("--test", action='store_true',
                       help="Use test/staging environment for API calls (JSON mode only)")
    
    args = parser.parse_args()
    
    # Check if target file exists
    if not Path(args.target_file).exists():
        print(f"Error: Target file not found: {args.target_file}")
        sys.exit(1)
    
    try:
        if args.mode == 'csv':
            # CSV-to-CSV mode
            if not Path(args.source).exists():
                print(f"Error: Source file not found: {args.source}")
                sys.exit(1)
            
            output_path = enrich_from_csv(args.source, args.target_file)
            
        else:  # json mode
            # JSON/API mode
            if args.source != 'api' and not Path(args.source).exists():
                print(f"Error: JSON file not found: {args.source}")
                sys.exit(1)
            
            output_path = enrich_from_json(args.target_file, args.source, args.test)
        
        print(f"\n✓ Success! Enriched file saved to: {output_path}")
        
    except Exception as e:
        print(f"\n✗ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()