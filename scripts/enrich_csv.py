#!/usr/bin/env python3
"""
CSV Enrichment Script
Enrich a target CSV with journal/publisher data from either:
1. Another CSV file (matching by 'handle' column)
2. JSON file (matching by journal/publisher title -- requires --type)
3. Uploader log JSON (matching by journal/publisher title -- requires --type)
"""

import csv
import json
import argparse
import sys
import os
import pandas as pd
from pathlib import Path

# Mapping configuration for JSON mode — journals.
# Values are dot-notation paths into the journal JSON object.
# All target columns are only written when the existing CSV cell is empty.
JOURNAL_JSON_MAPPINGS = {
    "journal_uuid": "uuid",
    "publisher_uuid": "publisher.uuid",
    "journal_title": "titles.0.title",
}

# Mapping configuration for JSON mode — publishers.
# Only publisher_uuid is written; publisher_name is left as-is in the CSV.
# The target column is only written when the existing CSV cell is empty.
PUBLISHER_JSON_MAPPINGS = {
    "publisher_uuid": "uuid",
}

# UUID column name per record type (log mode)
LOG_UUID_COLUMN = {
    "journals": "journal_uuid",
    "publishers": "publisher_uuid",
}

_COMPARISON_STRIP = str.maketrans("", "", """—!–¿()-[]{};:'"''""‐\\,<>./?@#$%^&=+|£€*_~®™©0123456789""")


def _normalise(s: str) -> str:
    return s.strip().lower().translate(_COMPARISON_STRIP)


def _resolve_publisher_name(row: dict) -> str | None:
    """
    Return the publisher name from a CSV row (dc.publisher column only).

    Args:
        row: A csv.DictReader row dict

    Returns:
        Publisher name string, or None if the column is absent or empty
    """
    return row.get("dc.publisher", "").strip() or None


# ---------------------------------------------------------------------------
# CSV mode
# ---------------------------------------------------------------------------

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
        'journal_issn',
        'journal_uuid',
        'publisher_name',
        'publisher_uuid',
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
        if pd.notna(handle):
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

            has_update = False
            for col in columns_to_copy:
                if pd.notna(source_data.get(col)):
                    target_df.at[idx, col] = source_data[col]
                    has_update = True

            if has_update:
                updated_rows += 1

    # Generate output filename
    target_path_obj = Path(target_path)
    output_path = target_path_obj.parent / f"enriched_{target_path_obj.name}"

    print(f"\nSaving enriched file: {output_path}")
    target_df.to_csv(output_path, index=False)

    print("\n" + "="*60)
    print("ENRICHMENT STATISTICS (CSV MODE)")
    print("="*60)
    print(f"Total rows in target file:  {total_rows}")
    print(f"Rows with matching handles: {matched_rows}")
    print(f"Rows updated with new data: {updated_rows}")
    print(f"Rows not matched:           {total_rows - matched_rows}")
    print(f"\nMatch rate:  {matched_rows/total_rows*100:.2f}%")
    print(f"Update rate: {updated_rows/total_rows*100:.2f}%")
    print("="*60)

    return output_path


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def extract_value(item, key_path):
    """
    Extract a value from a nested dictionary using dot notation.
    Supports array indexing (e.g. 'titles.0.title').

    Args:
        item: Dictionary to extract from
        key_path: Dot-separated path (e.g. 'uuid' or 'titles.0.title')

    Returns:
        Extracted value or None
    """
    keys = key_path.split('.')
    value = item

    for key in keys:
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


# ---------------------------------------------------------------------------
# JSON mode — lookup builders
# ---------------------------------------------------------------------------

def create_journal_lookup(data):
    """
    Build lookup dictionaries for JSON mode — journals.

    Primary key  : (normalised_title, journal_uuid)
    Fallback key : normalised_title

    Args:
        data: List of journal objects from the JSON file

    Returns:
        dict mapping keys to journal objects
    """
    lookup = {}

    for item in data:
        if 'titles' not in item or not item['titles']:
            continue

        title = item['titles'][0]['title']
        norm_title = _normalise(title)
        journal_uuid = item.get('uuid')

        # Primary: composite key (normalised_title, journal_uuid)
        if journal_uuid:
            composite_key = (norm_title, journal_uuid)
            lookup[composite_key] = item

        # Fallback: normalised title only (first occurrence wins)
        if norm_title not in lookup:
            lookup[norm_title] = item

    return lookup


def create_publisher_lookup(data):
    """
    Build a lookup dictionary for JSON mode — publishers.

    Key  : normalised name (via _normalise)
    Value: publisher object

    Args:
        data: List of publisher objects from the JSON file.
              Each object must have at least 'uuid' and 'name'.

    Returns:
        dict mapping normalised name to publisher object
    """
    lookup = {}

    for item in data:
        name = item.get('name', '').strip()
        if not name or not item.get('uuid'):
            continue
        key = _normalise(name)
        if key not in lookup:   # first occurrence wins
            lookup[key] = item

    return lookup


# ---------------------------------------------------------------------------
# Log mode — lookup builder
# ---------------------------------------------------------------------------

def create_log_lookup(data, record_type):
    """
    Build a lookup dictionary for log mode.

    Key  : normalised name (via _normalise)
    Value: uuid string

    Only entries with success=True and type matching record_type are included.

    Args:
        data: List of log entry objects
        record_type: 'journals' or 'publishers'

    Returns:
        dict mapping normalised name to uuid
    """
    lookup = {}

    for entry in data:
        if not entry.get("success") or not entry.get("uuid"):
            continue
        if entry.get("type") != record_type:
            continue
        name = entry.get("name", "")
        if name:
            key = _normalise(name)
            if key not in lookup:
                lookup[key] = entry["uuid"]

    return lookup


# ---------------------------------------------------------------------------
# JSON mode — main enrichment function
# ---------------------------------------------------------------------------

def enrich_from_json(csv_file, json_file, record_type):
    """
    Enrich CSV columns with data from a Pure JSON file.

    Journals  : matches by (normalised title, journal_uuid) with title-only fallback;
                writes journal_uuid, journal_issn, publisher_uuid, journal_title —
                but only into cells that are currently empty.
    Publishers: matches by dc.publisher; writes publisher_uuid only, and only when
                the cell is currently empty.

    Args:
        csv_file: Path to input CSV file
        json_file: Path to JSON file (journals or publishers)
        record_type: 'journals' or 'publishers'

    Returns:
        Path to output file
    """
    directory = os.path.dirname(csv_file) or '.'
    output_file = os.path.join(directory, f"enriched_{os.path.basename(csv_file)}")

    try:
        with open(json_file, 'r', encoding='utf-8') as f:
            json_data = json.load(f)
    except FileNotFoundError:
        print(f"Error: JSON file '{json_file}' not found.")
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON in '{json_file}': {e}")
        sys.exit(1)

    if isinstance(json_data, dict) and 'items' in json_data:
        json_data = json_data['items']

    if record_type == 'journals':
        lookup = create_journal_lookup(json_data)
        mappings = JOURNAL_JSON_MAPPINGS
        print(f"Loaded {len(json_data)} journals from {json_file}")
    else:  # publishers
        lookup = create_publisher_lookup(json_data)
        mappings = PUBLISHER_JSON_MAPPINGS
        print(f"Loaded {len(lookup)} publishers from {json_file}")

    try:
        with open(csv_file, 'r', encoding='utf-8') as infile:
            reader = csv.DictReader(infile)
            fieldnames = list(reader.fieldnames)

            for csv_col in mappings:
                if csv_col not in fieldnames:
                    fieldnames.append(csv_col)

            rows = []
            matches = 0
            no_matches = 0
            updated = 0
            skipped = 0   # matched but all writable cells already had values

            for row in reader:
                matched = False
                item = None

                if record_type == 'journals':
                    # Detect journal title and journal_uuid columns (case-insensitive)
                    title_col = None
                    journal_uuid_col = None

                    for col in row.keys():
                        col_lower = col.lower()
                        if 'journal' in col_lower and 'title' in col_lower:
                            title_col = col
                        elif 'title' in col_lower and not title_col:
                            title_col = col
                        if 'journal' in col_lower and 'uuid' in col_lower:
                            journal_uuid_col = col

                    if title_col and row.get(title_col):
                        norm_title = _normalise(row[title_col])
                        journal_uuid = row.get(journal_uuid_col, "").strip() if journal_uuid_col else None

                        # Primary: composite match (normalised title + journal_uuid)
                        if journal_uuid:
                            item = lookup.get((norm_title, journal_uuid))
                            if item:
                                matched = True

                        # Fallback: normalised title only
                        if not matched:
                            item = lookup.get(norm_title)
                            if item:
                                matched = True

                else:  # publishers
                    pub_name = _resolve_publisher_name(row)
                    if pub_name:
                        item = lookup.get(_normalise(pub_name))
                        if item:
                            matched = True

                if matched and item:
                    has_update = False
                    for csv_col, json_key in mappings.items():
                        # Only write into cells that are currently empty
                        if row.get(csv_col, "").strip():
                            continue
                        value = extract_value(item, json_key)
                        if value is not None and value != "":
                            row[csv_col] = value
                            has_update = True
                    matches += 1
                    if has_update:
                        updated += 1
                    else:
                        skipped += 1
                else:
                    # Ensure output columns exist in the row even when unmatched
                    for csv_col in mappings:
                        if csv_col not in row:
                            row[csv_col] = ""
                    no_matches += 1

                rows.append(row)

        with open(output_file, 'w', encoding='utf-8', newline='') as outfile:
            writer = csv.DictWriter(outfile, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

        total_rows = matches + no_matches
        print("\n" + "="*60)
        print(f"ENRICHMENT STATISTICS (JSON MODE — {record_type.upper()})")
        print("="*60)
        print(f"Total rows in target file:  {total_rows}")
        print(f"Rows matched:               {matches}")
        print(f"Rows updated with new data: {updated}")
        print(f"Rows already had values:    {skipped}")
        print(f"Rows not matched:           {no_matches}")
        print(f"\nMatch rate:  {matches/total_rows*100:.2f}%")
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


# ---------------------------------------------------------------------------
# Log mode — main enrichment function
# ---------------------------------------------------------------------------

def enrich_from_log(csv_file, log_file, record_type):
    """
    Enrich CSV with UUIDs taken from an uploader log JSON file.

    Journals  : matches by normalised journal title column.
    Publishers: matches by dc.publisher.

    The UUID is written to 'journal_uuid' or 'publisher_uuid' depending on
    record_type, and only when that cell is currently empty.

    Args:
        csv_file: Path to input CSV file
        log_file: Path to uploader log JSON file
        record_type: 'journals' or 'publishers'

    Returns:
        Path to output file
    """
    uuid_column = LOG_UUID_COLUMN[record_type]

    directory = os.path.dirname(csv_file) or '.'
    output_file = os.path.join(directory, f"enriched_{os.path.basename(csv_file)}")

    try:
        with open(log_file, 'r', encoding='utf-8') as f:
            log_data = json.load(f)
    except FileNotFoundError:
        print(f"Error: Log file '{log_file}' not found.")
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON in '{log_file}': {e}")
        sys.exit(1)

    lookup = create_log_lookup(log_data, record_type)
    print(f"Loaded {len(lookup)} {record_type} entries from log {log_file}")

    try:
        with open(csv_file, 'r', encoding='utf-8') as infile:
            reader = csv.DictReader(infile)
            fieldnames = list(reader.fieldnames)

            if uuid_column not in fieldnames:
                fieldnames.append(uuid_column)

            rows = []
            matches = 0       # matched and written (cell was empty)
            skipped = 0       # matched but cell already had a value
            no_matches = 0

            for row in reader:
                name_to_match = None

                if record_type == 'journals':
                    # Detect journal title column (case-insensitive)
                    title_col = None
                    for col in row.keys():
                        col_lower = col.lower()
                        if 'journal' in col_lower and 'title' in col_lower:
                            title_col = col
                            break
                        elif 'title' in col_lower and not title_col:
                            title_col = col
                    if title_col:
                        name_to_match = row.get(title_col, "").strip() or None
                else:  # publishers
                    name_to_match = _resolve_publisher_name(row)

                matched = False
                if name_to_match:
                    uuid = lookup.get(_normalise(name_to_match))
                    if uuid:
                        matched = True
                        if not row.get(uuid_column, "").strip():
                            row[uuid_column] = uuid
                            matches += 1
                        else:
                            skipped += 1

                if not matched:
                    if uuid_column not in row:
                        row[uuid_column] = ""
                    no_matches += 1

                rows.append(row)

        with open(output_file, 'w', encoding='utf-8', newline='') as outfile:
            writer = csv.DictWriter(outfile, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

        total_rows = matches + skipped + no_matches
        print("\n" + "="*60)
        print(f"ENRICHMENT STATISTICS (LOG MODE — {record_type.upper()})")
        print("="*60)
        print(f"Total rows in target file:     {total_rows}")
        print(f"Rows matched:                  {matches + skipped}")
        print(f"Rows updated ({uuid_column}): {matches}")
        print(f"Rows already had value:        {skipped}")
        print(f"Rows not matched:              {no_matches}")
        print(f"\nMatch rate:  {(matches + skipped)/total_rows*100:.2f}%")
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


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Enrich CSV with journal/publisher data from CSV, JSON, or uploader log.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
MODES OF OPERATION:

1. CSV mode (--mode csv):
   Matches rows by 'handle' column and copies journal/publisher data.

   python enrich_csv.py target.csv source.csv --mode csv

2. JSON mode (--mode json):
   Matches against a Pure JSON export and populates journal or publisher fields.
   Requires --type to specify which record type to match.

   python enrich_csv.py target.csv journals.json   --mode json --type journals
   python enrich_csv.py target.csv publishers.json --mode json --type publishers

   journals  : matches by journal title column; writes journal_uuid, journal_issn,
               publisher_uuid, journal_title — only into empty cells.
   publishers: matches by dc.publisher; writes publisher_uuid — only into empty cells.

3. Log mode (--mode log):
   Matches against an uploader log and populates journal_uuid or publisher_uuid.
   Requires --type to specify which record type to match.

   python enrich_csv.py target.csv upload_log.json --mode log --type journals
   python enrich_csv.py target.csv upload_log.json --mode log --type publishers

   journals  : matches by journal title column; writes journal_uuid — only into empty cells.
   publishers: matches by dc.publisher; writes publisher_uuid — only into empty cells.

OUTPUT:
  All modes write 'enriched_<original_filename>' in the same directory as the target file.
        """
    )

    parser.add_argument("target", help="Path to target DSpace CSV file to enrich")
    parser.add_argument("source", help="Path to source CSV, Pure JSON, or uploader log file")
    parser.add_argument("--mode", choices=['csv', 'json', 'log'], required=True,
                        help="Enrichment mode")
    parser.add_argument("--type", choices=['journals', 'publishers'], dest="record_type",
                        help="Record type (required when --mode json or --mode log)")

    args = parser.parse_args()

    if args.mode in ('json', 'log') and not args.record_type:
        parser.error(f"--type is required when --mode {args.mode}")

    if not Path(args.target).exists():
        print(f"Error: Target file not found: {args.target}")
        sys.exit(1)

    if not Path(args.source).exists():
        print(f"Error: Source file not found: {args.source}")
        sys.exit(1)

    try:
        if args.mode == 'csv':
            output_path = enrich_from_csv(args.source, args.target)
        elif args.mode == 'json':
            output_path = enrich_from_json(args.target, args.source, args.record_type)
        else:  # log
            output_path = enrich_from_log(args.target, args.source, args.record_type)

        print(f"\n✓ Success! Enriched file saved to: {output_path}")

    except Exception as e:
        print(f"\n✗ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()