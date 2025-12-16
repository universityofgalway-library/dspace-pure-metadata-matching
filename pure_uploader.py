#!/usr/bin/env python3

import os
import sys
import json
import argparse
import requests
from tqdm import tqdm
from datetime import datetime
from dotenv import load_dotenv

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
    
    # Format 3: {"value": "some title"}
    if isinstance(field, dict) and field_name in field:
        return field[field_name]
    
    # Format 2: {"en_IE": "some name"}
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
        return f"{first_name} {last_name}".strip()
    
    # For research-outputs, journals, events: use "title"
    if data_type in ["research-outputs", "journals", "events"]:
        title = response_data.get("title")
        return extract_localized_value(title, "value")
    
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

def log_record(mode, data_type, response_data, log_dir):
    """
    Log created or updated record to appropriate JSON file.
    """
    log_filename = "created_records.json" if mode == "create" else "updated_records.json"
    log_path = os.path.join(log_dir, log_filename)
    
    # Extract required fields
    uuid = response_data.get("uuid", "")
    modified_date = response_data.get("modifiedDate", "")
    name = extract_name_from_response(response_data, data_type)
    portal_url = response_data.get("portalUrl", "")
    handle = extract_handle_from_links(response_data)
    created_by = response_data.get("createdBy", "")
    modified_by = response_data.get("modifiedBy", "")
    
    # Create record entry
    record_entry = {
        "type": data_type,
        "name": name.strip(),
        "uuid": uuid,
        "modifiedDate": modified_date,
        "portalUrl": portal_url.strip(),
        "handle": handle.strip(),
        "createdBy": created_by,
        "modifiedBy": modified_by
    }
    
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
    
    timestamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
    
    # Determine output directory
    if os.path.isdir(source_path):
        output_dir = source_path
    else:
        output_dir = os.path.dirname(source_path)
    
    output_filename = f"failed_records_{data_type}_{mode}_{timestamp}.json"
    output_path = os.path.join(output_dir, output_filename)
    
    # Write failed records to file
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(failed_records, f, indent=2, ensure_ascii=False)
    
    return output_path

def process_file(path, mode, data_type, session, error_log, log_dir):
    """
    mode: 'create' or 'update'
    data_type: the data type to upload (e.g., 'research-outputs', 'persons')
    For update: expects the top-level dict to contain 'uuid' key (string).
    Returns: (success_bool, results_list, failed_records_list)
    """
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception as e:
        error_log.append(f"Failed to read {path}: {e}")
        return False, f"read_error: {e}", []

    # Data can be a list of records or a single record. Normalize to list.
    records = data if isinstance(data, list) else [data]

    results = []
    failed_records = []
    
    for rec in records:
        try:
            if mode == "update":
                uuid = rec.get("uuid")
                if not uuid:
                    error_log.append(f"No uuid in record for update: {path}")
                    results.append((path, False, "no_uuid"))
                    failed_records.append(rec)
                    continue
                resp = session.put(f"{PURE_BASE_URL}/{data_type}/{uuid}", headers=HEADERS, json=rec, timeout=60)
            elif mode == "create":
                resp = session.put(f"{PURE_BASE_URL}/{data_type}", headers=HEADERS, json=rec, timeout=60)
            else:
                results.append((path, False, f"bad_mode:{mode}"))
                failed_records.append(rec)
                continue

            if resp.status_code in (200, 201):
                # Log the successful create/update
                try:
                    response_data = resp.json()
                    log_record(mode, data_type, response_data, log_dir)
                except Exception as e:
                    error_log.append(f"Failed to log record from {path}: {e}")
                
                results.append((path, True, f"{resp.status_code}"))
            else:
                err_text = f"{resp.status_code} - {resp.text}"
                error_log.append(f"API error for {path}: {err_text}")
                results.append((path, False, err_text))
                failed_records.append(rec)
        except Exception as e:
            error_log.append(f"Exception when sending {path}: {e}")
            results.append((path, False, str(e)))
            failed_records.append(rec)

    # return overall success if all succeeded
    all_ok = all(r[1] for r in results)
    return all_ok, results, failed_records


def process_folder(folder_path, data_type, session, error_log, log_dir):
    # For folder mode, determine mode by folder name: matched -> update, unmatched -> create
    mode = "update" if os.path.basename(folder_path).lower().startswith("matched") else "create"
    json_files = []
    for root, _, files in os.walk(folder_path):
        for fn in files:
            if fn.lower().endswith(".json"):
                json_files.append(os.path.join(root, fn))
    
    successes = 0
    failures = 0
    all_failed_records = []
    
    for path in tqdm(json_files, desc=f"Uploading ({mode})", unit="file"):
        ok, results, failed_records = process_file(path, mode, data_type, session, error_log, log_dir)
        if ok:
            successes += 1
        else:
            failures += 1
        all_failed_records.extend(failed_records)
    
    # Save failed records if any
    if all_failed_records:
        failed_path = save_failed_records(all_failed_records, folder_path, data_type, mode)
        if failed_path:
            print(f"\n⚠️ {len(all_failed_records)} failed records saved to: {failed_path}")
    
    return successes, failures, len(json_files)

def process_single_file(path, mode, data_type, session, error_log, log_dir):
    ok, results, failed_records = process_file(path, mode, data_type, session, error_log, log_dir)
    
    # Save failed records if any
    if failed_records:
        failed_path = save_failed_records(failed_records, path, data_type, mode)
        if failed_path:
            print(f"\n⚠️ {len(failed_records)} failed records saved to: {failed_path}")
    
    return ok, results


if __name__ == "__main__":

    parser = argparse.ArgumentParser(description="Upload matched/unmatched JSONs to Pure")
    parser.add_argument("--folder", help="Folder containing JSON files to upload (matched/ or unmatched/)")
    parser.add_argument("--file", help="Single JSON file to upload")
    parser.add_argument("--mode", choices=["create", "update"], help="Mode for single file upload")
    parser.add_argument("--test", type=bool, default=True, help="Get data from UAT (--test True) or Production (--test False)")
    parser.add_argument("--data", default= "research-outputs", choices=["research-outputs", "persons", "external-persons", 
                        "journals", "events", "organizations", "external-organizations", "publishers"], help="What data to get from Pure")
    args = parser.parse_args()

    LOG_DIR = "./matching_test/test_output/logs/uploader_logs"
    os.makedirs(LOG_DIR, exist_ok=True)
    ERROR_LOG_PATH = os.path.join(LOG_DIR, f"uploader_errors_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.log")

    # Load environment variables from .env file
    load_dotenv()
    API_KEY = os.getenv("PURE_API_KEY", "")

    if not API_KEY:
        print("⚠️ WARNING: PURE_API_KEY not found in environment variables.")

    PURE_BASE_URL = "https://galway-staging.elsevierpure.com/ws/api/" if args.test == True else "https://research.universityofgalway.ie/ws/api/"

    HEADERS = {
            "accept": "application/json",
            "api-key": API_KEY
        }

    # let HEADERS be used in each request call
    session = requests.Session()
   
    error_log = []

    if args.folder:
        if not os.path.isdir(args.folder):
            print(f"Folder not found: {args.folder}")
            sys.exit(1)
        successes, failures, total = process_folder(args.folder, args.data, session, error_log, LOG_DIR)
        print(f"Done. {successes}/{total} files succeeded, {failures} failed.")
    elif args.file:
        if not os.path.isfile(args.file):
            print(f"File not found: {args.file}")
            sys.exit(1)
        if not args.mode:
            print("For single file uploads please set --mode create|update")
            sys.exit(1)
        ok, results = process_single_file(args.file, args.mode, args.data, session, error_log, LOG_DIR)
        print("Single file results:")
        print(results)
    else:
        print("Please specify --folder or --file")
        sys.exit(1)

    # write errors to log
    if error_log:
        with open(ERROR_LOG_PATH, 'w', encoding='utf-8') as f:
            for line in error_log:
                f.write(line + "\n")
        print(f"Errors logged to {ERROR_LOG_PATH}")
    else:
        print("No errors logged")
    
    # Print summary of logged records
    created_log = os.path.join(LOG_DIR, "created_records.json")
    updated_log = os.path.join(LOG_DIR, "updated_records.json")

    if args.folder:
        success_count = successes
    else:
        # For single file, count successful results
        success_count = sum(1 for r in results if r[1] is True) if isinstance(results, list) else (1 if ok else 0)
    
    if args.mode == 'create':
        print(f"\n✅ {success_count} records created and logged to {created_log}")
    elif args.mode == 'update':
        print(f"\n✅ {success_count} records updated and logged to {updated_log}")