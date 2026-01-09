import json
from datetime import date

# System fields to exclude
SYSTEM_FIELDS_TO_EXCLUDE = {
    "createdBy",
    "createdDate",
    "modifiedBy",
    "modifiedDate",
    "prettyUrlIdentifiers",
    "version",
    "keywordContainers",
    "keywordGroups"
}

TODAY = date.today().isoformat()

LOG_JSON_PATH = "./matching_test/test_output/logs/uploader_logs/updated_records.json"
SOURCE_JSON_PATH = "./matching_test/research_outputs/research_outputs_2025-11-20_all.json"
BACKUP_JSON_PATH = f"./matching_test/test_output/matched_records_before_updates_{TODAY}.json"

def strip_system_fields(record: dict) -> dict:
    """Remove system fields from a record (top-level only)."""
    return {
        k: v
        for k, v in record.items()
        if k not in SYSTEM_FIELDS_TO_EXCLUDE
    }


def copy_records_by_uuid(LOG_JSON_PATH, SOURCE_JSON_PATH, BACKUP_JSON_PATH):
    """
    Copy records from JSON2 whose UUID exists in JSON1 into JSON3,
    excluding system fields.
    """

    # Load JSON1
    with open(LOG_JSON_PATH, "r", encoding="utf-8") as f:
        json1_records = json.load(f)

    # Extract UUIDs from JSON1
    uuid_set = {
        record.get("uuid")
        for record in json1_records
        if isinstance(record, dict) and record.get("uuid")
    }

    if not uuid_set:
        raise ValueError("No UUIDs found in JSON1")

    # Load JSON2
    with open(SOURCE_JSON_PATH, "r", encoding="utf-8") as f:
        json2_records = json.load(f)

    # Filter and clean records
    matched_records = [
        strip_system_fields(record)
        for record in json2_records
        if isinstance(record, dict) and record.get("uuid") in uuid_set
    ]

    # Write JSON3
    with open(BACKUP_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(matched_records, f, indent=2, ensure_ascii=False)

    print(f"Copied {len(matched_records)} records to {BACKUP_JSON_PATH}")


if __name__ == "__main__":
    copy_records_by_uuid(LOG_JSON_PATH, SOURCE_JSON_PATH, BACKUP_JSON_PATH)