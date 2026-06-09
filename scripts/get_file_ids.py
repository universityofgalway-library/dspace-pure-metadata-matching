#!/usr/bin/env python3
"""
get_file_ids.py

Match records between a DSpace CSV and a Pure JSON export by Handle URL.
Outputs a CSV with matched record identifiers from both systems, including
file IDs for all matching PDFs (semicolon-separated when multiple).

Output columns:
    dspace_uuid, pure_uuid, pure_id, title,
    dspace_file_id, pure_file_id, pure_file_pure_id, pure_file_name,
    file_match_type, handle

Usage:
    python get_file_ids.py --csv input.csv --json input.json --output output.csv
    python get_file_ids.py --csv input.csv --json input.json --modified-by "john@example.com"
    python get_file_ids.py --csv input.csv --json input.json --modified-after "2025-01-01"
    python get_file_ids.py --csv input.csv --json input.json --modified-by "john@example.com" --modified-after "2025-01-01"
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


def load_csv_records(csv_path: str) -> tuple[dict[str, dict], dict[str, dict]]:
    by_handle: dict[str, dict] = {}
    by_uuid:   dict[str, dict] = {}
    with open(csv_path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            raw_handle = row.get("handle", "").strip()
            if not raw_handle:
                continue
            full_handle = build_handle_url(raw_handle)
            by_handle[full_handle] = row
            dspace_uuid = row.get("uuid", "").strip()
            if dspace_uuid:
                by_uuid[dspace_uuid] = row
    return by_handle, by_uuid


def extract_dspace_uuid_from_json_record(record: dict) -> str | None:
    for identifier in record.get("identifiers", []):
        if identifier.get("idSource") == "DSpace":
            value = identifier.get("value", "").strip()
            if value:
                return value
    return None


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
    pure_file_names: list[str],
) -> tuple[set[int], set[int]]:
    """
    Attempt to pair each DSpace pdf_handle_path with a Pure FileElectronicVersion
    by comparing normalized base filenames.

    Returns a pair of index sets:
        matched_dspace_indices  — indices into dspace_pdf_paths that found a Pure match
        matched_pure_indices    — indices into pure_file_names that were matched

    The caller is responsible for assembling output columns from the original lists
    using these index sets. Unmatched entries from either side are preserved by the
    caller so that no file information is ever silently dropped.
    """
    from urllib.parse import unquote

    def base_name(path: str) -> str:
        return pure_normalize_filename(unquote(path.rstrip("/").split("/")[-1]))

    # Build a lookup: normalized Pure filename -> Pure index
    norm_pure: dict[str, int] = {}
    for i, fname in enumerate(pure_file_names):
        norm_pure[pure_normalize_filename(fname)] = i

    matched_dspace_indices: set[int] = set()
    matched_pure_indices:   set[int] = set()

    for d_idx, dpath in enumerate(dspace_pdf_paths):
        p_idx = norm_pure.get(base_name(dpath))
        if p_idx is not None:
            matched_dspace_indices.add(d_idx)
            matched_pure_indices.add(p_idx)

    return matched_dspace_indices, matched_pure_indices


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


def classify_file_match(
    dspace_pdf_paths: list[str],
    pure_fids: list[str],
    matched_dspace_indices: set[int],
) -> str:
    """
    Derive the file_match_type label for a record.

    Categories (evaluated in order):
      "full_match"            — every DSpace PDF paired with a Pure file AND no Pure
                          files are unmatched (counts on both sides are equal and
                          all matched).
      "partial_match"         — at least one DSpace PDF matched AND at least one did not.
      "file_name_mismatch"   — both sides have files but zero filenames matched.
      "dspace_only_pdf" — DSpace has files, Pure has none.
      "pure_only_pdf"   — Pure has files, DSpace has none.
      "no_pdf"          — neither side has files (no label needed).
    """
    has_dspace = bool(dspace_pdf_paths)
    has_pure   = bool(pure_fids)

    if not has_dspace and not has_pure:
        return ""

    if has_dspace and not has_pure:
        return "dspace_only_pdf"

    if has_pure and not has_dspace:
        return "pure_only_pdf"

    # Both sides have files — examine match coverage
    n_dspace_matched = len(matched_dspace_indices)

    if n_dspace_matched == 0:
        return "file_name_mismatch"

    if n_dspace_matched == len(dspace_pdf_paths):
        return "full_match"

    return "partial_match"


def match_records(
    csv_by_handle: dict[str, dict],
    csv_by_uuid:   dict[str, dict],
    json_records: list[dict],
    pdf_filter: str = "all",
) -> tuple[list[dict], int]:
    """
    Match JSON records against CSV records by DSpace UUID first, falling back
    to Handle URL if no UUID match is found.
    Collects file IDs from both DSpace (pdf_handle_paths) and Pure
    (FileElectronicVersions). Multiple values are joined with '; '.

    All DSpace paths and all Pure files are always written to the output row
    regardless of whether their filenames matched, so no file information is
    silently dropped.

    pdf_filter controls which matched records are included:
      'all'                — include every matched record (default)
      'full_match'         — only records where every DSpace PDF has a matching Pure file
      'partial_match'      — only records where at least one DSpace PDF matched
                             AND at least one did not
      'file_name_mismatch' — both sides have files but no filenames matched
      'dspace_only_pdf'    — DSpace has files, Pure has none
      'pure_only_pdf'      — Pure has files, DSpace has none
      'no_pdf'             — only records where neither DSpace nor Pure have any files

    Returns (output_rows, skipped_count).
    """
    output_rows = []
    skipped = 0

    for json_rec in json_records:
        # --- Priority 1: match by DSpace UUID ---
        csv_rec    = None
        handle_url = None

        dspace_uuid_from_pure = extract_dspace_uuid_from_json_record(json_rec)
        if dspace_uuid_from_pure and dspace_uuid_from_pure in csv_by_uuid:
            csv_rec = csv_by_uuid[dspace_uuid_from_pure]
            raw_handle = csv_rec.get("handle", "").strip()
            handle_url = build_handle_url(raw_handle) if raw_handle else ""

        # --- Priority 2: fall back to Handle ---
        if csv_rec is None:
            for url in extract_handles_from_json_record(json_rec):
                if url in csv_by_handle:
                    csv_rec    = csv_by_handle[url]
                    handle_url = url
                    break

        if csv_rec is None:
            continue

        # --- Core identifiers ---
        dspace_uuid = csv_rec.get("uuid", "").strip()
        pure_uuid   = json_rec.get("uuid", "")
        pure_id     = str(json_rec.get("pureId", ""))
        title_obj   = json_rec.get("title", {})
        title       = (
            title_obj.get("value", "").strip()
            if isinstance(title_obj, dict) else ""
        )

        # --- DSpace file paths (all of them, unconditionally) ---
        pdf_paths_raw = csv_rec.get("pdf_handle_paths", "").strip()
        dspace_pdf_paths = (
            [p.strip() for p in pdf_paths_raw.split(";") if p.strip()]
            if pdf_paths_raw else []
        )

        # --- Pure file metadata (all of them, unconditionally) ---
        pure_fids, pure_fpids, pure_fnames = extract_file_ids_from_pure_record(json_rec)

        # --- Attempt filename-based pairing ---
        if dspace_pdf_paths and pure_fids:
            matched_dspace_idx, _ = match_dspace_to_pure_files(
                dspace_pdf_paths, pure_fnames
            )
        else:
            matched_dspace_idx = set()

        # --- Classify ---
        file_match_type = classify_file_match(
            dspace_pdf_paths, pure_fids, matched_dspace_idx
        )

        # --- Apply PDF filter ---
        if pdf_filter != "all":
            if pdf_filter == "no_pdf":
                # Include only records where neither side has files
                if file_match_type != "":
                    continue
            elif file_match_type != pdf_filter:
                continue

        # --- Build output row — always emit ALL paths and ALL Pure files ---
        row = {
            "dspace_uuid":       dspace_uuid,
            "pure_uuid":         pure_uuid,
            "pure_id":           pure_id,
            "title":             title,
            "dspace_file_id":    "; ".join(dspace_pdf_paths),
            "pure_file_id":      "; ".join(pure_fids),
            "pure_file_pure_id": "; ".join(pure_fpids),
            "pure_file_name":    "; ".join(pure_fnames),
            "file_match_type":   file_match_type,
            "handle":            handle_url,
            # Internal field used for duplicate scoring; stripped before CSV output.
            "_modified_date":    json_rec.get("modifiedDate", ""),
        }

        # Require at minimum the four core identifiers to be non-empty
        core_fields = ("dspace_uuid", "pure_uuid", "pure_id", "handle")
        if all(str(row[f]).strip() for f in core_fields):
            output_rows.append(row)
        else:
            skipped += 1

    return output_rows, skipped


def _score_row(row: dict) -> tuple:
    """
    Return a sort key for a candidate row (higher = better).

    Priority (all descending):
      1. Number of DSpace filenames that matched a Pure filename
         (derived from file_match_type and the file lists).
      2. Total number of Pure files.
      3. Tie-breaker: most recently modified (modifiedDate from the Pure record).

    For criterion 1 we re-derive the matched count cheaply from the already-
    computed file_match_type and the semicolon-separated file lists rather than
    re-running the full matching logic.
    """
    fmt = row.get("file_match_type", "")
    dspace_files  = [f for f in row.get("dspace_file_id",  "").split(";") if f.strip()]
    pure_files    = [f for f in row.get("pure_file_id",    "").split(";") if f.strip()]
    n_pure        = len(pure_files)
    n_dspace      = len(dspace_files)

    if fmt == "full_match":
        n_matched = n_dspace
    elif fmt == "partial_match":
        n_matched = max(1, n_dspace - 1)
    else:
        n_matched = 0

    try:
        modified_dt = parse_iso_datetime(row.get("_modified_date", ""))
    except (ValueError, TypeError):
        modified_dt = datetime.min.replace(tzinfo=timezone.utc)

    return (n_matched, n_pure, modified_dt)


def resolve_duplicates(
    output_rows: list[dict],
) -> tuple[list[dict], list[dict], dict[str, list[dict]]]:
    """
    Detect rows where the same handle or dspace_uuid maps to more than one
    pure_uuid, select the best candidate for the main output, and collect all
    candidates (winners and losers alike) for the duplicates file.

    Selection criteria (descending priority):
      1. Most DSpace filenames that matched a Pure filename.
      2. Most Pure files in total.
      3. Most recently modified Pure record (tie-breaker for determinism).

    Returns:
        deduplicated_rows  — one row per unique (handle, dspace_uuid) pair
        duplicate_rows     — every row involved in a collision (including winner),
                             with an extra 'duplicate_key' column indicating which
                             key triggered the group
        duplicate_groups   — raw dict keyed by collision key, for warning output
    """
    # Group by handle, then separately by dspace_uuid, to catch both collision types.
    by_handle:      dict[str, list[dict]] = defaultdict(list)
    by_dspace_uuid: dict[str, list[dict]] = defaultdict(list)

    for row in output_rows:
        by_handle[row["handle"]].append(row)
        by_dspace_uuid[row["dspace_uuid"]].append(row)

    # Collect all pure_uuids that are part of any collision group so we can
    # replace them with the winner in the final output.
    # Key: pure_uuid → winning row for that collision group.
    replacement: dict[str, dict] = {}   # pure_uuid → winner row to keep
    to_remove:   set[str]        = set() # pure_uuids to drop from final output

    duplicate_groups: dict[str, list[dict]] = {}
    duplicate_rows:   list[dict]            = []

    def _process_group(key: str, rows: list[dict]) -> None:
        if len(rows) < 2:
            return
        duplicate_groups[key] = rows
        winner = max(rows, key=_score_row)
        for row in rows:
            annotated = dict(row, duplicate_key=key)
            duplicate_rows.append(annotated)
            if row["pure_uuid"] != winner["pure_uuid"]:
                to_remove.add(row["pure_uuid"])
            else:
                # Mark winner so we keep exactly one copy if it appears in
                # multiple collision groups (e.g. same row flagged by both
                # handle AND dspace_uuid).
                replacement[row["pure_uuid"]] = winner

    for key, rows in by_handle.items():
        _process_group(key, rows)
    for key, rows in by_dspace_uuid.items():
        _process_group(key, rows)

    # Rebuild output: drop losers, keep one copy of each winner.
    seen_winners: set[str] = set()
    deduplicated: list[dict] = []
    for row in output_rows:
        uid = row["pure_uuid"]
        if uid in to_remove:
            continue
        if uid in seen_winners:
            continue    # winner already emitted (dedup across both group passes)
        seen_winners.add(uid)
        deduplicated.append(row)

    # Deduplicate the duplicate_rows list to one entry per pure_uuid.
    # A row can be flagged by both its handle group and its dspace_uuid group;
    # we keep only the handle-keyed entry (more meaningful identifier), falling
    # back to the dspace_uuid-keyed entry if no handle entry exists.
    handle_keyed:    dict[str, dict] = {}   # pure_uuid → handle-keyed entry
    fallback_keyed:  dict[str, dict] = {}   # pure_uuid → first other entry seen

    for row in duplicate_rows:
        uid = row["pure_uuid"]
        if row["duplicate_key"].startswith("http"):
            handle_keyed[uid] = row
        else:
            if uid not in fallback_keyed:
                fallback_keyed[uid] = row

    deduped_dup_rows: list[dict] = []
    seen: set[str] = set()
    for row in duplicate_rows:
        uid = row["pure_uuid"]
        if uid in seen:
            continue
        seen.add(uid)
        # Prefer the handle-keyed entry; fall back to whatever we have
        canonical = handle_keyed.get(uid, fallback_keyed.get(uid, row))
        deduped_dup_rows.append(canonical)

    return deduplicated, deduped_dup_rows, duplicate_groups


def write_output(output_rows: list[dict], output_path: str) -> None:
    """Write matched records to a CSV file."""
    fieldnames = [
        "dspace_uuid", "pure_uuid", "pure_id", "title",
        "dspace_file_id", "pure_file_id", "pure_file_pure_id", "pure_file_name",
        "file_match_type", "handle",
    ]
    with open(output_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in output_rows:
            writer.writerow({k: row[k] for k in fieldnames})


def write_duplicates_output(duplicate_rows: list[dict], output_path: str) -> None:
    """
    Write all rows involved in duplicate collisions to a separate CSV.
    Identical columns to the main output plus a leading 'duplicate_key' column
    that shows which handle or dspace_uuid triggered the group.
    """
    fieldnames = [
        "duplicate_key",
        "dspace_uuid", "pure_uuid", "pure_id", "title",
        "dspace_file_id", "pure_file_id", "pure_file_pure_id", "pure_file_name",
        "file_match_type", "handle",
    ]
    with open(output_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in duplicate_rows:
            writer.writerow({k: row[k] for k in fieldnames})


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
        help=(
            "Base path for the main output CSV (default: matched_records.csv). "
            "Today's date (YYYY-MM-DD) is appended before the extension automatically. "
            "The duplicates file is written alongside it as duplicate_<name>."
        ),
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
        choices=["all", "full_match", "partial_match", "file_name_mismatch", "dspace_only_pdf", "pure_only_pdf", "no_pdf"],
        metavar="MODE",
        help=(
            "Filter matched records by PDF match type: "
            "'all' includes every match (default); "
            "'full_match' includes only records where every DSpace PDF matched a Pure file; "
            "'partial_match' includes only records where at least one DSpace PDF matched "
            "AND at least one did not; "
            "'file_name_mismatch' includes only records where both sides have files but "
            "no filenames matched; "
            "'dspace_only_pdf' includes only records where DSpace has files but Pure does not; "
            "'pure_only_pdf' includes only records where Pure has files but DSpace does not; "
            "'no_pdf' includes only records where neither DSpace nor Pure have any files."
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


def _dated_path(path: str, date_str: str) -> str:
    """
    Insert a YYYY-MM-DD suffix before the file extension.
    e.g. "temp_test_matched_records.csv" → "matched_records_2026-06-09.csv"
    """
    if "." in path:
        stem, ext = path.rsplit(".", 1)
        return f"{stem}_{date_str}.{ext}"
    return f"{path}_{date_str}"


def main() -> None:
    args = parse_args()

    today = datetime.now().strftime("%Y-%m-%d")

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
    csv_by_handle, csv_by_uuid = load_csv_records(args.csv)
    print(f"  → {len(csv_by_handle)} records with Handles loaded.")

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
    output_rows, skipped = match_records(csv_by_handle, csv_by_uuid, json_records, pdf_filter=args.pdf_filter)
    print(f"  → {len(output_rows)} complete matches found.")
    if skipped:
        print(f"  → {skipped} matched records skipped due to missing core fields.")

    # --- Duplicate resolution ---
    output_rows, duplicate_rows, duplicate_groups = resolve_duplicates(output_rows)

    if duplicate_groups:
        print(f"  → WARNING: {len(duplicate_groups)} duplicate keys detected "
              f"(same handle or dspace_uuid maps to multiple pure_uuids).")
        for key, rows in duplicate_groups.items():
            winner = max(rows, key=_score_row)
            losers = [r for r in rows if r["pure_uuid"] != winner["pure_uuid"]]
            loser_uuids = ", ".join(r["pure_uuid"] for r in losers)
            print(f"     • {key}")
            print(f"       kept:    {winner['pure_uuid']}  (score {_score_row(winner)})")
            print(f"       dropped: [{loser_uuids}]")
    else:
        print("  → No duplicates found.")

    # --- Write outputs with pdf_filter prefix and date suffix ---
    base_name  = args.output.rsplit("/", 1)[-1]           # strip any directory
    base_dir   = args.output[: len(args.output) - len(base_name)]  # may be ""
    prefix     = f"{args.pdf_filter}_"

    main_output = _dated_path(f"{base_dir}{prefix}{base_name}", today)
    dup_output  = _dated_path(f"{base_dir}{prefix}duplicate_{base_name}", today)

    write_output(output_rows, main_output)
    print(f"Output written to: {main_output}")

    if duplicate_rows:
        write_duplicates_output(duplicate_rows, dup_output)
        print(f"Duplicates written to: {dup_output}")


if __name__ == "__main__":
    main()