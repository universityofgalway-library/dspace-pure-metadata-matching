#!/usr/bin/env python3
"""
get_file_ids.py

Match records between a DSpace CSV and a Pure JSON export (by DSpace UUID,
falling back to Handle), pair their PDF files by filename, and write the
identifiers from both systems in two formats:

  - one row per matched record (file columns semicolon-separated), and
  - one row per file (each DSpace file on the same row as the Pure file it
    matched; unmatched files on rows of their own).

Record-level output columns:
    dspace_uuid, pure_uuid, pure_id, title,
    dspace_file_id, pure_file_id, pure_file_pure_id, pure_file_name,
    file_match_type, handle

File-level output columns: the same, plus "file_match" (per file).

In the record-level file the file lists are ordered so that matched files
come first and line up by position (the n-th DSpace file matched the n-th
Pure file); then unmatched DSpace files; then unmatched Pure files. The
file-level file states each pairing explicitly and is the one to use for
per-file work.

dspace_file_id format:
    "{handle}:{seq}/{file uuid}:{filename}", e.g.
    "10379/1034:1/3c246f30-8d3a-4077-a013-6ca4ef262a3e:paper_0078.pdf"
    -- the handle comes from the CSV's "handle" column, the sequence and
    filename come from "pdf_handle_paths", and the file UUID is extracted
    from the matching "pdf_links" entry. This mirrors how create_pure_xml.py
    builds DSpace <file id="..."> values. If a file UUID can't be found for
    an entry, the raw pdf_handle_paths value is used instead so no file
    information is silently dropped.

A Pure record's DSpace UUID is read from its identifiers with idSource
"DSpace" -- "PrimaryId" first, then "Id". A record belongs to ONE DSpace
item: a record carrying several distinct DSpace UUIDs uses the first
(PrimaryId before Id) and is reported (multiple_dspace_ids). Every file has
its own ID on both sides (DSpace bitstream UUID; Pure fileId / pureId).

Also written: a duplicates report (several Pure records for one DSpace
item), unmatched reports (DSpace items no Pure record links to; Pure
records whose DSpace UUID / Handle isn't in the CSV) and the
multiple_dspace_ids report.

Usage:
    python get_file_ids.py --csv input.csv --json input.json --output output.csv
    python get_file_ids.py --csv input.csv --json input.json --modified-by "john@example.com"
    python get_file_ids.py --csv input.csv --json input.json --modified-after "2025-01-01"
    python get_file_ids.py --csv input.csv --json input.json --modified-by "john@example.com" --modified-after "2025-01-01"
    python get_file_ids.py --csv input.csv --json input.json --handles "10379/7309"
    python get_file_ids.py --csv input.csv --json input.json --handles "10379/7309,10379/1501,10379/5890"
    python get_file_ids.py --csv input.csv --json input.json --xml-filter "sample_xml.xml"
"""

import argparse
import csv
import json
import os
import re
import sys
import html
import unicodedata
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime, timezone
from urllib.parse import unquote


HANDLE_BASE_URL = "http://hdl.handle.net/"

# Output files are written as UTF-8 with a BOM, so Excel shows accented
# characters correctly (Python and other CSV readers ignore the BOM when
# reading with "utf-8-sig").
OUTPUT_ENCODING = "utf-8-sig"

# Matches a DSpace bitstream/file UUID embedded in a content URL, e.g.
# '.../server/api/core/bitstreams/{uuid}/content' -> {uuid}.
# (Same pattern used by create_pure_xml.py to build DSpace file IDs.)
BITSTREAM_UUID_RE = re.compile(
    r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})"
)

# A Handle, bare ("10379/6474") or as a URL: hdl.handle.net, handle.net or
# www.handle.net hosts, with or without http(s)://, any case, ignoring
# trailing slashes -- the same forms match_records.py and add_pdfs_to_pure.py
# accept.
HANDLE_RE = re.compile(
    r"^(?:(?:https?://)?(?:www\.|hdl\.)?handle\.net/)?(\d+(?:\.\d+)*/\S+?)/*$",
    re.IGNORECASE,
)

# Per-file match statuses (file-level output, "file_match" column).
FILE_MATCHED = "matched"
FILE_MATCHED_FALLBACK = "matched_fallback"
FILE_DSPACE_ONLY = "dspace_only"
FILE_PURE_ONLY = "pure_only"
FILE_NO_FILES = "no_files"


def clean_dspace_filename(filename: str) -> str:
    """
    DSpace-exported filenames can be HTML-entity-encoded (e.g. an accented
    character rendered as "&#769;") and then percent-encoded on top of that,
    so unquote() alone leaves literal "&#769;" text in the filename instead
    of the real character. This decodes any HTML character references, then
    NFKC-normalizes so combining marks merge into the preceding letter and
    compatibility characters (e.g. the "fl" ligature) fold to plain letters.
    """
    if not filename:
        return filename
    unescaped = html.unescape(filename)
    return unicodedata.normalize("NFKC", unescaped)


def _strip_diacritics(name: str) -> str:
    """Decompose accented characters and drop the combining marks, e.g. "é" -> "e"."""
    decomposed = unicodedata.normalize("NFKD", name)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


# A run of 2-6 digits bounded by underscores on both sides -- the shape left
# behind when Pure's own storage-time filename normalization turns a literal,
# un-decoded HTML character reference like "&#769;" into "_769_" (the "&" and
# ";" each become "_", the digits survive as-is since they're word characters).
_ENTITY_DIGIT_RUN_RE = re.compile(r'_\d{2,6}_')


def _strip_entity_digit_runs(name: str) -> str:
    return _ENTITY_DIGIT_RUN_RE.sub("", name)


def _legacy_skeleton(name: str) -> str:
    """
    Aggressive fallback normalization for matching a filename against a
    *legacy* Pure-stored name that predates clean_dspace_filename(): decode
    HTML entities, strip diacritics, collapse any Pure "_NNN_" entity-digit
    remnant, apply Pure's own punctuation normalization, then lowercase.

    A correctly-decoded filename (e.g. "Méliacin.pdf") and a file uploaded
    by the old script before this fix existed (stored by Pure as
    "Me_769_liacin.pdf", since Pure's own normalization turned the literal
    "&#769;" markup into "_769_") reduce to the same skeleton here even
    though clean_dspace_filename() alone can't reconcile them -- the "_769_"
    remnant no longer looks like an HTML entity by the time it reaches this
    script, so there's nothing left for clean_dspace_filename() to decode.
    Used only as a fallback when the direct normalized match fails, to keep
    the extra risk of a same-skeleton coincidence limited to names that
    would otherwise be reported as unmatched anyway.
    """
    step = clean_dspace_filename(name)
    step = _strip_diacritics(step)
    step = _strip_entity_digit_runs(step)
    step = pure_normalize_filename(step)
    return step.lower()


def normalize_handle(value: str) -> str:
    """
    A Handle in canonical form: "http://hdl.handle.net/<handle>", lower case.
    Bare handles and the URL forms in HANDLE_RE are accepted. A value that is
    not recognisable as a Handle is returned stripped, unchanged.
    """
    value = (value or "").strip()
    match = HANDLE_RE.match(value)
    if not match:
        return value
    return HANDLE_BASE_URL + match.group(1).lower()


def is_handle_url(value: str) -> bool:
    """True if value is a Handle URL (contains handle.net and normalizes to a Handle)."""
    if not isinstance(value, str) or "handle.net" not in value.lower():
        return False
    return normalize_handle(value).startswith(HANDLE_BASE_URL)


def build_handle_url(raw_handle: str) -> str:
    """Canonical Handle URL for a bare handle (e.g. '10379/6474') or a Handle URL."""
    return normalize_handle(raw_handle)


def bare_handle_of(handle_url: str) -> str:
    """'http://hdl.handle.net/10379/6474' -> '10379/6474'."""
    if handle_url.startswith(HANDLE_BASE_URL):
        return handle_url[len(HANDLE_BASE_URL):]
    return handle_url


def parse_handles_arg(handles_arg: str | None) -> set[str]:
    """
    Parse a comma-separated --handles string (e.g. '10379/7309' or
    '10379/7309,10379/1501,10379/5890') into a set of normalized Handle URLs.
    """
    if not handles_arg:
        return set()
    return {
        build_handle_url(h)
        for h in handles_arg.split(",")
        if h.strip()
    }


def extract_handles_from_xml_filter(xml_path: str) -> set[str]:
    """
    Extract Handles from a Pure XML import file (--xml-filter) to use as a
    record filter.

    Handles come from the "id" attribute of each top-level record element —
    i.e. the direct children of the document root, such as
    <book id="10379/7309">, <contributionToJournal id="10379/17295">,
    <workingPaper id="10379/1034">, etc. Nested elements like
    <person id="...">, <organisation id="...">, or <file id="..."> use
    unrelated ID schemes (UUIDs, or the "{handle}:{seq}/{uuid}:{filename}"
    DSpace file ID format) and are deliberately not considered — only the
    record-level "id" attribute holds a bare Handle.
    """
    try:
        tree = ET.parse(xml_path)
    except ET.ParseError as exc:
        print(f"Error: could not parse --xml-filter file '{xml_path}': {exc}", file=sys.stderr)
        sys.exit(1)
    except OSError as exc:
        print(f"Error: could not read --xml-filter file '{xml_path}': {exc}", file=sys.stderr)
        sys.exit(1)

    root = tree.getroot()
    handles: set[str] = set()
    for record in root:
        raw_handle = record.attrib.get("id", "").strip()
        if raw_handle:
            handles.add(build_handle_url(raw_handle))
    return handles


def load_csv_records(csv_path: str) -> tuple[dict[str, dict], dict[str, dict], list[dict], int]:
    """
    Read the DSpace CSV.

    Returns (by_handle, by_uuid, rows, handle_collisions):
      by_handle  canonical Handle URL -> row
      by_uuid    DSpace UUID (lower case) -> row
      rows       every row that has a handle, in file order
      handle_collisions  number of rows whose handle was already used by an
                 earlier row (the later row wins in by_handle)
    Rows without a handle value are ignored.
    """
    by_handle: dict[str, dict] = {}
    by_uuid:   dict[str, dict] = {}
    rows: list[dict] = []
    collisions = 0
    with open(csv_path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            raw_handle = (row.get("handle") or "").strip()
            if not raw_handle:
                continue
            full_handle = build_handle_url(raw_handle)
            if full_handle in by_handle:
                collisions += 1
            by_handle[full_handle] = row
            rows.append(row)
            dspace_uuid = (row.get("uuid") or "").strip().lower()
            if dspace_uuid:
                by_uuid[dspace_uuid] = row
    return by_handle, by_uuid, rows, collisions


# Identifier types that carry a record's DSpace UUID (idSource "DSpace"):
# "PrimaryId" (written by match_records.py) and "Id" (Pure's import). PrimaryId
# is checked first. Any other identifier type with idSource "DSpace" is still
# read, after these two.
DSPACE_ID_TYPES = ("PrimaryId", "Id")


def extract_dspace_uuids_from_json_record(record: dict) -> list[str]:
    """
    All distinct DSpace UUIDs of a Pure record (identifiers with idSource
    "DSpace", any case), lower case: those in "PrimaryId" identifiers first,
    then "Id", then any other identifier type. A record should carry only
    one (see record_dspace_uuid); the same UUID in both a PrimaryId and an
    Id identifier counts once. (File UUIDs are not identifiers: each DSpace
    file's own bitstream UUID is read from pdf_links, see
    build_dspace_file_ids.)
    """
    found: dict[str, list[str]] = defaultdict(list)
    for identifier in record.get("identifiers") or []:
        if not isinstance(identifier, dict):
            continue
        if str(identifier.get("idSource") or "").strip().lower() != "dspace":
            continue
        value = str(identifier.get("value") or "").strip().lower()
        if value:
            found[str(identifier.get("typeDiscriminator") or "")].append(value)
    order = list(DSPACE_ID_TYPES) + [t for t in found if t not in DSPACE_ID_TYPES]
    uuids: list[str] = []
    for id_type in order:
        for value in found.get(id_type, []):
            if value not in uuids:
                uuids.append(value)
    return uuids


def record_dspace_uuid(record: dict) -> tuple[str | None, list[str]]:
    """
    (the record's DSpace UUID, all DSpace UUIDs it carries). A Pure record
    belongs to ONE DSpace item: the PrimaryId's UUID if it has one, else the
    first Id's. More than one distinct UUID is a data error -- only the first
    is used, and the record is listed in the multiple_dspace_ids report.
    """
    uuids = extract_dspace_uuids_from_json_record(record)
    return (uuids[0] if uuids else None), uuids


def extract_dspace_uuid_from_json_record(record: dict) -> str | None:
    """The record's DSpace UUID (see record_dspace_uuid; kept for compatibility)."""
    return record_dspace_uuid(record)[0]


_FRACTION_RE = re.compile(r"^(.*T\d{2}:\d{2}:\d{2})\.(\d+)(.*)$")


def parse_iso_datetime(dt_string: str) -> datetime:
    """
    Parse an ISO-8601 datetime string into a timezone-aware datetime.

    Accepts an explicit offset ("+01:00"), "Z", or no offset at all (taken
    as UTC), and fractional seconds of any length -- Pure writes e.g.
    "2026-04-22T13:27:18.721+01:00". Works on Python 3.10+.
    Raises ValueError for anything else.
    """
    if not isinstance(dt_string, str) or not dt_string.strip():
        raise ValueError("empty datetime")
    value = dt_string.strip()
    if value.endswith(("Z", "z")):
        value = value[:-1] + "+00:00"
    match = _FRACTION_RE.match(value)
    if match:
        fraction = (match.group(2) + "000000")[:6]
        value = f"{match.group(1)}.{fraction}{match.group(3)}"
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def extract_handles_from_json_record(record: dict) -> list[str]:
    """
    All Handles in the 'links' list of a Pure record, canonical form, in
    order. Any link whose URL is a Handle counts, whatever its alias.
    """
    handles: list[str] = []
    for link in record.get("links") or []:
        if not isinstance(link, dict):
            continue
        url = (link.get("url") or "").strip()
        if url and is_handle_url(url):
            handle = normalize_handle(url)
            if handle not in handles:
                handles.append(handle)
    return handles


def extract_bitstream_uuid(url: str) -> str | None:
    """Pull the DSpace bitstream UUID out of a bitstream content URL, e.g.
    '.../server/api/core/bitstreams/{uuid}/content' -> {uuid}.
    (Same logic as create_pure_xml.py's extract_bitstream_uuid.)"""
    if not url:
        return None
    m = BITSTREAM_UUID_RE.search(url)
    return m.group(1) if m else None


def parse_pdf_handle_path(path: str, handle: str) -> tuple[str, str] | tuple[None, None]:
    """
    Split a DSpace 'pdf_handle_paths' entry (e.g. '/10379/1034/1/paper.pdf')
    into (sequence, filename), stripping the leading handle if present.
    (Same logic as create_pure_xml.py's parse_pdf_handle_path.)
    """
    path = (path or "").strip()
    if not path:
        return None, None
    handle = (handle or "").strip().strip("/")
    remainder = path.strip("/")
    prefix = handle + "/"
    if handle and remainder.startswith(prefix):
        remainder = remainder[len(prefix):]
    parts = remainder.split("/", 1)
    if len(parts) == 2:
        sequence, filename = parts
    elif len(parts) == 1 and parts[0]:
        sequence, filename = "1", parts[0]
    else:
        return None, None
    filename = clean_dspace_filename(unquote(filename)).strip()
    sequence = sequence.strip() or "1"
    if not filename:
        return None, None
    return sequence, filename


def build_dspace_file_ids(
    pdf_handle_paths: str,
    pdf_links: str,
    handle: str,
) -> tuple[list[str], list[str]]:
    """
    Build the DSpace-style file IDs for a record, in the format
    "{handle}:{seq}/{file uuid}:{filename}", e.g.
    "10379/1034:1/3c246f30-8d3a-4077-a013-6ca4ef262a3e:paper_0078.pdf".
    (Same ID construction as create_pure_xml.py's DSpace <file id="..."> values,
    which use "{handle}:{seq}/{bitstream_uuid}:{filename}".)

    Iteration is driven by 'pdf_handle_paths' (semicolon-separated), so every
    DSpace file entry is included even if 'pdf_links' is missing, shorter, or
    misaligned. The matching entry (by index) in 'pdf_links' supplies the
    bitstream UUID needed to build the full ID.

    Returns a pair of parallel lists:
        filenames — plain filenames, one per DSpace file (used for
                    filename-based matching against Pure files)
        file_ids  — the constructed "{handle}:{seq}/{uuid}:{filename}" IDs;
                    when a UUID can't be found (e.g. missing/misaligned
                    pdf_links entry), falls back to the original raw
                    pdf_handle_paths entry so no file information is lost.
    """
    raw_paths = [p.strip() for p in (pdf_handle_paths or "").split(";") if p.strip()]
    raw_links = [l.strip() for l in (pdf_links or "").split(";") if l.strip()]

    filenames: list[str] = []
    file_ids:  list[str] = []

    for idx, raw_path in enumerate(raw_paths):
        sequence, filename = parse_pdf_handle_path(raw_path, handle)
        if not filename:
            # Malformed entry: use the raw path string as-is rather than
            # dropping it.
            filename = raw_path
            sequence = "1"
        filenames.append(filename)

        raw_url = raw_links[idx] if idx < len(raw_links) else ""
        bitstream_uuid = extract_bitstream_uuid(raw_url)

        if handle and bitstream_uuid and filename:
            file_ids.append(f"{handle}:{sequence}/{bitstream_uuid}:{filename}")
        else:
            # Could not build the full ID (e.g. no bitstream UUID found in
            # the corresponding pdf_links entry) — fall back to the raw
            # pdf_handle_paths value rather than dropping the file.
            file_ids.append(raw_path)

    return filenames, file_ids


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
    for ev in pure_record.get("electronicVersions") or []:
        if not isinstance(ev, dict) or ev.get("typeDiscriminator") != "FileElectronicVersion":
            continue
        file_block = ev.get("file") or {}
        fid  = file_block.get("fileId", "") or ""
        fpid = file_block.get("pureId", "")
        fname = file_block.get("fileName", "") or ""
        file_ids.append(str(fid))
        file_pure_ids.append("" if fpid is None else str(fpid))
        file_names.append(fname)
    return file_ids, file_pure_ids, file_names


def pair_dspace_and_pure_files(
    dspace_filenames: list[str],
    pure_file_names: list[str],
) -> list[tuple[int | None, int | None, str]]:
    """
    Pair DSpace files with Pure FileElectronicVersions by filename, one to
    one (a Pure file is never paired with two DSpace files).

    Two passes, so a direct match is never taken away by a fallback match:
      1. direct: the cleaned DSpace filename and the Pure fileName are equal
         after pure_normalize_filename();
      2. fallback, for the files still unpaired: equal after
         _legacy_skeleton() (files uploaded under an old, mangled name).
    When several Pure files share a normalized name (e.g. a duplicate
    upload), the first unpaired one is used.

    Returns a list of (dspace_index | None, pure_index | None, status), in
    this order: matched pairs (in DSpace order), then unmatched DSpace files,
    then unmatched Pure files. status is FILE_MATCHED, FILE_MATCHED_FALLBACK,
    FILE_DSPACE_ONLY or FILE_PURE_ONLY.
    """
    def base_name(path: str) -> str:
        return pure_normalize_filename(clean_dspace_filename(unquote(path.rstrip("/").split("/")[-1])))

    direct_pure: dict[str, list[int]] = defaultdict(list)
    legacy_pure: dict[str, list[int]] = defaultdict(list)
    for i, fname in enumerate(pure_file_names):
        direct_pure[pure_normalize_filename(fname)].append(i)
        legacy_pure[_legacy_skeleton(fname)].append(i)

    used_pure: set[int] = set()
    partner: dict[int, tuple[int, str]] = {}

    def take(candidates: list[int]) -> int | None:
        for p_idx in candidates:
            if p_idx not in used_pure:
                used_pure.add(p_idx)
                return p_idx
        return None

    for d_idx, dname in enumerate(dspace_filenames):
        p_idx = take(direct_pure.get(base_name(dname), []))
        if p_idx is not None:
            partner[d_idx] = (p_idx, FILE_MATCHED)
    for d_idx, dname in enumerate(dspace_filenames):
        if d_idx in partner:
            continue
        p_idx = take(legacy_pure.get(_legacy_skeleton(dname.rstrip("/").split("/")[-1]), []))
        if p_idx is not None:
            partner[d_idx] = (p_idx, FILE_MATCHED_FALLBACK)

    pairs: list[tuple[int | None, int | None, str]] = []
    for d_idx in range(len(dspace_filenames)):
        if d_idx in partner:
            p_idx, status = partner[d_idx]
            pairs.append((d_idx, p_idx, status))
    for d_idx in range(len(dspace_filenames)):
        if d_idx not in partner:
            pairs.append((d_idx, None, FILE_DSPACE_ONLY))
    for p_idx in range(len(pure_file_names)):
        if p_idx not in used_pure:
            pairs.append((None, p_idx, FILE_PURE_ONLY))
    return pairs


def match_dspace_to_pure_files(
    dspace_pdf_paths: list[str],
    pure_file_names: list[str],
) -> tuple[set[int], set[int]]:
    """
    Index sets of the DSpace files and Pure files that were paired
    (see pair_dspace_and_pure_files). Kept for compatibility.
    """
    pairs = pair_dspace_and_pure_files(dspace_pdf_paths, pure_file_names)
    matched = [(d, p) for d, p, status in pairs if d is not None and p is not None]
    return {d for d, _ in matched}, {p for _, p in matched}


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
    n_dspace: int,
    n_pure: int,
    n_matched: int,
) -> str:
    """
    Derive the file_match_type label for a record from the number of DSpace
    files, Pure files and matched pairs.

      "no_pdf"             — neither side has files.
      "dspace_only_pdf"    — DSpace has files, Pure has none.
      "pure_only_pdf"      — Pure has files, DSpace has none.
      "file_name_mismatch" — both sides have files but none matched.
      "full_match"         — every DSpace file AND every Pure file is in a
                             matched pair.
      "partial_match"      — at least one pair matched, but at least one file
                             on either side is unmatched (including extra
                             Pure files when every DSpace file matched).
    """
    if not n_dspace and not n_pure:
        return "no_pdf"
    if n_dspace and not n_pure:
        return "dspace_only_pdf"
    if n_pure and not n_dspace:
        return "pure_only_pdf"
    if n_matched == 0:
        return "file_name_mismatch"
    if n_matched == n_dspace and n_matched == n_pure:
        return "full_match"
    return "partial_match"


def _build_rows_for_pair(
    csv_rec: dict,
    json_rec: dict,
    handle_url: str,
) -> tuple[dict, list[dict]]:
    """Record-level row and file-level rows for one DSpace item / Pure record pair."""
    dspace_uuid = (csv_rec.get("uuid") or "").strip()
    pure_uuid   = json_rec.get("uuid", "") or ""
    pure_id     = str(json_rec.get("pureId", "") or "")
    title_obj   = json_rec.get("title", {})
    title       = title_obj.get("value", "").strip() if isinstance(title_obj, dict) else ""

    # bare handle (no "http://hdl.handle.net/" prefix) is what DSpace file IDs
    # are keyed on, same as create_pure_xml.py.
    bare_handle = bare_handle_of(handle_url) if handle_url else (csv_rec.get("handle") or "").strip()
    dspace_names, dspace_ids = build_dspace_file_ids(
        (csv_rec.get("pdf_handle_paths") or "").strip(),
        (csv_rec.get("pdf_links") or "").strip(),
        bare_handle,
    )
    pure_fids, pure_fpids, pure_fnames = extract_file_ids_from_pure_record(json_rec)

    pairs = pair_dspace_and_pure_files(dspace_names, pure_fnames) if (dspace_names or pure_fnames) else []
    n_matched = sum(1 for d, p, _ in pairs if d is not None and p is not None)
    file_match_type = classify_file_match(len(dspace_names), len(pure_fids), n_matched)

    common = {
        "dspace_uuid": dspace_uuid,
        "pure_uuid":   pure_uuid,
        "pure_id":     pure_id,
        "title":       title,
    }

    # Record level: lists in pair order, so the matched files line up.
    d_order = [d for d, _, _ in pairs if d is not None]
    p_order = [p for _, p, _ in pairs if p is not None]
    record_row = dict(
        common,
        dspace_file_id="; ".join(dspace_ids[d] for d in d_order),
        pure_file_id="; ".join(pure_fids[p] for p in p_order),
        pure_file_pure_id="; ".join(pure_fpids[p] for p in p_order),
        pure_file_name="; ".join(pure_fnames[p] for p in p_order),
        file_match_type=file_match_type,
        handle=handle_url,
        # Internal fields used for duplicate scoring; not written out.
        _modified_date=json_rec.get("modifiedDate", ""),
        _n_matched=n_matched,
        _n_pure=len(pure_fids),
    )

    # File level: one row per pair; a record without files gets one row.
    file_rows: list[dict] = []
    for d, p, status in pairs:
        file_rows.append(dict(
            common,
            dspace_file_id=dspace_ids[d] if d is not None else "",
            pure_file_id=pure_fids[p] if p is not None else "",
            pure_file_pure_id=pure_fpids[p] if p is not None else "",
            pure_file_name=pure_fnames[p] if p is not None else "",
            file_match=status,
            file_match_type=file_match_type,
            handle=handle_url,
        ))
    if not file_rows:
        file_rows.append(dict(
            common,
            dspace_file_id="", pure_file_id="", pure_file_pure_id="", pure_file_name="",
            file_match=FILE_NO_FILES,
            file_match_type=file_match_type,
            handle=handle_url,
        ))
    record_row["_file_rows"] = file_rows
    return record_row, file_rows


def match_records(
    csv_by_handle: dict[str, dict],
    csv_by_uuid:   dict[str, dict],
    json_records: list[dict],
    pdf_filter: str = "all",
    handle_filter: set[str] | None = None,
) -> tuple[list[dict], int, int, dict]:
    """
    Match Pure JSON records against DSpace CSV records:

      1. by the record's DSpace UUID (record_dspace_uuid: the PrimaryId
         identifier's, else the Id identifier's; case-insensitive);
      2. only if that UUID isn't in the CSV (or the record has none): by
         Handle -- the first link on the Pure record whose URL is a Handle
         of an item in the CSV, in any of the accepted forms.

    A Pure record belongs to ONE DSpace item, so it produces at most one row.
    A record carrying several distinct DSpace UUIDs, or (when matched by
    Handle) Handles of several CSV items, is listed in
    info["multiple_dspace_ids"]; only the identifier above is used.

    pdf_filter controls which matched records are included:
      'all'                — include every matched record (default)
      'full_match'         — every DSpace file and every Pure file is in a matched pair
      'partial_match'      — at least one pair matched, but some file on either side did not
      'file_name_mismatch' — both sides have files but no filenames matched
      'dspace_only_pdf'    — DSpace has files, Pure has none
      'pure_only_pdf'      — Pure has files, DSpace has none
      'no_pdf'             — neither DSpace nor Pure have any files

    handle_filter, if not None, restricts output to records whose matched
    Handle URL is in the set (built from --handles and/or --xml-filter).

    Returns (output_rows, skipped_count, filtered_out_count, info):
      info["linked_dspace_uuids"]  DSpace UUIDs (lower case) that some Pure
                                   record links to (before the Handle / PDF
                                   filters)
      info["unmatched_pure"]       Pure records that carry a DSpace UUID or a
                                   Handle, none of which is in the CSV
      info["no_dspace_link"]       number of Pure records with neither
      info["multiple_dspace_ids"]  Pure records with 2+ distinct DSpace UUIDs,
                                   or matched by Handle with Handles of 2+
                                   CSV items (only one is used)
    """
    output_rows = []
    skipped = 0
    filtered_out = 0
    linked_dspace_uuids: set[str] = set()
    unmatched_pure: list[dict] = []
    no_dspace_link = 0
    multiple_dspace_ids: list[dict] = []

    for json_rec in json_records:
        record_uuid, dspace_uuids = record_dspace_uuid(json_rec)
        pure_handles = extract_handles_from_json_record(json_rec)

        # --- 1. The record's DSpace UUID ---
        matches: list[tuple[dict, str]] = []
        problem = ""
        csv_rec = csv_by_uuid.get(record_uuid) if record_uuid else None
        if csv_rec is not None:
            matches.append((csv_rec, build_handle_url(csv_rec.get("handle", ""))))

        # --- 2. Handle (only if the DSpace UUID didn't match) ---
        if not matches:
            handle_items: list[tuple[dict, str]] = []
            for handle in pure_handles:
                h_rec = csv_by_handle.get(handle)
                if h_rec is not None and all(h_rec is not r for r, _ in handle_items):
                    handle_items.append((h_rec, handle))
            if handle_items:
                matches.append(handle_items[0])
                if len(handle_items) > 1:
                    problem = "Handles of several DSpace items in the CSV"
                    # The other Handles still point at DSpace items: not reported as unlinked.
                    for other_rec, _ in handle_items[1:]:
                        linked_dspace_uuids.add((other_rec.get("uuid") or "").strip().lower())

        if len(dspace_uuids) > 1:
            problem = "; ".join(p for p in ("several DSpace UUIDs", problem) if p)
            # The other UUIDs still point at DSpace items: not reported as unlinked.
            for other in dspace_uuids:
                other_rec = csv_by_uuid.get(other)
                if other_rec is not None:
                    linked_dspace_uuids.add((other_rec.get("uuid") or "").strip().lower())
        if problem:
            title_obj = json_rec.get("title", {})
            used_rec, used_handle = matches[0] if matches else (None, "")
            multiple_dspace_ids.append({
                "pure_uuid": json_rec.get("uuid", "") or "",
                "pure_id": str(json_rec.get("pureId", "") or ""),
                "title": title_obj.get("value", "").strip() if isinstance(title_obj, dict) else "",
                "dspace_uuids": "; ".join(dspace_uuids),
                "handles": "; ".join(pure_handles),
                "used_dspace_uuid": (used_rec.get("uuid") or "").strip() if used_rec else "",
                "used_handle": used_handle,
                "problem": problem,
            })

        if not matches:
            if dspace_uuids or pure_handles:
                title_obj = json_rec.get("title", {})
                unmatched_pure.append({
                    "pure_uuid": json_rec.get("uuid", "") or "",
                    "pure_id": str(json_rec.get("pureId", "") or ""),
                    "title": title_obj.get("value", "").strip() if isinstance(title_obj, dict) else "",
                    "dspace_uuids": "; ".join(dspace_uuids),
                    "handles": "; ".join(pure_handles),
                    "reason": ("DSpace UUID(s) and Handle(s) not in the CSV" if dspace_uuids and pure_handles
                               else "DSpace UUID(s) not in the CSV" if dspace_uuids
                               else "Handle(s) not in the CSV"),
                })
            else:
                no_dspace_link += 1
            continue

        for csv_rec, handle_url in matches:
            linked_dspace_uuids.add((csv_rec.get("uuid") or "").strip().lower())

            # --- Apply Handle filter (--handles / --xml-filter) ---
            if handle_filter is not None and handle_url not in handle_filter:
                filtered_out += 1
                continue

            row, _ = _build_rows_for_pair(csv_rec, json_rec, handle_url)

            # --- Apply PDF filter ---
            if pdf_filter != "all" and row["file_match_type"] != pdf_filter:
                continue

            # Require at minimum the four core identifiers to be non-empty
            core_fields = ("dspace_uuid", "pure_uuid", "pure_id", "handle")
            if all(str(row[f]).strip() for f in core_fields):
                output_rows.append(row)
            else:
                skipped += 1

    info = {
        "linked_dspace_uuids": linked_dspace_uuids,
        "unmatched_pure": unmatched_pure,
        "no_dspace_link": no_dspace_link,
        "multiple_dspace_ids": multiple_dspace_ids,
    }
    return output_rows, skipped, filtered_out, info


def _score_row(row: dict) -> tuple:
    """
    Return a sort key for a candidate row (higher = better).

    Priority (all descending):
      1. Number of matched DSpace/Pure file pairs.
      2. Total number of Pure files.
      3. Tie-breaker: most recently modified (modifiedDate from the Pure record);
         a missing or unparseable date ranks lowest.
    """
    try:
        modified_dt = parse_iso_datetime(row.get("_modified_date", ""))
    except (ValueError, TypeError):
        modified_dt = datetime.min.replace(tzinfo=timezone.utc)
    return (row.get("_n_matched", 0), row.get("_n_pure", 0), modified_dt)


def _row_key(row: dict) -> tuple[str, str]:
    """A row is one (Pure record, DSpace item) pair."""
    return row["pure_uuid"], row["dspace_uuid"]


def resolve_duplicates(
    output_rows: list[dict],
) -> tuple[list[dict], list[dict], dict[str, list[dict]]]:
    """
    Detect rows where the same dspace_uuid or handle maps to more than one
    Pure record, select the best candidate for the main output, and collect
    all candidates (winners and losers alike) for the duplicates file.

    Selection criteria (descending priority):
      1. Most matched DSpace/Pure file pairs.
      2. Most Pure files in total.
      3. Most recently modified Pure record.

    Rows are identified by (pure_uuid, dspace_uuid), so a Pure record that is
    linked to two different DSpace items keeps a row for each.

    Returns:
        deduplicated_rows  — the main output: losers removed
        duplicate_rows     — every row involved in any collision, annotated
                             with a 'duplicate_key' column (dspace_uuid
                             preferred, handle as fallback)
        duplicate_groups   — dict keyed by collision key, for warning output
    """
    by_dspace_uuid: dict[str, list[dict]] = defaultdict(list)
    by_handle:      dict[str, list[dict]] = defaultdict(list)
    for row in output_rows:
        by_dspace_uuid[row["dspace_uuid"].lower()].append(row)
        by_handle[row["handle"]].append(row)

    to_remove: set[tuple[str, str]] = set()
    duplicate_groups: dict[str, list[dict]] = {}
    dup_by_key: dict[tuple[str, str], dict] = {}

    reported_sets: set[frozenset] = set()

    def _process_group(key: str, rows: list[dict], prefer: bool) -> None:
        if len({r["pure_uuid"] for r in rows}) < 2:
            return
        row_set = frozenset(_row_key(r) for r in rows)
        if row_set in reported_sets:
            # Same collision already found under the other key (the DSpace
            # UUID and the Handle of one item) -- reported once.
            return
        reported_sets.add(row_set)
        duplicate_groups[key] = rows
        winner = max(rows, key=_score_row)
        for row in rows:
            rk = _row_key(row)
            if prefer or rk not in dup_by_key:
                dup_by_key[rk] = dict(row, duplicate_key=key)
            if row["pure_uuid"] != winner["pure_uuid"]:
                to_remove.add(rk)

    for key, rows in by_dspace_uuid.items():
        _process_group(key, rows, prefer=True)
    for key, rows in by_handle.items():
        _process_group(key, rows, prefer=False)

    duplicate_rows = [dup_by_key[_row_key(r)] for r in output_rows if _row_key(r) in dup_by_key]

    seen: set[tuple[str, str]] = set()
    deduplicated: list[dict] = []
    for row in output_rows:
        rk = _row_key(row)
        if rk in to_remove or rk in seen:
            continue
        seen.add(rk)
        deduplicated.append(row)
    return deduplicated, duplicate_rows, duplicate_groups


RECORD_FIELDS = [
    "dspace_uuid", "pure_uuid", "pure_id", "title",
    "dspace_file_id", "pure_file_id", "pure_file_pure_id", "pure_file_name",
    "file_match_type", "handle",
]
FILE_FIELDS = [
    "dspace_uuid", "pure_uuid", "pure_id", "title",
    "dspace_file_id", "pure_file_id", "pure_file_pure_id", "pure_file_name",
    "file_match", "file_match_type", "handle",
]
UNMATCHED_DSPACE_FIELDS = ["dspace_uuid", "handle", "title", "dc.type", "collection_names", "pdf_count"]
UNMATCHED_PURE_FIELDS = ["pure_uuid", "pure_id", "title", "dspace_uuids", "handles", "reason"]
MULTIPLE_IDS_FIELDS = ["pure_uuid", "pure_id", "title", "dspace_uuids", "handles",
                       "used_dspace_uuid", "used_handle", "problem"]


def _write_csv(rows: list[dict], fieldnames: list[str], output_path: str) -> None:
    folder = os.path.dirname(output_path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    with open(output_path, "w", newline="", encoding=OUTPUT_ENCODING) as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in fieldnames})


def write_output(output_rows: list[dict], output_path: str) -> None:
    """Write the record-level output (one row per matched record)."""
    _write_csv(output_rows, RECORD_FIELDS, output_path)


def write_file_output(output_rows: list[dict], output_path: str) -> None:
    """Write the file-level output (one row per file, DSpace and Pure files paired)."""
    file_rows = [file_row for row in output_rows for file_row in row["_file_rows"]]
    _write_csv(file_rows, FILE_FIELDS, output_path)


def write_duplicates_output(duplicate_rows: list[dict], output_path: str) -> None:
    """
    Write all rows involved in duplicate collisions to a separate CSV.
    Identical columns to the record-level output plus a leading
    'duplicate_key' column that shows which dspace_uuid or handle triggered
    the group.
    """
    _write_csv(duplicate_rows, ["duplicate_key"] + RECORD_FIELDS, output_path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Match DSpace CSV and Pure JSON records (by DSpace UUID, then Handle), "
            "pair their files, and write the identifiers per record and per file."
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
        default="./pure_temp/temp_test_matched_records.csv",
        metavar="OUTPUT_FILE",
        help=(
            "Base path for the output CSVs (default: ./pure_temp/temp_test_matched_records.csv). "
            "The --pdf-filter value is added as a prefix and today's date (YYYY-MM-DD) as a "
            "suffix to the file name. The per-file, duplicates and unmatched files are written "
            "alongside it."
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
            "'full_match' includes only records where every DSpace file and every Pure file "
            "is in a matched pair; "
            "'partial_match' includes only records where at least one pair matched but some "
            "file on either side did not; "
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
            "Only include JSON records modified after the start of this date (UTC) "
            "(e.g. '2025-01-01'). Filter is off by default."
        ),
    )
    parser.add_argument(
        "--handles",
        default=None,
        metavar="HANDLE_LIST",
        help=(
            "Only include records whose Handle is in this comma-separated list, "
            "e.g. '10379/7309' or '10379/7309,10379/1501,10379/5890'. "
            "Filter is off by default. Can be combined with --xml-filter "
            "(the two sets of Handles are unioned)."
        ),
    )
    parser.add_argument(
        "--xml-filter",
        default=None,
        metavar="XML_FILE",
        dest="xml_filter",
        help=(
            "Only include records whose Handle matches a record id in this "
            "Pure XML import file, e.g. a temp_pure_import XML. Handles are "
            "extracted from the 'id' attribute of each top-level record "
            "element (<book id=\"10379/7309\">, <contributionToJournal "
            "id=\"...\">, etc.). Filter is off by default. Can be combined "
            "with --handles (the two sets of Handles are unioned)."
        ),
    )
    return parser.parse_args()


def _dated_path(path: str, date_str: str) -> str:
    """
    Insert a YYYY-MM-DD suffix before the file extension (file name only,
    never inside a folder name).
    e.g. "matched_records.csv" → "matched_records_2026-06-09.csv"
    """
    folder, name = os.path.split(path)
    stem, ext = os.path.splitext(name)
    return os.path.join(folder, f"{stem}_{date_str}{ext}")


def _output_path(base: str, prefix: str, insert: str, date_str: str) -> str:
    """
    Output file path from the --output base path: '<prefix><insert><name>' in
    the same folder, dated. Works with '/' and (on Windows) '\\' separators.
    """
    folder, name = os.path.split(base)
    return _dated_path(os.path.join(folder, f"{prefix}{insert}{name}"), date_str)


def _pdf_count(row: dict) -> int:
    return len([p for p in (row.get("pdf_handle_paths") or "").split(";") if p.strip()])


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
    csv_by_handle, csv_by_uuid, csv_rows, handle_collisions = load_csv_records(args.csv)
    print(f"  → {len(csv_by_handle)} records with Handles loaded.")
    if handle_collisions:
        print(f"  → WARNING: {handle_collisions} CSV row(s) repeat a Handle used by an earlier row "
              f"(the later row is used for Handle matching).")

    print(f"Loading JSON from: {args.json}")
    with open(args.json, encoding="utf-8-sig") as fh:
        json_records: list[dict] = json.load(fh)
    print(f"  → {len(json_records)} records loaded.")

    json_filtered = bool(args.modified_by or modified_after_dt)
    if json_filtered:
        json_records = filter_json_records(
            json_records,
            modified_by=args.modified_by,
            modified_after=modified_after_dt,
        )
        print(f"  → {len(json_records)} records after filtering.")

    # --- Handle filter (--handles / --xml-filter) ---
    handle_filter: set[str] | None = None
    if args.handles or args.xml_filter:
        handle_filter = set()
        if args.handles:
            from_handles = parse_handles_arg(args.handles)
            print(f"  → {len(from_handles)} handle(s) from --handles.")
            handle_filter |= from_handles
        if args.xml_filter:
            print(f"Loading XML filter from: {args.xml_filter}")
            from_xml = extract_handles_from_xml_filter(args.xml_filter)
            print(f"  → {len(from_xml)} handle(s) extracted from XML filter.")
            handle_filter |= from_xml
        print(f"  → {len(handle_filter)} handle(s) in combined filter.")

    print("Matching records by DSpace UUID, then Handle…")
    output_rows, skipped, filtered_out, info = match_records(
        csv_by_handle, csv_by_uuid, json_records,
        pdf_filter=args.pdf_filter,
        handle_filter=handle_filter,
    )
    print(f"  → {len(output_rows)} complete matches found.")
    if skipped:
        print(f"  → {skipped} matched records skipped due to missing core fields.")
    if filtered_out:
        print(f"  → {filtered_out} matched records excluded by the Handle filter.")
    if info["multiple_dspace_ids"]:
        print(f"  → WARNING: {len(info['multiple_dspace_ids'])} Pure record(s) carry several DSpace UUIDs "
              f"(or Handles of several DSpace items) — only one is used (PrimaryId first, then Id).")
    print(f"  → {info['no_dspace_link']} Pure record(s) have no DSpace UUID or Handle (not DSpace records).")

    # --- Duplicate resolution ---
    output_rows, duplicate_rows, duplicate_groups = resolve_duplicates(output_rows)

    if duplicate_groups:
        print(f"  → WARNING: {len(duplicate_groups)} duplicate keys detected "
              f"(same dspace_uuid or handle maps to multiple Pure records).")
        for key, rows in duplicate_groups.items():
            winner = max(rows, key=_score_row)
            losers = [r for r in rows if r["pure_uuid"] != winner["pure_uuid"]]
            loser_uuids = ", ".join(r["pure_uuid"] for r in losers)
            print(f"     • {key}")
            print(f"       kept:    {winner['pure_uuid']}  (score {_score_row(winner)})")
            print(f"       dropped: [{loser_uuids}]")
    else:
        print("  → No duplicates found.")

    # --- Unmatched reports ---
    unmatched_pure = info["unmatched_pure"]
    if handle_filter is not None:
        unmatched_pure = [
            r for r in unmatched_pure
            if set(filter(None, r["handles"].split("; "))) & handle_filter
        ]
    unmatched_dspace: list[dict] | None
    if json_filtered:
        # With --modified-by / --modified-after only part of Pure is read,
        # so "no Pure record links to it" would be meaningless.
        unmatched_dspace = None
    else:
        unmatched_dspace = []
        for row in csv_rows:
            dspace_uuid = (row.get("uuid") or "").strip()
            if dspace_uuid.lower() in info["linked_dspace_uuids"]:
                continue
            handle_url = build_handle_url(row.get("handle", ""))
            if handle_filter is not None and handle_url not in handle_filter:
                continue
            unmatched_dspace.append({
                "dspace_uuid": dspace_uuid,
                "handle": handle_url,
                "title": (row.get("dc.title") or "").strip(),
                "dc.type": (row.get("dc.type") or "").strip(),
                "collection_names": (row.get("collection_names") or "").strip(),
                "pdf_count": _pdf_count(row),
            })

    # --- Write outputs with pdf_filter prefix and date suffix ---
    prefix = f"{args.pdf_filter}_"
    main_output = _output_path(args.output, prefix, "", today)
    file_output = _output_path(args.output, prefix, "per_file_", today)
    dup_output = _output_path(args.output, prefix, "duplicate_", today)
    unmatched_dspace_output = _output_path(args.output, "", "unmatched_dspace_", today)
    unmatched_pure_output = _output_path(args.output, "", "unmatched_pure_", today)
    multiple_ids_output = _output_path(args.output, "", "multiple_dspace_ids_", today)

    write_output(output_rows, main_output)
    print(f"Output written to: {main_output}")

    write_file_output(output_rows, file_output)
    n_file_rows = sum(len(r["_file_rows"]) for r in output_rows)
    print(f"Per-file output written to: {file_output} ({n_file_rows} rows)")

    if duplicate_rows:
        write_duplicates_output(duplicate_rows, dup_output)
        print(f"Duplicates written to: {dup_output}")

    if unmatched_dspace is None:
        print("  → Unmatched DSpace report not written (--modified-by / --modified-after "
              "limit the Pure records read).")
    elif unmatched_dspace:
        _write_csv(unmatched_dspace, UNMATCHED_DSPACE_FIELDS, unmatched_dspace_output)
        print(f"DSpace items no Pure record links to: {len(unmatched_dspace)} → {unmatched_dspace_output}")
    else:
        print("  → Every DSpace item is linked to a Pure record.")

    if unmatched_pure:
        _write_csv(unmatched_pure, UNMATCHED_PURE_FIELDS, unmatched_pure_output)
        print(f"Pure records whose DSpace UUID / Handle is not in the CSV: {len(unmatched_pure)} "
              f"→ {unmatched_pure_output}")
    else:
        print("  → Every Pure record with a DSpace UUID or Handle was found in the CSV.")

    if info["multiple_dspace_ids"]:
        _write_csv(info["multiple_dspace_ids"], MULTIPLE_IDS_FIELDS, multiple_ids_output)
        print(f"Pure records with several DSpace UUIDs / Handles: {len(info['multiple_dspace_ids'])} "
              f"→ {multiple_ids_output}")


if __name__ == "__main__":
    main()
