#!/usr/bin/env python3

import os
import sys
import csv
import json
import argparse
import requests
from tqdm import tqdm
from datetime import datetime
from dotenv import load_dotenv


TODAY = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
TODAY_DATE = datetime.now().strftime('%Y-%m-%d')

def extract_localized_value(field, field_name="value"):
    """
    Extract value from localized field with multiple possible formats:
    1) Direct string: "name": "some name"
    2) Localized dict: "name": {"en_IE": "some name"}
    3) Value dict: "title": {"value": "some title"}
    """
    if not field:
        return ""
    
    # Format 1: Direct string
    if isinstance(field, str):
        return field
    
    # Format 2: {"value": "some title"}
    if isinstance(field, dict) and field_name in field:
        return field[field_name]
    
    # Format 3: {"en_IE": "some name"}
    if isinstance(field, dict):
        # Try language codes in order of preference
        for lang_code in ["en_IE", "en_GB", "en_US"]:
            if lang_code in field:
                return field[lang_code]
        # Fallback: take any available value
        if field:
            return next(iter(field.values()), "")
    
    return ""

def extract_name_from_response(response_data, data_type):
    """
    Extract name/title from API response based on data type.
    Returns empty string if not found.
    """
    # For persons and external-persons, combine firstName and lastName
    if data_type in ["persons", "external-persons"]:
        name_obj = response_data.get("name", {})
        first_name = name_obj.get("firstName", "")
        last_name = name_obj.get("lastName", "")
        return f"{last_name}, {first_name}".strip()
    
    # For research-outputs, events: use "title"
    if data_type in ["research-outputs", "events"]:
        title = response_data.get("title")
        return extract_localized_value(title, "value")
    
    # For journals: use "title"
    if data_type in ["journals"]:
        titles = response_data.get("titles", [])
        if len(titles) > 0:
            title = titles[0].get("title", "") 
        else: 
            title = ""
        return title
    
    # For organizations, external-organizations, publishers: use "name"
    if data_type in ["organizations", "external-organizations", "publishers"]:
        name = response_data.get("name")
        return extract_localized_value(name, "value")
    
    return ""

def extract_handle_from_links(response_data):
    """
    Extract handle URL from links array in API response.
    Looks for URL containing 'hdl.handle.net'.
    Returns empty string if not found.
    """
    links = response_data.get("links", [])
    if not isinstance(links, list):
        return ""
    
    for link in links:
        if isinstance(link, dict):
            url = link.get("url", "")
            if "hdl.handle.net" in url:
                return url
    
    return ""

def log_record(mode, data_type, response_data, log_dir, is_test):
    """
    Log created or updated record to appropriate JSON file.
    """
    log_filename = f"created_records_{TODAY}.json" if mode == "create" else f"updated_records_{TODAY}.json"
    log_path = os.path.join(log_dir, log_filename)
    
    # Extract required fields
    uuid = response_data.get("uuid", "")
    modified_date = response_data.get("modifiedDate", "")
    created_date = response_data.get("createdDate", "")
    name = extract_name_from_response(response_data, data_type)
    created_by = response_data.get("createdBy", "")
    modified_by = response_data.get("modifiedBy", "")

    # Create record entry
    record_entry = {
        "data": data_type,
        "name": name,
        "uuid": uuid,
        "createdDate": created_date,
        "modifiedDate": modified_date,
        "createdBy": created_by,
        "modifiedBy": modified_by,
        "success": True  
    }
    
    # Add research-outputs specific fields
    if data_type == "research-outputs":
        portal_url = response_data.get("portalUrl", "")
        handle = extract_handle_from_links(response_data)
        record_entry["portalUrl"] = portal_url
        record_entry["handle"] = handle
        
        # Add portalUrlPROD only in test mode for UPDATE operations
        # (created records don't exist in production yet)
        if is_test and mode == "update":
            record_entry["portalUrlPROD"] = f"https://research.universityofgalway.ie/en/publications/{uuid}"
    
    # Load existing log or create new list
    existing_records = []
    if os.path.exists(log_path):
        try:
            with open(log_path, 'r', encoding='utf-8') as f:
                existing_records = json.load(f)
        except Exception:
            existing_records = []
    
    # Append new record
    existing_records.append(record_entry)
    
    # Write back to file
    with open(log_path, 'w', encoding='utf-8') as f:
        json.dump(existing_records, f, indent=2, ensure_ascii=False)

def save_failed_records(failed_records, source_path, data_type, mode):
    """
    Save all failed records to a single JSON file in the source directory.
    """
    if not failed_records:
        return None
    
    # Determine output directory
    source_dir = os.path.dirname(source_path)
    output_dir = os.path.abspath(source_dir)
    
    output_filename = f"failed_records_{data_type}_{mode}_{TODAY}.json"
    output_path = os.path.join(output_dir, output_filename)
    
    # Write failed records to file
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(failed_records, f, indent=2, ensure_ascii=False)
    
    return output_path

def process_file(path, mode, data_type, session, error_log, log_dir, is_test):
    """
    mode: 'create' or 'update'
    data_type: the data type to upload (e.g., 'research-outputs', 'persons')
    For update: expects the top-level dict to contain 'uuid' key (string).
    Returns: (success_count, results_list, failed_records_list)
    """
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception as e:
        error_log.append(f"Failed to read {path}: {e}")
        return 0, [f"read_error: {e}"], []

    # Data can be a list of records or a single record. Normalize to list.
    records = data if isinstance(data, list) else [data]

    results = []
    failed_records = []
    success_count = 0
    
    # Add progress bar for records within the file
    record_pbar = tqdm(records, desc=f"  Processing records", unit="rec", leave=False)
    
    for rec in record_pbar:
        # Extract identifiers for error logging
        record_title = extract_name_from_response(rec, data_type)
        record_handle = extract_handle_from_links(rec)
        record_uuid = rec.get("uuid", "N/A")
        record_id = f"[Title: {record_title}, Handle: {record_handle}, UUID: {record_uuid}]"
        
        try:
            if mode == "update":
                uuid = rec.get("uuid")
                if not uuid:
                    error_log.append(f"No uuid in record for update in file {path} - {record_id}")
                    results.append((path, False, "no_uuid"))
                    failed_records.append(rec)
                    continue
                resp = session.put(f"{PURE_BASE_URL}/{data_type}/{uuid}", headers=HEADERS, json=rec, timeout=60)
            elif mode == "create":
                resp = session.put(f"{PURE_BASE_URL}/{data_type}", headers=HEADERS, json=rec, timeout=60)
            else:
                error_log.append(f"Invalid mode '{mode}' for file {path} - {record_id}")
                results.append((path, False, f"bad_mode:{mode}"))
                failed_records.append(rec)
                continue

            if resp.status_code in (200, 201):
                # Log the successful create/update
                try:
                    response_data = resp.json()
                    log_record(mode, data_type, response_data, log_dir, is_test)
                    success_count += 1
                except Exception as e:
                    error_log.append(f"Failed to log record from {path} - {record_id}: {e}")
                
                results.append((path, True, f"{resp.status_code}"))
            else:
                err_text = f"{resp.status_code} - {resp.text}"
                error_log.append(f"API error for {path} - {record_id}: {err_text}")
                results.append((path, False, err_text))
                failed_records.append(rec)
        except Exception as e:
            error_log.append(f"Exception when sending {path} - {record_id}: {e}")
            results.append((path, False, str(e)))
            failed_records.append(rec)
    
    record_pbar.close()
    return success_count, results, failed_records


def process_folder(folder_path, data_type, session, error_log, log_dir, is_test):
    # For folder mode, determine mode by folder name: matched -> update, unmatched -> create
    mode = "update" if os.path.basename(folder_path).lower().startswith("matched") else "create"
    json_files = []
    for root, _, files in os.walk(folder_path):
        for fn in files:
            if fn.lower().endswith(".json"):
                json_files.append(os.path.join(root, fn))
    
    total_successes = 0
    total_failures = 0
    all_failed_records = []
    
    # Progress bar for files
    for path in tqdm(json_files, desc=f"Uploading files ({mode})", unit="file"):
        success_count, results, failed_records = process_file(path, mode, data_type, session, error_log, log_dir, is_test)
        total_successes += success_count
        total_failures += len(failed_records)
        all_failed_records.extend(failed_records)
    
    # Save failed records if any
    if all_failed_records:
        failed_path = save_failed_records(all_failed_records, folder_path, data_type, mode)
        if failed_path:
            print(f"\n⚠️ {len(all_failed_records)} failed records saved to: {failed_path}")
    
    return total_successes, total_failures, len(json_files)

def process_single_file(path, mode, data_type, session, error_log, log_dir, is_test):
    success_count, results, failed_records = process_file(path, mode, data_type, session, error_log, log_dir, is_test)
    
    # Save failed records if any
    if failed_records:
        failed_path = save_failed_records(failed_records, path, data_type, mode)
        if failed_path:
            print(f"\n⚠️ {len(failed_records)} failed records saved to: {failed_path}")
    
    return success_count, results


def create_csv(log_dir, mode):
    """
    Create CSVs from created and updated records.
    """
    if mode == 'create':
        created_log = os.path.join(log_dir, f"created_records_{TODAY}.json")
        # Process created records
        if os.path.exists(created_log):
            try:
                with open(created_log, 'r', encoding='utf-8') as f:
                    created_records = json.load(f)
                    if created_records:
                        csv_path = os.path.join(log_dir, f"created_records_{TODAY}.csv")
                        rows_written = write_records_to_csv(created_records, csv_path)
                        print(f"📊 Created records CSV: {csv_path} ({rows_written} rows)")
            except Exception as e:
                print(f"Warning: Could not create CSV from created_records.json: {e}")
    
    elif mode == 'update':
        updated_log = os.path.join(log_dir, f"updated_records_{TODAY}.json")
        # Process updated records
        if os.path.exists(updated_log):
            try:
                with open(updated_log, 'r', encoding='utf-8') as f:
                    updated_records = json.load(f)
                    if updated_records:
                        csv_path = os.path.join(log_dir, f"updated_records_{TODAY}.csv")
                        rows_written = write_records_to_csv(updated_records, csv_path)
                        print(f"📊 Updated records CSV: {csv_path} ({rows_written} rows)")
            except Exception as e:
                print(f"Warning: Could not create CSV from updated_records.json: {e}")

def write_records_to_csv(records, csv_path):
    """
    Write records to CSV with specified columns.
    Returns number of rows written.
    """
    if not records:
        return 0
    
    # Ensure records is a list
    records_list = records if isinstance(records, list) else [records]
    
    columns = ["name", "handle", "portalUrl", "portalUrlPROD"]
    
    rows_written = 0
    with open(csv_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction='ignore')
        writer.writeheader()
        for record in records_list:
            if record.get("success", True):  # Default to True if not present
                # Create a row with only the specified columns
                row = {col: record.get(col, "") for col in columns}
                writer.writerow(row)
                rows_written += 1
    
    return rows_written


if __name__ == "__main__":

    parser = argparse.ArgumentParser(description="Upload matched/unmatched JSONs to Pure")
    parser.add_argument("--folder", help="Folder containing JSON files to upload (matched/ or unmatched/)")
    parser.add_argument("--file", help="Single JSON file to upload")
    parser.add_argument("--mode", choices=["create", "update"], help="Mode for single file upload")
    parser.add_argument("--test", action="store_true", default=False, help="Get data from UAT (--test) or Production (default)")
    parser.add_argument("--data", default= "research-outputs", choices=["research-outputs", "persons", "external-persons", 
                        "journals", "events", "organizations", "external-organizations", "publishers"], help="What data to upload to Pure")
    parser.add_argument("--log-dir", default=None, help="Directory to save logs (default: ./logs/uploader_logs next to the input file or folder)")
    args = parser.parse_args()

    if args.log_dir is None:
        input_path = args.folder if args.folder else args.file
        if input_path:
            base = os.path.dirname(os.path.abspath(input_path.rstrip("/\\")))
        else:
            base = os.getcwd()
        args.log_dir = os.path.join(base, "logs")

    LOG_DIR = os.path.join(args.log_dir, "uploader_logs")

    os.makedirs(LOG_DIR, exist_ok=True)
    ERROR_LOG_PATH = os.path.join(LOG_DIR, f"uploader_errors_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.log")

    # Load environment variables from .env file
    load_dotenv()


    api_key_var = "PURE_ROOT_API_KEY_TEST" if args.test else "PURE_ROOT_API_KEY"
    API_KEY = os.getenv(api_key_var, "")

    if not API_KEY:
        print(f"⚠️ WARNING: {api_key_var} not found in environment variables.")

    PURE_BASE_URL = "https://galway-staging.elsevierpure.com/ws/api/" if args.test == True else "https://research.universityofgalway.ie/ws/api/"

    HEADERS = {
            "accept": "application/json",
            "api-key": API_KEY
        }

    # Let HEADERS be used in each request call
    session = requests.Session()
   
    error_log = []

    if args.folder:
        if not os.path.isdir(args.folder):
            print(f"Folder not found: {args.folder}")
            sys.exit(1)
        mode = "update" if os.path.basename(args.folder).lower().startswith("matched") else "create"
        successes, failures, total_files = process_folder(args.folder, args.data, session, error_log, LOG_DIR, args.test)
        print(f"\n✅ Done. {successes} records succeeded, {failures} failed across {total_files} files.")
        success_count = successes
    elif args.file:
        if not os.path.isfile(args.file):
            print(f"File not found: {args.file}")
            sys.exit(1)
        if not args.mode:
            print("For single file uploads please set --mode create|update")
            sys.exit(1)
        mode = args.mode
        success_count, results = process_single_file(args.file, args.mode, args.data, session, error_log, LOG_DIR, args.test)
        print(f"\n✅ Single file results: {success_count} records succeeded")
        # print(f"Details: {results}")
    else:
        print("Please specify --folder or --file")
        sys.exit(1)

    # write errors to log
    if error_log:
        with open(ERROR_LOG_PATH, 'w', encoding='utf-8') as f:
            for line in error_log:
                f.write(line + "\n")
        print(f"⚠️ Errors logged to {ERROR_LOG_PATH}")
    else:
        print("✅ No errors logged")
    
    # Create CSVs from logs only for research-outputs
    if args.data == "research-outputs":
        create_csv(LOG_DIR, mode)
    
    print(f"\n✅ {success_count} records {mode}d and logged")