#!/usr/bin/env python3
"""
match_handles.py

Match records between a DSpace CSV and a Pure JSON export by Handle URL.
Outputs a CSV with matched record identifiers from both systems.

Usage:
    python match_handles.py --csv input.csv --json input.json --output output.csv
    python match_handles.py --csv input.csv --json input.json --modified-by "john@example.com"
    python match_handles.py --csv input.csv --json input.json --modified-after "2025-01-01"
    python match_handles.py --csv input.csv --json input.json --modified-by "john@example.com" --modified-after "2025-01-01"
"""

import argparse
import csv
import json
import sys
from datetime import datetime, timezone


HANDLE_BASE_URL = "http://hdl.handle.net/"


def build_handle_url(raw_handle: str) -> str:
    """Prefix a bare handle (e.g. '10379/6474') with the HDL base URL."""
    raw_handle = raw_handle.strip()
    if raw_handle.startswith("http"):
        return raw_handle
    return HANDLE_BASE_URL + raw_handle


def load_csv_records(csv_path: str) -> dict[str, dict]:
    """
    Load the DSpace CSV and return a dict keyed by full Handle URL.
    Only records that have a non-empty 'handle' column are included.
    """
    records = {}
    # utf-8-sig automatically strips a leading BOM if present (common in
    # CSV exports from Excel and some repository tools), falling back to
    # plain UTF-8 otherwise.
    with open(csv_path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            raw_handle = row.get("handle", "").strip()
            if not raw_handle:
                continue
            full_handle = build_handle_url(raw_handle)
            records[full_handle] = row
    return records


def parse_iso_datetime(dt_string: str) -> datetime:
    """Parse an ISO-8601 datetime string into a timezone-aware datetime object."""
    # Python < 3.11 doesn't handle the trailing 'Z' in fromisoformat
    dt_string = dt_string.rstrip("Z") + "+00:00"
    return datetime.fromisoformat(dt_string)


def extract_handles_from_json_record(record: dict) -> list[str]:
    """Return all Handle URLs found in the 'links' list of a JSON record."""
    handles = []
    for link in record.get("links", []):
        if link.get("alias") == "Handle":
            url = link.get("url", "").strip()
            if url:
                handles.append(url)
    return handles


def filter_json_records(
    records: list[dict],
    modified_by: str | None,
    modified_after: datetime | None,
) -> list[dict]:
    """Apply optional filters to the JSON record list."""
    filtered = []
    for rec in records:
        if modified_by:
            if rec.get("modifiedBy", "") != modified_by:
                continue
        if modified_after:
            modified_date_str = rec.get("modifiedDate", "")
            if not modified_date_str:
                continue
            try:
                modified_date = parse_iso_datetime(modified_date_str)
            except ValueError:
                continue
            if modified_date <= modified_after:
                continue
        filtered.append(rec)
    return filtered


def match_records(
    csv_records: dict[str, dict],
    json_records: list[dict],
) -> list[dict]:
    """
    Match JSON records against CSV records by Handle URL.
    Returns a tuple of (output_rows, skipped_count).
    """
    output_rows = []
    skipped = 0

    for json_rec in json_records:
        handles = extract_handles_from_json_record(json_rec)
        for handle_url in handles:
            if handle_url in csv_records:
                csv_rec = csv_records[handle_url]
                row = {
                    "dspace_uuid": csv_rec.get("uuid", ""),
                    "handle": handle_url,
                    "pure_uuid": json_rec.get("uuid", ""),
                    "pure_id": json_rec.get("pureId", ""),
                }
                # Only include records where every output field is populated
                if all(str(v).strip() for v in row.values()):
                    output_rows.append(row)
                else:
                    skipped += 1
                # A single JSON record could theoretically have multiple Handles;
                # stop after the first match to avoid duplicate output rows.
                break

    return output_rows, skipped


def find_duplicates(output_rows: list[dict]) -> dict[str, list[dict]]:
    """
    Detect rows where the same handle or dspace_uuid maps to more than one
    pure_uuid. Returns a dict keyed by the duplicate key, with a list of the
    conflicting rows as the value.
    """
    from collections import defaultdict

    by_handle: dict[str, list[dict]] = defaultdict(list)
    by_dspace_uuid: dict[str, list[dict]] = defaultdict(list)

    for row in output_rows:
        by_handle[row["handle"]].append(row)
        by_dspace_uuid[row["dspace_uuid"]].append(row)

    duplicates: dict[str, list[dict]] = {}
    for key, rows in {**by_handle, **by_dspace_uuid}.items():
        if len(rows) > 1:
            duplicates[key] = rows

    return duplicates


def write_output(output_rows: list[dict], output_path: str) -> None:
    """Write matched records to a CSV file, with an extra 'duplicate' flag column."""
    # Pre-compute which handles/dspace_uuids appear more than once
    from collections import Counter
    handle_counts = Counter(r["handle"] for r in output_rows)
    uuid_counts = Counter(r["dspace_uuid"] for r in output_rows)

    fieldnames = ["dspace_uuid", "handle", "pure_uuid", "pure_id", "duplicate_flag"]
    with open(output_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in output_rows:
            reasons = []
            if handle_counts[row["handle"]] > 1:
                reasons.append("duplicate_handle")
            if uuid_counts[row["dspace_uuid"]] > 1:
                reasons.append("duplicate_dspace_uuid")
            writer.writerow({**row, "duplicate_flag": "|".join(reasons)})


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Match DSpace CSV and Pure JSON records by Handle URL.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--csv",
        required=True,
        metavar="CSV_FILE",
        help="Path to the DSpace CSV input file.",
    )
    parser.add_argument(
        "--json",
        required=True,
        metavar="JSON_FILE",
        help="Path to the Pure JSON input file.",
    )
    parser.add_argument(
        "--output",
        default="matched_records.csv",
        metavar="OUTPUT_FILE",
        help="Path for the output CSV file (default: matched_records.csv).",
    )
    parser.add_argument(
        "--modified-by",
        default=None,
        metavar="USER",
        help=(
            "Only include JSON records whose 'modifiedBy' field equals USER "
            "(e.g. 'john@example.com'). Filter is off by default."
        ),
    )
    parser.add_argument(
        "--modified-after",
        default=None,
        metavar="YYYY-MM-DD",
        help=(
            "Only include JSON records modified strictly after this date "
            "(e.g. '2025-01-01'). Filter is off by default."
        ),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    # Parse --modified-after into an aware datetime if provided
    modified_after_dt: datetime | None = None
    if args.modified_after:
        try:
            modified_after_dt = datetime.strptime(
                args.modified_after, "%Y-%m-%d"
            ).replace(tzinfo=timezone.utc)
        except ValueError:
            print(
                f"Error: --modified-after value '{args.modified_after}' "
                "is not in YYYY-MM-DD format.",
                file=sys.stderr,
            )
            sys.exit(1)

    # Load inputs
    print(f"Loading CSV from: {args.csv}")
    csv_records = load_csv_records(args.csv)
    print(f"  → {len(csv_records)} records with Handles loaded.")

    print(f"Loading JSON from: {args.json}")
    with open(args.json, encoding="utf-8") as fh:
        json_records: list[dict] = json.load(fh)
    print(f"  → {len(json_records)} records loaded.")

    # Apply filters
    if args.modified_by or modified_after_dt:
        json_records = filter_json_records(
            json_records,
            modified_by=args.modified_by,
            modified_after=modified_after_dt,
        )
        print(f"  → {len(json_records)} records after filtering.")

    # Match
    print("Matching records by Handle…")
    output_rows, skipped = match_records(csv_records, json_records)
    print(f"  → {len(output_rows)} complete matches found.")
    if skipped:
        print(f"  → {skipped} matched records skipped due to one or more empty fields.")

    # Duplicate detection
    duplicates = find_duplicates(output_rows)
    if duplicates:
        print(f"  → WARNING: {len(duplicates)} duplicate keys detected "
              f"(same handle or dspace_uuid maps to multiple pure_uuids).")
        for key, rows in duplicates.items():
            pure_uuids = ", ".join(r["pure_uuid"] for r in rows)
            print(f"     • {key}  →  [{pure_uuids}]")
    else:
        print("  → No duplicates found.")

    # Write output (duplicate_flag column included in every row)
    write_output(output_rows, args.output)
    print(f"Output written to: {args.output}")
    if duplicates:
        print("  → Rows with duplicates are flagged in the 'duplicate_flag' column "
              "(values: duplicate_handle, duplicate_dspace_uuid, or both).")


if __name__ == "__main__":
    main()