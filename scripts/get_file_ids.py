#!/usr/bin/env python3
"""
match_handles.py

Match records between a DSpace CSV and a Pure JSON export by Handle URL.
Outputs a CSV with matched record identifiers from both systems, including
file IDs for all matching PDFs (semicolon-separated when multiple).

Output columns:
    dspace_uuid, pure_uuid, pure_id, title,
    dspace_file_id, pure_file_id, pure_file_pure_id, pure_file_name, handle

Usage:
    python match_handles.py --csv input.csv --json input.json --output output.csv
    python match_handles.py --csv input.csv --json input.json --modified-by "john@example.com"
    python match_handles.py --csv input.csv --json input.json --modified-after "2025-01-01"
    python match_handles.py --csv input.csv --json input.json --modified-by "john@example.com" --modified-after "2025-01-01"
"""

import argparse
import csv
import json
import re
import sys
from collections import defaultdict
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


def pure_normalize_filename(name: str) -> str:
    """
    Normalize a filename the same way Pure does when storing uploaded files.
    Pure replaces characters that are not alphanumeric, hyphen, underscore,
    dot, or space with underscores.
    """
    return re.sub(r'[^\w.\- ]', '_', name)


def extract_file_ids_from_pure_record(pure_record: dict) -> tuple[list, list, list]:
    """
    Extract fileId, pureId, and fileName for every FileElectronicVersion
    in the Pure record.
    Returns three parallel lists: (file_ids, file_pure_ids, file_names).
    """
    file_ids      = []
    file_pure_ids = []
    file_names    = []
    for ev in pure_record.get("electronicVersions", []):
        if ev.get("typeDiscriminator") != "FileElectronicVersion":
            continue
        file_block = ev.get("file", {})
        fid  = file_block.get("fileId", "")
        fpid = str(file_block.get("pureId", ""))
        fname = file_block.get("fileName", "")
        file_ids.append(fid)
        file_pure_ids.append(fpid)
        file_names.append(fname)
    return file_ids, file_pure_ids, file_names


def match_dspace_to_pure_files(
    dspace_pdf_paths: list[str],
    pure_file_ids: list[str],
    pure_file_pure_ids: list[str],
    pure_file_names: list[str],
) -> tuple[list, list, list, list]:
    """
    For each DSpace pdf_handle_path, try to find the corresponding Pure file
    by comparing the decoded base filename (after Pure-style normalization).

    Returns four parallel lists aligned with dspace_pdf_paths:
        matched_dspace_paths, matched_pure_file_ids,
        matched_pure_file_pure_ids, matched_pure_file_names

    Only paths that have a matching Pure file are included in the per-path
    pairing. If NO DSpace paths match any Pure file, falls back to returning
    all DSpace paths paired with all Pure files (best-effort fallback), so
    that file info is never silently dropped.
    """
    from urllib.parse import unquote

    def base_name(path: str) -> str:
        return pure_normalize_filename(unquote(path.rstrip("/").split("/")[-1]))

    matched_dspace  = []
    matched_fids    = []
    matched_fpids   = []
    matched_fnames  = []

    # Build a lookup from normalized Pure filename → index
    norm_pure = {pure_normalize_filename(fn): i for i, fn in enumerate(pure_file_names)}

    for dpath in dspace_pdf_paths:
        norm_dspace = base_name(dpath)
        idx = norm_pure.get(norm_dspace)
        if idx is not None:
            matched_dspace.append(dpath)
            matched_fids.append(pure_file_ids[idx])
            matched_fpids.append(pure_file_pure_ids[idx])
            matched_fnames.append(pure_file_names[idx])

    # If no DSpace path matched any Pure file, fall back to returning all
    # DSpace paths alongside all Pure files. This ensures file info is never
    # silently lost due to minor filename discrepancies (e.g. double vs single
    # underscore: JBDS_MentalHealth__31082017.pdf vs JBDS_MentalHealth_31082017.pdf).
    if not matched_dspace:
        return dspace_pdf_paths, pure_file_ids, pure_file_pure_ids, pure_file_names

    return matched_dspace, matched_fids, matched_fpids, matched_fnames


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
    pdf_filter: str = "all",
) -> tuple[list[dict], int]:
    """
    Match JSON records against CSV records by Handle URL.
    Collects file IDs from both DSpace (pdf_handle_paths) and Pure
    (FileElectronicVersions). Multiple values are joined with '; '.

    pdf_filter controls which matched records are included:
      'all'      — include every matched record (default)
      'with'     — only records where every DSpace PDF has a matching Pure file
                   (DSpace count may be <= Pure count, but no DSpace file is unmatched)
      'without'  — only records with no DSpace pdf_handle_paths
      'partial'  — only records where at least one DSpace PDF has no matching
                   Pure file (i.e. len(matched) < len(dspace_pdf_paths))

    Returns (output_rows, skipped_count).
    """
    output_rows = []
    skipped = 0

    for json_rec in json_records:
        handles = extract_handles_from_json_record(json_rec)
        for handle_url in handles:
            if handle_url not in csv_records:
                continue

            csv_rec = csv_records[handle_url]

            # --- Core identifiers ---
            dspace_uuid = csv_rec.get("uuid", "").strip()
            pure_uuid   = json_rec.get("uuid", "")
            pure_id     = str(json_rec.get("pureId", ""))
            title_obj   = json_rec.get("title", {})
            title       = (
                title_obj.get("value", "").strip()
                if isinstance(title_obj, dict) else ""
            )

            # --- DSpace file paths ---
            pdf_paths_raw = csv_rec.get("pdf_handle_paths", "").strip()
            dspace_pdf_paths = (
                [p.strip() for p in pdf_paths_raw.split(";") if p.strip()]
                if pdf_paths_raw else []
            )

            has_pdfs = bool(dspace_pdf_paths)

            # --- Pure file IDs ---
            pure_fids, pure_fpids, pure_fnames = extract_file_ids_from_pure_record(json_rec)

            # --- Match DSpace paths to Pure files ---
            if dspace_pdf_paths and pure_fids:
                matched_dspace, matched_fids, matched_fpids, matched_fnames = (
                    match_dspace_to_pure_files(
                        dspace_pdf_paths, pure_fids, pure_fpids, pure_fnames
                    )
                )
            elif not dspace_pdf_paths:
                # No DSpace PDFs — carry through whatever Pure files exist (may be empty)
                matched_dspace = []
                matched_fids   = pure_fids
                matched_fpids  = pure_fpids
                matched_fnames = pure_fnames
            else:
                # DSpace has PDFs but Pure record has no FileElectronicVersions at all —
                # nothing is matched; do not populate matched_dspace
                matched_dspace = dspace_pdf_paths
                matched_fids   = []
                matched_fpids  = []
                matched_fnames = []

            # --- Classify the PDF match state ---
            # 'partial' means: at least one DSpace PDF matched a Pure file,
            # AND at least one DSpace PDF did not — i.e. some matched, some didn't.
            # Records where NO DSpace PDF matched any Pure file are not partial.
            if has_pdfs:
                some_matched = len(matched_dspace) > 0
                some_unmatched = len(matched_dspace) < len(dspace_pdf_paths)
                is_partial = some_matched and some_unmatched
            else:
                is_partial = False

            # --- Apply PDF filter ---
            if pdf_filter == "without" and has_pdfs:
                continue
            if pdf_filter == "without" and not has_pdfs:
                pass  # include
            elif pdf_filter == "with" and (not has_pdfs or is_partial):
                continue
            elif pdf_filter == "partial" and not is_partial:
                continue
            elif pdf_filter == "all":
                pass  # include everything

            # --- For 'without' and 'partial', keep only the matched subset.
            #     For 'without', file fields will be empty anyway.
            #     For 'with', use the full matched lists (all DSpace paths matched). ---
            row = {
                "dspace_uuid":       dspace_uuid,
                "pure_uuid":         pure_uuid,
                "pure_id":           pure_id,
                "title":             title,
                "dspace_file_id":    "; ".join(matched_dspace),
                "pure_file_id":      "; ".join(matched_fids),
                "pure_file_pure_id": "; ".join(matched_fpids),
                "pure_file_name":    "; ".join(matched_fnames),
                "handle":            handle_url,
            }

            # Require at minimum the four core identifiers to be non-empty
            core_fields = ("dspace_uuid", "pure_uuid", "pure_id", "handle")
            if all(str(row[f]).strip() for f in core_fields):
                output_rows.append(row)
            else:
                skipped += 1

            # Stop after the first handle match for this JSON record
            break

    return output_rows, skipped


def find_duplicates(output_rows: list[dict]) -> dict[str, list[dict]]:
    """
    Detect rows where the same handle or dspace_uuid maps to more than one
    pure_uuid. Returns a dict keyed by the duplicate key.
    """
    by_handle:      dict[str, list[dict]] = defaultdict(list)
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
    """Write matched records to a CSV file."""
    fieldnames = [
        "dspace_uuid", "pure_uuid", "pure_id", "title",
        "dspace_file_id", "pure_file_id", "pure_file_pure_id", "pure_file_name",
        "handle",
    ]
    with open(output_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in output_rows:
            writer.writerow(row)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Match DSpace CSV and Pure JSON records by Handle URL, "
            "collecting file IDs from both systems."
        ),
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
            "Only include JSON records whose 'modifiedBy' field equals USER. "
            "Filter is off by default."
        ),
    )
    parser.add_argument(
        "--pdf-filter",
        default="all",
        choices=["all", "with", "without", "partial"],
        metavar="MODE",
        help=(
            "Filter matched records by PDF match state: "
            "'all' includes every match (default); "
            "'with' includes only records where all DSpace PDFs have a matching Pure file; "
            "'without' includes only records with no DSpace pdf_handle_paths; "
            "'partial' includes only records where at least one DSpace PDF "
            "has no corresponding Pure file."
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

    print(f"Loading CSV from: {args.csv}")
    csv_records = load_csv_records(args.csv)
    print(f"  → {len(csv_records)} records with Handles loaded.")

    print(f"Loading JSON from: {args.json}")
    with open(args.json, encoding="utf-8") as fh:
        json_records: list[dict] = json.load(fh)
    print(f"  → {len(json_records)} records loaded.")

    if args.modified_by or modified_after_dt:
        json_records = filter_json_records(
            json_records,
            modified_by=args.modified_by,
            modified_after=modified_after_dt,
        )
        print(f"  → {len(json_records)} records after filtering.")

    print("Matching records by Handle…")
    output_rows, skipped = match_records(csv_records, json_records, pdf_filter=args.pdf_filter)
    print(f"  → {len(output_rows)} complete matches found.")
    if skipped:
        print(f"  → {skipped} matched records skipped due to missing core fields.")

    duplicates = find_duplicates(output_rows)
    if duplicates:
        print(f"  → WARNING: {len(duplicates)} duplicate keys detected "
              f"(same handle or dspace_uuid maps to multiple pure_uuids).")
        for key, rows in duplicates.items():
            pure_uuids = ", ".join(r["pure_uuid"] for r in rows)
            print(f"     • {key}  →  [{pure_uuids}]")
    else:
        print("  → No duplicates found.")

    write_output(output_rows, args.output)
    print(f"Output written to: {args.output}")


if __name__ == "__main__":
    main()