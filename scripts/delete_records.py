#!/usr/bin/env python3

from email import parser
import os
import json
import argparse
import requests
from datetime import datetime
from dotenv import load_dotenv


VALID_ENDPOINTS = {
    "research-outputs",
    "persons",
    "external-persons",
    "organizations",
    "external-organizations",
    "journals",
    "publishers",       
}


def parse_date(date_string):
    """
    Parse date or datetime string to a timezone-aware datetime object.
    Accepts:
      - Full ISO datetime: 2025-12-14T00:00:00Z
      - Date only:        2025-12-14  (assumed 00:00:00 UTC)
    """
    if not date_string:
        return None
    try:
        # Date-only format — append time so fromisoformat can parse it
        if len(date_string.strip()) == 10:
            date_string = date_string.strip() + "T00:00:00Z"
        return datetime.fromisoformat(date_string.replace('Z', '+00:00'))
    except Exception as e:
        print(f"Error parsing date '{date_string}': {e}")
        return None


def load_records_from_log(log_path):
    """
    Load records from JSON log file.
    Returns list of records or empty list if error.
    """
    try:
        with open(log_path, 'r', encoding='utf-8') as f:
            records = json.load(f)
        return records if isinstance(records, list) else [records]
    except Exception as e:
        print(f"Error reading log file {log_path}: {e}")
        return []


def get_data_type_from_record(record):
    """
    Resolve the correct API endpoint segment for a record.
    Reads the 'type' field from the log, validates it, and remaps
    any log labels that differ from the actual API path (e.g. publishers).
    """
    raw_type = record.get("data", "research-outputs")  # default to research-outputs if type is missing
    if isinstance(raw_type, str) and raw_type.strip().lower() in VALID_ENDPOINTS:
        endpoint = raw_type.strip().lower()
        return endpoint

    # Explicit override key for manually patched log entries
    if endpoint := record.get("endpoint", ""):
        return endpoint

    # Fallback
    print(f"  ⚠️ Could not determine endpoint for record "
          f"{record.get('uuid', '(no uuid)')} — defaulting to research-outputs")
    return "research-outputs"


def filter_records_by_date(records, cutoff_date):
    """
    Filter records to delete.
    If cutoff_date is None, all records are returned.
    Otherwise only records modified after cutoff_date are returned.
    """
    if cutoff_date is None:
        return list(records)

    cutoff_dt = parse_date(cutoff_date)
    if not cutoff_dt:
        print(f"Invalid cutoff date: {cutoff_date}")
        return []

    records_to_delete = []
    for record in records:
        modified_date = record.get("modifiedDate", "")
        if not modified_date:
            continue
        modified_dt = parse_date(modified_date)
        if modified_dt and modified_dt > cutoff_dt:
            records_to_delete.append(record)

    return records_to_delete


def delete_record(uuid, data_type, session, base_url, headers):
    """
    Delete a single record via API.
    Returns (success, status_code, message).
    - 204: deleted successfully
    - 404: record not found in Pure (already deleted or never created)
    - anything else: genuine failure
    """
    try:
        url = f"{base_url}/{data_type}/{uuid}"
        resp = session.delete(url, headers=headers, timeout=60)

        if resp.status_code == 204:
            return "deleted", resp.status_code, "Successfully deleted"
        elif resp.status_code == 404:
            return "not_found", resp.status_code, "Record not found in Pure (already deleted or never existed)"
        else:
            return "failed", resp.status_code, resp.text
    except Exception as e:
        return "failed", None, str(e)


def save_deletion_log(deleted_records, not_found_records, failed_records, log_dir):
    timestamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')

    if deleted_records:
        path = os.path.join(log_dir, f"deleted_records_{timestamp}.json")
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(deleted_records, f, indent=2, ensure_ascii=False)
        print(f"✅ Deleted records logged to: {path}")

    if not_found_records:
        path = os.path.join(log_dir, f"not_found_records_{timestamp}.json")
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(not_found_records, f, indent=2, ensure_ascii=False)
        print(f"⚠️ Not-found records logged to: {path}")

    if failed_records:
        path = os.path.join(log_dir, f"failed_deletions_{timestamp}.json")
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(failed_records, f, indent=2, ensure_ascii=False)
        print(f"❌ Failed deletions logged to: {path}")


def main():
    parser = argparse.ArgumentParser(
        description="Delete records from Pure API based on modification date"
    )
    parser.add_argument(
        "--log",
        default=None,
        help="Path to a single JSON log file"
    )
    parser.add_argument(
        "--log-dir",
        default=None,
        help="Path to a folder of JSON log files — all .json files will be processed"
    )
    parser.add_argument(
        "--log-output-dir",
        default=None,
        help=(
            "Directory to save deletion logs. "
            "Defaults to a 'deletion_logs' folder next to the processed log file, "
            "or inside the processed folder if --log-dir is used."
        )
    )
    parser.add_argument(
        "--after-date",
        default=None,
        help=(
            "Only delete records modified after this date. "
            "Accepts date (2025-12-01) or datetime (2025-12-01T00:00:00Z). "
            "If omitted, all records in the log are deleted."
        )
    )
    parser.add_argument(
        "--test", 
        action="store_true",
        default=False, 
        help="Use UAT (--test) or Production (default)"
    )
    parser.add_argument(
        "--dry-run", 
        action="store_true",
        help="Show what would be deleted without actually deleting"
    )
    
    args = parser.parse_args()
    
    # Load environment variables
    load_dotenv()
    api_key_var = "PURE_ROOT_API_KEY_TEST" if args.test else "PURE_ROOT_API_KEY"
    API_KEY = os.getenv(api_key_var, "")

    if not API_KEY:
        print(f"⚠️ WARNING: {api_key_var} not found in environment variables.")
        return
    
    # Set base URL
    BASE_URL = (
        "https://galway-staging.elsevierpure.com/ws/api" 
        if args.test 
        else "https://research.universityofgalway.ie/ws/api"
    )
    
    HEADERS = {
        "accept": "application/json",
        "api-key": API_KEY
    }
    
    # Resolve log output directory
    if args.log_output_dir:
        LOG_DIR = args.log_output_dir
    elif args.log_dir:
        LOG_DIR = os.path.join(args.log_dir, "deletion_logs")
    else:
        # Place deletion_logs next to the single log file
        LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(args.log)), "deletion_logs")

    os.makedirs(LOG_DIR, exist_ok=True)
    print(f"Deletion logs will be saved to: {LOG_DIR}")
    
    # Load records from log file(s)
    if not args.log and not args.log_dir:
        print("⚠️ ERROR: Either --log or --log-dir must be provided.")
        return

    log_files = []
    if args.log:
        log_files.append(args.log)
    if args.log_dir:
        if not os.path.isdir(args.log_dir):
            print(f"⚠️ ERROR: --log-dir path is not a directory: {args.log_dir}")
            return
        log_files.extend(
            os.path.join(args.log_dir, f)
            for f in sorted(os.listdir(args.log_dir))
            if f.endswith(".json")
        )
        if not log_files:
            print(f"No .json files found in: {args.log_dir}")
            return

    records = []
    for log_file in log_files:
        print(f"Loading records from: {log_file}")
        file_records = load_records_from_log(log_file)
        print(f"  → {len(file_records)} records loaded")
        records.extend(file_records)

    if not records:
        print("No records found in any log file.")
        return

    print(f"Total records across all logs: {len(records)}")
    
    # Filter records by date
    cutoff_msg = f"modified after {args.after_date}" if args.after_date else "all dates (no cutoff)"
    print(f"Filtering records — {cutoff_msg}")
    records_to_delete = filter_records_by_date(records, args.after_date)
    
    if not records_to_delete:
        print("No records match the deletion criteria.")
        return
    
    print(f"\n{'DRY RUN: ' if args.dry_run else ''}Found {len(records_to_delete)} records to delete:")
    print("-" * 80)
    
    for record in records_to_delete:
        resolved_endpoint = args.data_type if hasattr(args, 'data_type') and args.data_type else get_data_type_from_record(record)
        print(f"  Endpoint : {resolved_endpoint}")
        print(f"  UUID     : {record.get('uuid')}")
        print(f"  Name     : {record.get('name') or '(no name)'}")
        print(f"  Modified : {record.get('modifiedDate')}")
        print()
    
    if args.dry_run:
        print("DRY RUN: No records were actually deleted.")
        return
    
    # Confirm deletion
    confirm = input(f"\nAre you sure you want to DELETE these {len(records_to_delete)} records? (yes/no): ")
    if confirm.lower() != 'yes':
        print("Deletion cancelled.")
        return
    
    # Delete records
    session = requests.Session()
    deleted_records = []
    not_found_records = []
    failed_records = []

    print("\nDeleting records...")
    for record in records_to_delete:
        uuid = record.get("uuid")
        name = record.get("name") or "(no name)"
        data_type = args.data_type if hasattr(args, 'data_type') and args.data_type else get_data_type_from_record(record)

        if not uuid:
            print(f"⚠️ Skipping record with no UUID: {name}")
            failed_records.append({**record, "error": "No UUID in log entry"})
            continue

        status, status_code, message = delete_record(
            uuid, data_type, session, BASE_URL, HEADERS
        )

        if status == "deleted":
            print(f"✅ Deleted     : {name} ({uuid}) [{data_type}]")
            deleted_records.append({**record, "deletedAt": datetime.now().isoformat()})
        elif status == "not_found":
            print(f"⚠️ Not found   : {name} ({uuid}) [{data_type}] — skipping")
            not_found_records.append({**record, "note": message})
        else:
            print(f"❌ Failed      : {name} ({uuid}) [{data_type}] — {status_code}: {message}")
            failed_records.append({**record, "error": f"{status_code}: {message}"})
    
    # Summary
    print("\n" + "=" * 80)
    print(f"Deletion complete:")
    print(f"  Successfully deleted : {len(deleted_records)}")
    print(f"  Not found in Pure    : {len(not_found_records)}")
    print(f"  Failed               : {len(failed_records)}")
    print("=" * 80)
    
    # Save logs
    save_deletion_log(deleted_records, not_found_records, failed_records, LOG_DIR)
    

if __name__ == "__main__":
    main()