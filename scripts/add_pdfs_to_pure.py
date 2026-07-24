#!/usr/bin/env python3
"""
add_pdfs_to_pure.py

For each record in the Pure JSON that has a matching DSpace row with a PDF path:
  1. Downloads the PDF from DSpace (or reads from local disk)
  2. Uploads it to Pure's file-upload endpoint (temporary file, valid for 2 hours)
  3. Immediately PUTs the Pure record with a FileElectronicVersion referencing the upload
  4. Logs all outcomes

Records are processed one at a time to ensure each uploaded file is linked before
the 2-hour Pure expiry window. Multiple PDFs per DSpace row are all processed.

Usage:
    python add_pdfs_to_pure.py --dspace-csv <path> --pure-json <path> [options]

.env file must contain:
    PURE_ROOT_API_KEY_TEST=your_key_here   (UAT)
    PURE_ROOT_API_KEY=your_key_here        (Production & TEMP)

Options:
    --test                  Use UAT environment
    --temp                  Use TEMP environment
    --source                Where to get PDFs: 'dspace' (default) or 'local'
    --save-locally          Also save downloaded PDFs to disk (dspace source only)
    --pdf-dir               Save/read directory for PDFs (default: ./dspace_pdfs)
    --log-dir               Directory for logs (default: ./pdf_upload_logs)
    --dry-run               Match records and report what would be done, but do not upload/PUT
    --skip-existing /
    --no-skip-existing      Skip files already in Pure with same name and size (default: skip)
"""


import os
import re
import sys
import csv
import json
import time
import argparse
import requests
from collections import defaultdict
from datetime import datetime, date
from dotenv import load_dotenv
from tqdm import tqdm
from urllib.parse import unquote


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TODAY  = date.today().isoformat()
RUN_TS = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

DOI_REGEX    = re.compile(r'^(?:https?://)?(?:doi\.org/|doi:)?(10\.\S+)$', re.IGNORECASE)
HANDLE_REGEX = re.compile(r'^(?:https?://hdl\.handle\.net/)?(10379/\S+)$', re.IGNORECASE)

LICENSE_MAP = {
    "CC BY-NC-ND":         "cc_by_nc_nd",
    "CC BY":               "cc_by",
    "CC BY-SA":            "cc_by_sa",
    "CC BY-NC":            "cc_by_nc",
    "CC BY-NC-SA":         "cc_by_nc_sa",
    "Public Domain":       "public_domain",
    "All rights reserved": "all_rights_reserved",
}

# System fields stripped before PUT
SYSTEM_FIELDS = {
    "pureId", "createdBy", "createdDate", "modifiedBy", "modifiedDate",
    "portalUrl", "prettyUrlIdentifiers", "previousUuids", "version",
    "systemName", "systemModified", "current",
}
NESTED_SYSTEM_FIELDS = {"pureId", "systemModified", "current"}


# ---------------------------------------------------------------------------
# Normalisation helpers
# ---------------------------------------------------------------------------

def safe_path(path: str) -> str:
    if sys.platform == "win32":
        abs_path = os.path.abspath(path)
        if not abs_path.startswith("\\\\?\\"):
            return "\\\\?\\" + abs_path
        return abs_path
    return path


def sanitize_filename(file_name: str) -> str:
    """
    Replace characters that are illegal in Windows filenames with underscores.
    Illegal characters on Windows: \\ / : * ? " < > |
    On other platforms this is a no-op.
    """
    if sys.platform == "win32":
        return re.sub(r'[\\/:*?"<>|]', '_', file_name)
    return file_name


def pure_normalize_filename(name: str) -> str:
    """
    Normalize a filename the same way Pure does when storing uploaded files.
    Pure replaces characters that are not alphanumeric, hyphen, underscore,
    dot, or space with underscores (e.g. commas become underscores).
    Used to match DSpace-derived filenames against Pure-stored filenames in
    the skip check, where an exact match on the original name is not reliable.
    """
    return re.sub(r'[^\w.\- ]', '_', name)


def is_valid_pdf(content: bytes) -> bool:
    """Return True if content starts with the PDF magic bytes and is larger than 1kb."""
    return len(content) > 1024 and content[:4] == b'%PDF'


def normalize_doi(value: str) -> str:
    if not isinstance(value, str):
        return value
    match = DOI_REGEX.match(value.strip().lower())
    return f"https://doi.org/{match.group(1)}" if match else value


def normalize_handle(value: str) -> str:
    if not isinstance(value, str):
        return value
    match = HANDLE_REGEX.match(value.strip().lower())
    return f"http://hdl.handle.net/{match.group(1)}" if match else value


def extract_dois_from_uri(uri_str: str) -> list:
    if not uri_str:
        return []
    result = []
    for u in uri_str.split(";"):
        u = u.strip().lower()
        m = DOI_REGEX.match(u)
        if m:
            result.append(f"https://doi.org/{m.group(1)}")
    return result


def extract_handles_from_uri(uri_str: str) -> list:
    if not uri_str:
        return []
    result = []
    for u in uri_str.split(";"):
        u = u.strip().lower()
        m = HANDLE_REGEX.match(u)
        if m:
            result.append(f"http://hdl.handle.net/{m.group(1)}")
    return result


# ---------------------------------------------------------------------------
# Record cleaning (strip system fields before PUT)
# ---------------------------------------------------------------------------

def strip_system_fields(obj, top_level: bool = True):
    if isinstance(obj, dict):
        fields = SYSTEM_FIELDS if top_level else NESTED_SYSTEM_FIELDS
        return {k: strip_system_fields(v, top_level=False)
                for k, v in obj.items() if k not in fields}
    if isinstance(obj, list):
        return [strip_system_fields(i, top_level=False) for i in obj if i is not None]
    return obj


def remove_nulls(obj):
    if isinstance(obj, dict):
        cleaned = {k: remove_nulls(v) for k, v in obj.items()}
        return {k: v for k, v in cleaned.items() if v is not None}
    if isinstance(obj, list):
        return [remove_nulls(i) for i in obj if i is not None]
    return obj


# ---------------------------------------------------------------------------
# DSpace / Pure helpers
# ---------------------------------------------------------------------------

def resolve_license_uri(rights_str: str) -> str:
    key = LICENSE_MAP.get(rights_str.strip(), "cc_by_nc") if rights_str else "cc_by_nc"
    return f"/dk/atira/pure/core/document/licenses/{key}"


def resolve_embargo_and_access(dspace_row: dict):
    """
    Returns (embargo_date_iso | None, embargo_active bool, embargo_period dict | None)
    """
    embargo_date_str = dspace_row.get("dc.date.embargo", "").strip()
    embargo_desc     = dspace_row.get("dc.description.embargo", "").strip()

    embargo_date_iso = None
    embargo_active   = False

    if embargo_date_str:
        try:
            from dateutil import parser as dp
            parsed    = dp.parse(embargo_date_str, dayfirst=True)
            candidate = parsed.strftime("%Y-%m-%d")
        except Exception:
            m = re.search(r'\b(1[89]\d{2}|20\d{2})\b', embargo_date_str)
            candidate = f"{m.group(1)}-01-01" if m else None
        if candidate and candidate > TODAY:
            embargo_date_iso = candidate
            embargo_active   = True

    if not embargo_active and embargo_desc and embargo_desc > TODAY:
        embargo_date_iso = embargo_desc
        embargo_active   = True

    embargo_period = {"endDate": embargo_date_iso} if embargo_active else None
    return embargo_date_iso, embargo_active, embargo_period


def already_has_file_ev(pure_record: dict, file_name: str = None, file_size: int = None) -> bool:
    """
    Return True if the record already has a FileElectronicVersion whose fileName
    matches file_name after Pure-style normalization. Size is only used to
    disambiguate when both sides have a known value — if either is unknown,
    the filename match alone is sufficient.
    """
    if file_name is None:
        return False

    norm_name = pure_normalize_filename(file_name)

    for ev in pure_record.get("electronicVersions", []):
        if ev.get("typeDiscriminator") != "FileElectronicVersion":
            continue
        file_block = ev.get("file", {})
        ev_name = file_block.get("fileName", "")
        ev_size = file_block.get("size", -1)
        if pure_normalize_filename(ev_name) != norm_name:
            continue
        # Normalized names match. Only reject if both sizes are known and differ.
        if file_size is not None and ev_size != -1 and file_size != ev_size:
            return False
        return True

    return False


def needs_metadata_update(ev: dict, dspace_row: dict) -> tuple[bool, dict]:
    """
    Compare a FileElectronicVersion's metadata against DSpace-derived values.
    Returns (needs_update bool, updated_ev dict).
    Checks: licenseType, accessType, versionType, visibleOnPortalDate, embargoPeriod.
    """
    _, embargo_active, embargo_period = resolve_embargo_and_access(dspace_row)
    license_uri = resolve_license_uri(dspace_row.get("dc.rights", "").strip())
    access_uri  = (
        "/dk/atira/pure/core/openaccesspermission/embargoed"
        if embargo_active else
        "/dk/atira/pure/core/openaccesspermission/open"
    )
    version_uri        = "/dk/atira/pure/researchoutput/electronicversion/versiontype/authorsversion"
    visible_on_portal  = TODAY

    changed = False
    updated = dict(ev)  # shallow copy to modify

    # License
    if ev.get("licenseType", {}).get("uri") != license_uri:
        updated["licenseType"] = {"uri": license_uri}
        changed = True

    # Access type
    if ev.get("accessType", {}).get("uri") != access_uri:
        updated["accessType"] = {"uri": access_uri}
        changed = True

    # Version type — update if missing
    if not ev.get("versionType"):
        updated["versionType"] = {"uri": version_uri}
        changed = True

    # Visible on portal date — update if missing
    if not ev.get("visibleOnPortalDate"):
        updated["visibleOnPortalDate"] = visible_on_portal
        changed = True

    # Embargo period — update if DSpace has one and Pure doesn't, or end dates differ
    pure_embargo_end = ev.get("embargoPeriod", {}).get("endDate")
    dspace_embargo_end = embargo_period.get("endDate") if embargo_period else None
    if dspace_embargo_end != pure_embargo_end:
        if embargo_period:
            updated["embargoPeriod"] = embargo_period
        elif "embargoPeriod" in updated:
            del updated["embargoPeriod"]
        changed = True

    return changed, updated


# ---------------------------------------------------------------------------
# Duplicate FileElectronicVersion resolution
# ---------------------------------------------------------------------------

# Fields checked when scoring how "complete" a FileElectronicVersion's own
# metadata is (not the nested file block, which is identical across true
# duplicates by definition — same fileName + size).
FILE_EV_METADATA_FIELDS = (
    "licenseType", "accessType", "versionType", "visibleOnPortalDate",
    "embargoPeriod", "title",
)


def find_duplicate_file_groups(pure_record: dict) -> dict:
    """
    Group a record's FileElectronicVersions by (fileName, size).
    Returns {(fileName, size): [ev, ev, ...]} for groups with more than
    one entry — i.e. only the genuine duplicates, keyed by object identity
    so callers can safely tell entries apart even when every other field
    (including fileName/size) is identical.
    """
    groups = defaultdict(list)
    for ev in pure_record.get("electronicVersions", []):
        if ev.get("typeDiscriminator") != "FileElectronicVersion":
            continue
        file_block = ev.get("file") or {}
        file_name = file_block.get("fileName")
        if not file_name:
            continue
        key = (file_name, file_block.get("size"))
        groups[key].append(ev)

    return {key: evs for key, evs in groups.items() if len(evs) > 1}


def metadata_completeness_score(ev: dict) -> int:
    """
    Count how many of FILE_EV_METADATA_FIELDS are meaningfully populated
    on a FileElectronicVersion. Higher = more complete metadata.
    """
    score = 0
    for field in FILE_EV_METADATA_FIELDS:
        value = ev.get(field)
        if value is None:
            continue
        if isinstance(value, (dict, list, str)) and not value:
            continue
        score += 1
    return score


# Creator values treated as system/import accounts rather than real Pure users.
SYSTEM_CREATORS = {"root", "atira", "sync_user", "admin", "system", ""}


def is_uploaded_by_real_user(ev: dict) -> bool:
    """
    Heuristic: entries created by a known system/import account (see
    SYSTEM_CREATORS) are treated as system/import uploads; any other
    creator is treated as a real Pure user.
    """
    creator = (ev.get("creator") or "").strip().lower()
    return creator not in SYSTEM_CREATORS


def choose_duplicate_to_keep(duplicates: list) -> dict:
    """
    Given a list of FileElectronicVersion entries that are duplicates (same
    fileName + size), choose which single entry to keep.

    Priority:
      1. Most complete metadata (highest metadata_completeness_score).
      2. Uploaded by a real Pure user rather than the root/import account.
      3. Fallback: first entry in the record's original electronicVersions order.
    """
    best_ev = None
    best_key = None
    for idx, ev in enumerate(duplicates):
        key = (
            metadata_completeness_score(ev),
            1 if is_uploaded_by_real_user(ev) else 0,
            -idx,  # earlier entries win ties (idx 0 sorts highest)
        )
        if best_key is None or key > best_key:
            best_key = key
            best_ev = ev
    return best_ev


def resolve_duplicate_file_versions(pure_record: dict) -> tuple[bool, list, list, dict]:
    """
    Find and resolve duplicate FileElectronicVersions on a record, keeping
    exactly one entry per (fileName, size) group per choose_duplicate_to_keep().

    Does NOT mutate pure_record — the caller decides whether/when to apply
    the returned list. Returns (changed, deduped_versions, removed_evs, kept_by_group):
      - changed: True if any duplicates were found and resolved
      - deduped_versions: a new electronicVersions list with duplicates removed
      - removed_evs: the FileElectronicVersion dicts that were dropped
      - kept_by_group: {(fileName, size): kept_ev} for the groups that were deduped
    """
    groups = find_duplicate_file_groups(pure_record)
    if not groups:
        return False, pure_record.get("electronicVersions", []), [], {}

    to_remove_ids = set()
    removed = []
    kept_by_group = {}
    for key, evs in groups.items():
        keeper = choose_duplicate_to_keep(evs)
        kept_by_group[key] = keeper
        for ev in evs:
            if ev is not keeper:
                to_remove_ids.add(id(ev))
                removed.append(ev)

    deduped_versions = [
        ev for ev in pure_record.get("electronicVersions", [])
        if id(ev) not in to_remove_ids
    ]
    return True, deduped_versions, removed, kept_by_group


# ---------------------------------------------------------------------------
# Matching: build lookup indices from Pure JSON
# ---------------------------------------------------------------------------

def build_pure_index(pure_items: list) -> dict:
    """
    Returns a dict with keys: by_doi, by_repo_doi, by_handle, by_dspace_uuid
    Each maps a normalised key -> Pure record dict.
    """
    by_doi         = {}
    by_repo_doi    = {}
    by_handle      = {}
    by_dspace_uuid = {}

    for item in pure_items:
        # DSpace UUID stored in Pure's electronicVersions or links
        for ev in item.get("electronicVersions", []):
            doi = ev.get("doi", "")
            if doi:
                ndoi = normalize_doi(doi)
                if "10.13025" in ndoi:
                    by_repo_doi[ndoi] = item
                elif "hdl.handle.net" in ndoi:
                    by_handle[normalize_handle(ndoi)] = item
                else:
                    by_doi[ndoi] = item

        for link in item.get("links", []):
            url = link.get("url", "")
            if not url:
                continue
            if "hdl.handle.net" in url:
                by_handle[normalize_handle(url)] = item
            elif "10.13025" in url:
                by_repo_doi[normalize_doi(url)] = item
            elif url.startswith("https://doi.org/") or url.startswith("http://doi.org/"):
                by_doi[normalize_doi(url)] = item

        # Index by DSpace UUID from identifiers list
        for id_entry in item.get("identifiers", []):
            if id_entry.get("idSource", "") == "DSpace":
                dspace_uuid = id_entry.get("value", "").strip()
                if dspace_uuid:
                    by_dspace_uuid[dspace_uuid] = item
                break

    return {
        "by_doi":         by_doi,
        "by_repo_doi":    by_repo_doi,
        "by_handle":      by_handle,
        "by_dspace_uuid": by_dspace_uuid,
    }


def find_pure_record(dspace_row: dict, pure_index: dict):
    """
    Try to match a DSpace row to a Pure record.
    Priority: DSpace UUID -> Publisher DOI -> Repository DOI -> Handle.
    Returns (pure_record | None, match_type str | None)
    """
    # 1. DSpace UUID
    dspace_uuid = dspace_row.get("uuid", "").strip()
    if dspace_uuid and dspace_uuid in pure_index["by_dspace_uuid"]:
        return pure_index["by_dspace_uuid"][dspace_uuid], "DSpace UUID"

    # 2. Publisher DOI
    pub_doi = dspace_row.get("dc.identifier.doi", "").strip()
    if pub_doi:
        ndoi = normalize_doi(pub_doi)
        if ndoi in pure_index["by_doi"]:
            return pure_index["by_doi"][ndoi], "Publisher DOI"

    # 3. Repository DOI (from dc.identifier.uri)
    for rdoi in extract_dois_from_uri(dspace_row.get("dc.identifier.uri", "")):
        nrdoi = normalize_doi(rdoi)
        if nrdoi in pure_index["by_repo_doi"]:
            return pure_index["by_repo_doi"][nrdoi], "Repository DOI"

    # 4. Handle — prefer dedicated 'handle' column, then dc.identifier.uri
    candidates = []
    handle_col = dspace_row.get("handle", "").strip()
    if handle_col:
        candidates.append(handle_col)
    candidates.extend(extract_handles_from_uri(dspace_row.get("dc.identifier.uri", "")))
    for h in candidates:
        nh = normalize_handle(h)
        if nh in pure_index["by_handle"]:
            return pure_index["by_handle"][nh], "Handle"

    return None, None


# ---------------------------------------------------------------------------
# PDF download -> Pure upload -> PUT record
# ---------------------------------------------------------------------------

def upload_pdf_to_pure(
    full_pdf_url: str,
    file_name: str,
    api_key: str,
    pure_file_upload_url: str,
    save_locally: bool,
    pdf_save_dir: str,
    session: requests.Session,
) -> dict | None:
    try:
        os.makedirs(safe_path(pdf_save_dir), exist_ok=True)
    except OSError as exc:
        print(f"    ❌ Could not create PDF save directory '{pdf_save_dir}': {exc}")
        return None

    local_path = os.path.abspath(os.path.join(pdf_save_dir, file_name))

    # Always download to disk first to ensure the complete file
    if not os.path.exists(local_path):
        try:
            src = session.get(full_pdf_url, timeout=120, allow_redirects=True)
            if src.status_code != 200:
                print(f"    ❌ PDF download failed (HTTP {src.status_code}): {full_pdf_url}")
                return None
            content_type = src.headers.get("Content-Type", "")
            if "text/html" in content_type:
                print(f"    ❌ DSpace returned HTML instead of PDF: {full_pdf_url}")
                return None
            if not is_valid_pdf(src.content):
                print(f"    ❌ Content is not a valid PDF or is under 1kb "
                      f"(first bytes: {src.content[:8]!r}, size: {len(src.content)})")
                return None
            try:
                with open(safe_path(local_path), "wb") as fh:
                    fh.write(src.content)
                print(f"    💾 PDF saved locally: {local_path} ({len(src.content):,} bytes)")
            except OSError as exc:
                print(f"    ❌ Could not write PDF to disk (invalid path/filename?): {exc}")
                print(f"       Attempted path : {local_path}")
                print(f"       Original name  : {file_name}")
                return None
        except requests.RequestException as exc:
            print(f"    ❌ PDF download error: {exc}")
            return None
    else:
        print(f"    ♻️  Using existing local file: {local_path}")

    # Upload from disk
    try:
        with open(safe_path(local_path), "rb") as fh:
            upload_resp = session.put(
                pure_file_upload_url,
                data=fh,
                headers={
                    "accept":       "application/json",
                    "api-key":      api_key,
                    "content-type": "application/pdf",
                },
                timeout=120,
            )
    except OSError as exc:
        print(f"    ❌ Could not open local PDF for upload (invalid path/filename?): {exc}")
        print(f"       Attempted path : {local_path}")
        print(f"       Original name  : {file_name}")
        return None
    except requests.RequestException as exc:
        print(f"    ❌ PDF upload error: {exc}")
        return None

    # Clean up unless --save-locally was requested
    if not save_locally and os.path.exists(safe_path(local_path)):
        try:
            os.remove(safe_path(local_path))
        except OSError as exc:
            print(f"    ⚠️  Could not remove temporary file '{local_path}': {exc}")

    if upload_resp.status_code not in (200, 201):
        print(f"    ❌ Pure file-upload failed (HTTP {upload_resp.status_code}): "
              f"{upload_resp.text[:200]}")
        return None

    try:
        return upload_resp.json()
    except ValueError:
        print("    ❌ Could not parse Pure file-upload response as JSON")
        return None
    

def build_file_electronic_version(
    upload_data: dict,
    file_name: str,
    dspace_row: dict,
) -> dict:
    """Build a FileElectronicVersion dict from a successful Pure file-upload response."""
    license_uri = resolve_license_uri(dspace_row.get("dc.rights", "").strip())
    _, embargo_active, embargo_period = resolve_embargo_and_access(dspace_row)

    access_uri = (
        "/dk/atira/pure/core/openaccesspermission/embargoed"
        if embargo_active else
        "/dk/atira/pure/core/openaccesspermission/open"
    )

    fev = {
        "typeDiscriminator": "FileElectronicVersion",
        "visibleOnPortalDate": TODAY,
        "accessType":  {"uri": access_uri},
        "licenseType": {"uri": license_uri},
        "versionType": {
            "uri": "/dk/atira/pure/researchoutput/electronicversion/versiontype/authorsversion"
        },
        "file": {
            "fileName": file_name,
            "mimeType": "application/pdf",
            "size":     upload_data.get("size", 0),
            "uploadedFile": {
                "digest":     upload_data.get("digest"),
                "digestType": upload_data.get("digestType"),
                "mimeType":   "application/pdf",
                "size":       upload_data.get("size", 0),
                "key":        upload_data.get("key"),
            },
        },
    }

    if embargo_period:
        fev["embargoPeriod"] = embargo_period

    return fev


def upload_local_pdf_to_pure(
    safe_file_name: str,
    pdf_dir: str,
    api_key: str,
    pure_file_upload_url: str,
    session: requests.Session,
) -> dict | None:
    """
    Upload a locally stored PDF to Pure's file-upload endpoint.
    safe_file_name is the decoded filename (as saved on disk).
    Falls back to URL-encoded variant if the decoded name is not found.
    Returns the upload response JSON dict, or None on any failure.
    """
    local_path = os.path.join(pdf_dir, safe_file_name)

    # If decoded filename not found, try URL-encoded variant
    if not os.path.exists(safe_path(local_path)):
        from urllib.parse import quote
        encoded_name = quote(safe_file_name, safe="")
        encoded_path = os.path.join(pdf_dir, encoded_name)
        if os.path.exists(safe_path(encoded_path)):
            print(f"    ℹ️  Decoded filename not found, using encoded: {encoded_name}")
            local_path = encoded_path
        else:
            print(f"    ❌ Local PDF not found: {local_path}")
            return None

    print(f"    📂 Reading local PDF: {local_path}")
    try:
        with open(safe_path(local_path), "rb") as fh:
            upload_resp = session.put(
                pure_file_upload_url,
                data=fh,
                headers={
                    "accept":       "application/json",
                    "api-key":      api_key,
                    "content-type": "*/*",
                },
                timeout=120,
            )
    except OSError as exc:
        print(f"    ❌ Could not open local PDF (invalid path/filename?): {exc}")
        print(f"       Attempted path : {local_path}")
        print(f"       Original name  : {safe_file_name}")
        return None
    except requests.RequestException as exc:
        print(f"    ❌ Local PDF upload error: {exc}")
        return None

    if upload_resp.status_code not in (200, 201):
        print(f"    ❌ Pure file-upload failed (HTTP {upload_resp.status_code}): "
              f"{upload_resp.text[:200]}")
        return None

    try:
        return upload_resp.json()
    except ValueError:
        print("    ❌ Could not parse Pure file-upload response as JSON")
        return None


def put_pure_record(
    pure_record: dict,
    file_ev: dict,
    api_key: str,
    base_url: str,
    session: requests.Session,
) -> tuple[bool, str, str]:
    """
    Clean the Pure record, append the FileElectronicVersion, and PUT it back.
    Captures pureId before stripping system fields.
    Returns (success bool, status_or_error str, pure_id str).
    """
    uuid    = pure_record.get("uuid")
    pure_id = str(pure_record.get("pureId", ""))  # capture before stripping

    if not uuid:
        return False, "no_uuid", pure_id

    cleaned = strip_system_fields(pure_record, top_level=True)
    cleaned = remove_nulls(cleaned)

    evs = cleaned.get("electronicVersions", [])
    if file_ev is not None:
        evs.append(file_ev)
    cleaned["electronicVersions"] = evs

    url = f"{base_url}research-outputs/{uuid}"
    try:
        resp = session.put(
            url,
            json=cleaned,
            headers={
                "accept":       "application/json",
                "content-type": "application/json",
                "api-key":      api_key,
            },
            timeout=60,
        )
        if resp.status_code in (200, 201):
            return True, str(resp.status_code), pure_id
        else:
            return False, f"{resp.status_code} - {resp.text[:300]}", pure_id
    except requests.RequestException as exc:
        return False, str(exc), pure_id


def get_file_info(
    session: requests.Session,
    base_url: str,
    uuid: str,
    expected_file_name: str = None,
) -> tuple[str, str]:
    """
    GET a research output from Pure and return (fileId, fileName) for the
    FileElectronicVersion that matches expected_file_name (compared after
    Pure-style normalization), or the first FileElectronicVersion found.
    Returns ("", "") on failure or when no FileElectronicVersion exists.
    """
    url = f"{base_url}research-outputs/{uuid}"
    try:
        resp = session.get(url, timeout=30)
        resp.raise_for_status()
    except requests.HTTPError as exc:
        print(f"    ⚠️  GET file info — HTTP {exc.response.status_code} for uuid={uuid}")
        return "", ""
    except requests.RequestException as exc:
        print(f"    ⚠️  GET file info — request failed for uuid={uuid}: {exc}")
        return "", ""

    data = resp.json()
    first_file_id   = ""
    first_file_pure_id = ""
    first_file_name = ""
    norm_expected   = pure_normalize_filename(expected_file_name) if expected_file_name else None

    for ev in data.get("electronicVersions", []):
        if ev.get("typeDiscriminator") != "FileElectronicVersion":
            continue
        file_obj  = ev.get("file", {})
        file_id   = file_obj.get("fileId", "")
        file_pure_id = file_obj.get("pureId", "")
        file_name = file_obj.get("fileName", "")
        if not first_file_id and file_id:
            first_file_id   = file_id
            first_file_pure_id = file_pure_id
            first_file_name = file_name
        if norm_expected and pure_normalize_filename(file_name) == norm_expected:
            return file_id, file_pure_id,file_name

    return first_file_id, first_file_pure_id, first_file_name


# ---------------------------------------------------------------------------
# Logging helpers
# ---------------------------------------------------------------------------

class RunLogger:
    """Writes to both stdout and a .log file simultaneously."""

    def __init__(self, log_path: str):
        self._terminal = sys.stdout
        self._file     = open(log_path, "w", encoding="utf-8")

    def write(self, msg: str):
        self._terminal.write(msg)
        self._file.write(msg)
        self._file.flush()

    def flush(self):
        self._terminal.flush()
        self._file.flush()

    def close(self):
        self._file.close()


def write_json_log(records: list, path: str):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(records, fh, indent=2, ensure_ascii=False)


def write_csv_log(records: list, path: str, fieldnames: list):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():

    # Parse command-line arguments
    parser = argparse.ArgumentParser(
        description="Download DSpace PDFs, upload to Pure, and attach as FileElectronicVersions"
    )
    parser.add_argument("--dspace-csv",       required=True,
                        help="Path to DSpace CSV export")
    parser.add_argument("--pure-json",        required=True,
                        help="Path to Pure research-outputs JSON")
    parser.add_argument("--test",             action="store_true", default=False,
                        help="Use UAT environment.")
    parser.add_argument("--temp",             action="store_true", default=False,
                        help="Use TEMP environment (uses same API key as Production).")
    parser.add_argument("--save-locally",     action="store_true", default=False,
                        help="Also save downloaded PDFs to disk (dspace source only).")
    parser.add_argument("--log-dir",          default="./pdf_upload_logs",
                        help="Directory for logs (default: ./pdf_upload_logs)")
    parser.add_argument("--dry-run",          action="store_true", default=False,
                        help="Match and report only — do not upload or PUT.")
    parser.add_argument("--skip-existing",    action="store_true", default=True,
                        help="Skip files already in Pure with same name and size (default).")
    parser.add_argument("--no-skip-existing", dest="skip_existing", action="store_false")
    parser.add_argument("--source",           choices=["dspace", "local"], default="dspace",
                        help="Where to get PDFs from: 'dspace' (default) or 'local'.")
    parser.add_argument("--pdf-dir",          default="./dspace_pdfs",
                        help="If --source dspace: directory to save PDFs locally (only with --save-locally). "
                             "If --source local: directory to read PDFs from.")
    args = parser.parse_args()

    # ---- Environment -------------------------------------------------------
    load_dotenv()
    api_key_var = "PURE_ROOT_API_KEY_TEST" if args.test else "PURE_ROOT_API_KEY"
    api_key     = os.getenv(api_key_var, "")

    if args.test:
        base_url = "https://galway-staging.elsevierpure.com/ws/api/"
    elif args.temp:
        base_url = "https://galway-test.elsevierpure.com/ws/api/"
    else:
        base_url = "https://research.universityofgalway.ie/ws/api/"

    pure_file_upload_url = f"{base_url}research-outputs/file-uploads"

    # ---- Logging setup -----------------------------------------------------
    os.makedirs(args.log_dir, exist_ok=True)
    run_log_path    = os.path.join(args.log_dir, f"run_{RUN_TS}.log")
    results_json    = os.path.join(args.log_dir, f"results_{RUN_TS}.json")
    success_csv     = os.path.join(args.log_dir, f"success_{RUN_TS}.csv")
    failed_csv      = os.path.join(args.log_dir, f"failed_{RUN_TS}.csv")
    skipped_csv     = os.path.join(args.log_dir, f"skipped_{RUN_TS}.csv")
    matched_ref_csv = os.path.join(args.log_dir, f"pdf_matched_records_{RUN_TS}.csv")

    # Open matched_ref CSV for records with PDFs immediately so rows are written as we go,
    # surviving early termination or keyboard interrupt.
    MATCHED_REF_FIELDS = [
        "dspace_uuid", "pure_uuid", "pure_id", "title",
        "dspace_file_id", "pure_file_id", "pure_file_pure_id", 
        "pure_file_name", "handle"
            ]
    matched_ref_fh     = open(matched_ref_csv, "w", newline="", encoding="utf-8")
    matched_ref_writer = csv.DictWriter(
        matched_ref_fh, fieldnames=MATCHED_REF_FIELDS, extrasaction="ignore"
    )
    matched_ref_writer.writeheader()
    matched_ref_fh.flush()

    # Open no_pdf_matched CSV for records without PDFsimmediately so rows are written as we go,
    # surviving early termination or keyboard interrupt.
    NO_PDF_MATCHED_FIELDS = [
        "dspace_uuid", "pure_uuid", "pure_id", "title", "handle"
    ]
    no_pdf_matched_csv = os.path.join(args.log_dir, f"no_pdf_matched_records_{RUN_TS}.csv")
    no_pdf_matched_fh     = open(no_pdf_matched_csv, "w", newline="", encoding="utf-8")
    no_pdf_matched_writer = csv.DictWriter(
        no_pdf_matched_fh, fieldnames=NO_PDF_MATCHED_FIELDS, extrasaction="ignore"
    )
    no_pdf_matched_writer.writeheader()
    no_pdf_matched_fh.flush()

    logger     = RunLogger(run_log_path)
    sys.stdout = logger

    # ---- Validate inputs ---------------------------------------------------
    if not api_key:
        print(f"⚠️  WARNING: {api_key_var} not set in .env — API calls will fail.")

    if args.source == "local" and not os.path.isdir(args.pdf_dir):
        print(f"❌ --pdf-dir '{args.pdf_dir}' does not exist or is not a directory (required for --source local)")
        sys.exit(1)

    for path, label in [(args.dspace_csv, "DSpace CSV"), (args.pure_json, "Pure JSON")]:
        if not os.path.isfile(path):
            print(f"❌ {label} not found: {path}")
            sys.exit(1)

    env_label = "UAT" if args.test else "TEMP" if args.temp else "PRODUCTION"
    print(f"{'='*70}")
    print(f"  PDF Upload to Pure — {RUN_TS}")
    print(f"  Environment       : {env_label}")
    print(f"  API key var       : {api_key_var}")
    print(f"  Base URL          : {base_url}")
    print(f"  DSpace CSV        : {args.dspace_csv}")
    print(f"  Pure JSON         : {args.pure_json}")
    print(f"  Source            : {args.source.upper()}")
    print(f"  PDF dir           : {args.pdf_dir}")
    if args.source == "dspace":
        print(f"  Save PDFs locally : {args.save_locally}")
    print(f"  Skip existing EVs : {args.skip_existing}")
    print(f"  Dry run           : {args.dry_run}")
    print(f"  Log dir           : {args.log_dir}")
    print(f"{'='*70}\n")

    # ---- Load data ---------------------------------------------------------
    print("Loading DSpace CSV...")
    dspace_rows = []
    with open(args.dspace_csv, "r", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            dspace_rows.append(row)
    print(f"✅ Loaded {len(dspace_rows)} DSpace rows")

    print("Loading Pure JSON...")
    with open(args.pure_json, "r", encoding="utf-8") as fh:
        pure_items = json.load(fh)
    print(f"✅ Loaded {len(pure_items)} Pure records\n")

    # ---- Build Pure index --------------------------------------------------
    print("Building Pure lookup index...")
    pure_index = build_pure_index(pure_items)
    print(f"  by_doi         : {len(pure_index['by_doi'])} entries")
    print(f"  by_repo_doi    : {len(pure_index['by_repo_doi'])} entries")
    print(f"  by_handle      : {len(pure_index['by_handle'])} entries")
    print(f"  by_dspace_uuid : {len(pure_index['by_dspace_uuid'])} entries\n")

    # ---- Filter to rows with a PDF path ------------------------------------
    rows_in_publications = [
        r for r in dspace_rows
        if "Publications" in r.get("collection_names", "")
    ]
    rows_with_pdf = [
        r for r in rows_in_publications
        if r.get("pdf_handle_paths", "").strip()
    ]
    rows_without_pdf = [
        r for r in rows_in_publications
        if not r.get("pdf_handle_paths", "").strip()
    ]
    print(f"DSpace rows in Publications collection  : {len(rows_in_publications)} / {len(dspace_rows)}")
    print(f"DSpace rows with pdf_handle_paths       : {len(rows_with_pdf)} / {len(rows_in_publications)}")
    print(f"DSpace rows without pdf_handle_paths    : {len(rows_without_pdf)} / {len(rows_in_publications)}\n")

    print("Matching no-PDF rows to Pure records...")
    for row in rows_without_pdf:
        pure_record, match_type = find_pure_record(row, pure_index)
        if pure_record is None:
            continue
        handle_str = row.get("handle", "").strip()
        if not handle_str:
            uri_handles = extract_handles_from_uri(row.get("dc.identifier.uri", ""))
            handle_str  = uri_handles[0] if uri_handles else ""
        no_pdf_matched_writer.writerow({
            "dspace_uuid": row.get("uuid", "").strip(),
            "pure_uuid":   pure_record.get("uuid", ""),
            "pure_id":     str(pure_record.get("pureId", "")),
            "title":       row.get("dc.title", "").strip(),
            "handle":      f"https://hdl.handle.net/{handle_str}" if handle_str and not handle_str.startswith("http") else handle_str,
        })
    no_pdf_matched_fh.flush()
    print(f"✅ No-PDF matched records written to: {no_pdf_matched_csv}\n")

    # ---- Session -----------------------------------------------------------
    session = requests.Session()
    session.headers.update({"api-key": api_key})

    # ---- Process -----------------------------------------------------------
    results      = []
    success_rows = []
    failed_rows  = []
    skipped_rows = []

    counters = {
        "total":            len(rows_with_pdf),
        "no_match":         0,
        "already_has_fev":  0,
        "metadata_updated": 0,
        "duplicates_removed": 0,
        "pdf_fail":         0,
        "put_fail":         0,
        "success":          0,
        "dry_run_would":    0,
    }

    start_time = time.time()
    last_dspace_request = 0.0  # epoch zero so first request is never delayed

    print(f"Processing {len(rows_with_pdf)} DSpace rows with PDFs...")
    print(f"{'='*70}\n")

    for i, row in enumerate(tqdm(rows_with_pdf, desc="Uploading PDFs", unit="record"), start=1):
        # pdf_handle_paths may contain multiple semicolon-separated paths
        pdf_path    = row.get("pdf_handle_paths", "").strip()
        title       = row.get("dc.title", "").strip()
        dspace_uuid = row.get("uuid", "").strip()

        # Parse all PDF paths — semicolon-separated
        pdf_paths = [p.strip() for p in pdf_path.split(";") if p.strip()]
        pdf_links_raw = row.get("pdf_links", "").strip()
        pdf_links     = [p.strip() for p in pdf_links_raw.split(";") if p.strip()]
        pdf_link_map  = dict(zip(pdf_paths, pdf_links))

        # Prefer the dedicated 'handle' column; fall back to dc.identifier.uri
        handle_str = row.get("handle", "").strip()
        if not handle_str:
            uri_handles = extract_handles_from_uri(row.get("dc.identifier.uri", ""))
            handle_str  = uri_handles[0] if uri_handles else ""

        print(f"\n[{i}/{len(rows_with_pdf)}] {title[:70]}")
        print(f"  DSpace UUID    : {dspace_uuid}")
        print(f"  Handle         : {handle_str}")
        print(f"  DSpace file ID : {pdf_path}")
        if args.source == "dspace":
            print(f"  PDF URLs       : {'; '.join(pdf_links)}")

        entry = {
            "dspace_uuid":    dspace_uuid,
            "title":          title,
            # Prefix handle for the log if not already a full URL
            "handle":         f"https://hdl.handle.net/{handle_str}" if handle_str and not handle_str.startswith("http") else handle_str,
            "dspace_file_id": pdf_path,   # original semicolon-separated paths, as-is
            "pdf_url":        "; ".join(pdf_links) if args.source == "dspace" else "",
            "pure_uuid":      None,
            "pure_id":        None,
            "match_type":     None,
            "upload_key":     None,
            "pure_file_id":   None,
            "pure_file_pure_id": None,
            "pure_file_name": None,
            "status":         None,
            "detail":         None,
            "timestamp":      datetime.now().isoformat(),
        }

        # 1. Match to Pure record
        pure_record, match_type = find_pure_record(row, pure_index)
        if pure_record is None:
            print(f"  ⚠️  No Pure record matched — skipping")
            counters["no_match"] += 1
            entry["status"] = "no_match"
            entry["detail"] = "No Pure record found for this DSpace row"
            results.append(entry)
            skipped_rows.append(entry)
            continue

        pure_uuid           = pure_record.get("uuid", "")
        entry["pure_uuid"]  = pure_uuid
        entry["pure_id"]    = str(pure_record.get("pureId", ""))
        entry["match_type"] = match_type
        print(f"  ✅ Matched Pure record ({match_type}): {pure_uuid}  pureId: {entry['pure_id']}")

        # Steps 2-6: process each PDF path individually
        any_success             = False
        any_fail                = False
        metadata_update_handled = False
        uploaded_keys           = []
        skipped_paths           = []
        failed_paths            = []
        failed_upload_paths     = []
        failed_put_paths        = []
        all_pure_file_ids       = []
        all_pure_file_pure_ids  = []
        all_pure_file_names     = []

        for single_path in pdf_paths:
            file_name      = single_path.rstrip("/").split("/")[-1]  # original encoded
            safe_file_name = unquote(file_name)                       # decoded — used as Pure fileName

            # Sanitize for local disk (replaces chars illegal on Windows, e.g. |)
            disk_file_name = sanitize_filename(safe_file_name)
            if disk_file_name != safe_file_name:
                print(f"    ⚠️  Filename contains characters illegal on this OS — sanitized for disk use.")
                print(f"       Pure / DSpace name : {safe_file_name}")
                print(f"       Disk name          : {disk_file_name}")

            full_pdf_url = pdf_link_map.get(single_path, "") if args.source == "dspace" else ""

            # Pre-download from DSpace for local saving (always, regardless of skip check)
            if args.source == "dspace" and args.save_locally and not args.dry_run:
                local_path = os.path.abspath(os.path.join(args.pdf_dir, disk_file_name))
                try:
                    os.makedirs(safe_path(os.path.dirname(local_path)), exist_ok=True)
                except OSError as exc:
                    print(f"    ❌ Could not create directory for pre-download: {exc} — skipping pre-save")
                else:
                    if not os.path.exists(local_path):
                        elapsed_since_last = time.time() - last_dspace_request
                        if elapsed_since_last < 5:
                            wait = 5 - elapsed_since_last
                            print(f"    ⏳ Rate limiting — waiting {wait:.1f}s...")
                            time.sleep(wait)
                        print(f"    💾 Pre-downloading for local save: {disk_file_name}")
                        try:
                            presrc = session.get(full_pdf_url, timeout=120, allow_redirects=True)
                            if presrc.status_code == 200 and "text/html" not in presrc.headers.get("Content-Type", ""):
                                if not is_valid_pdf(presrc.content):
                                    print(f"    ❌ Pre-download is not a valid PDF or under 1kb — skipping save")
                                else:
                                    try:
                                        with open(safe_path(local_path), "wb") as fh:
                                            fh.write(presrc.content)
                                        last_dspace_request = time.time()
                                        print(f"    💾 Saved: {local_path} ({len(presrc.content):,} bytes)")
                                    except OSError as exc:
                                        print(f"    ❌ Could not write pre-downloaded PDF to disk: {exc}")
                                        print(f"       Attempted path   : {local_path}")
                                        print(f"       Original filename: {safe_file_name}")
                            else:
                                print(f"    ⚠️  Pre-download failed (HTTP {presrc.status_code})")
                        except requests.RequestException as exc:
                            print(f"    ⚠️  Pre-download error: {exc}")
                    else:
                        print(f"    ℹ️  Already saved locally: {local_path}")

            print(f"  📄 Processing file: {safe_file_name}")

            # 2. Skip check — compare against Pure-normalized filename
            if args.skip_existing:
                norm_safe_file_name = pure_normalize_filename(safe_file_name)
                # Find the actual stored filename in Pure that normalizes to the same value
                matched_ev_name = next(
                    (ev.get("file", {}).get("fileName", "")
                     for ev in pure_record.get("electronicVersions", [])
                     if ev.get("typeDiscriminator") == "FileElectronicVersion"
                     and pure_normalize_filename(ev.get("file", {}).get("fileName", "")) == norm_safe_file_name),
                    None,
                )
                if matched_ev_name is not None:
                    print(f"    ℹ️  Same filename found in Pure — checking size...")
                    known_size = None
                    if args.source == "local":
                        lp = os.path.join(args.pdf_dir, disk_file_name)
                        if os.path.exists(lp):
                            known_size = os.path.getsize(lp)
                    else:
                        lp = os.path.join(args.pdf_dir, disk_file_name)
                        if args.save_locally and os.path.exists(lp):
                            known_size = os.path.getsize(lp)
                        else:
                            try:
                                head = session.head(full_pdf_url, timeout=10)
                                cl = head.headers.get("Content-Length")
                                known_size = int(cl) if cl else None
                            except Exception:
                                known_size = None

                    if already_has_file_ev(pure_record, file_name=matched_ev_name, file_size=known_size):
                        # Find the matching FileEV using the actual stored name
                        matching_ev = next(
                            (ev for ev in pure_record.get("electronicVersions", [])
                             if ev.get("typeDiscriminator") == "FileElectronicVersion"
                             and ev.get("file", {}).get("fileName") == matched_ev_name),
                            None
                        )

                        # Look for duplicate FileElectronicVersions anywhere on this
                        # record (same fileName + size). If the file we matched on
                        # is one of a duplicate group, treat the chosen "keeper" as
                        # the entry to check/update metadata on, not just whichever
                        # duplicate happened to be found first above.
                        dedup_changed, deduped_versions, removed_evs, kept_by_group = \
                            resolve_duplicate_file_versions(pure_record)

                        target_ev = matching_ev
                        if matching_ev is not None:
                            for (dup_name, _dup_size), keeper in kept_by_group.items():
                                if dup_name == matched_ev_name:
                                    target_ev = keeper
                                    break

                        meta_changed, updated_ev = needs_metadata_update(target_ev, row) if target_ev else (False, None)

                        if (meta_changed or (dedup_changed and removed_evs)) and not args.dry_run:
                            if dedup_changed and removed_evs:
                                removed_names = ", ".join(
                                    f"{ev.get('file', {}).get('fileName', '?')} "
                                    f"(pureId={ev.get('pureId', '?')}, fileId={ev.get('file', {}).get('fileId', '?')})"
                                    for ev in removed_evs
                                )
                                print(f"    🧹 Removing {len(removed_evs)} duplicate file(s): {removed_names}")
                            if meta_changed:
                                print(f"    ℹ️  Same filename and size — updating metadata")

                            # Build the new electronicVersions list from the already
                            # deduped list (losers excluded), swapping in updated_ev
                            # for the exact surviving object (identity-based, never
                            # by filename — a record can hold several entries that
                            # share a fileName without being the same association).
                            updated_record = dict(pure_record)
                            updated_record["electronicVersions"] = [
                                updated_ev if (meta_changed and ev is target_ev) else ev
                                for ev in deduped_versions
                            ]
                            success, detail, pure_id = put_pure_record(
                                pure_record=updated_record,
                                file_ev=None,
                                api_key=api_key,
                                base_url=base_url,
                                session=session,
                            )
                            entry["pure_id"] = pure_id
                            if success:
                                # Reflect the change in the in-memory record so any
                                # remaining paths for this same row see the corrected
                                # (deduped / metadata-updated) state, not stale data.
                                pure_record["electronicVersions"] = updated_record["electronicVersions"]

                                detail_parts = []
                                if meta_changed:
                                    print(f"    ✅ Metadata updated ({detail})")
                                    detail_parts.append(f"Metadata updated for: {matched_ev_name}")
                                    counters["metadata_updated"] += 1
                                if dedup_changed and removed_evs:
                                    print(f"    ✅ Duplicates removed ({detail})")
                                    detail_parts.append(
                                        f"Removed {len(removed_evs)} duplicate copy/copies of: {matched_ev_name}"
                                    )
                                    counters["duplicates_removed"] += len(removed_evs)

                                p_file_id, p_file_pure_id, p_file_name = get_file_info(
                                    session, base_url, pure_uuid,
                                    expected_file_name=matched_ev_name,
                                )
                                entry["pure_file_id"]      = p_file_id
                                entry["pure_file_pure_id"] = p_file_pure_id
                                entry["pure_file_name"]    = p_file_name
                                if p_file_id:
                                    print(f"    🔎 File in Pure — fileId: {p_file_id}  fileName: {p_file_name}")
                                entry["status"] = "metadata_updated"
                                entry["detail"] = "; ".join(detail_parts) if detail_parts else "No change"
                                results.append(entry)
                                success_rows.append(entry)
                                matched_ref_writer.writerow(entry)
                                matched_ref_fh.flush()
                                metadata_update_handled = True
                            else:
                                print(f"    ❌ Metadata/duplicate update PUT failed: {detail}")
                                counters["put_fail"] += 1
                                entry["status"] = "put_failed"
                                entry["detail"] = f"Metadata/duplicate update failed: {detail}"
                                results.append(entry)
                                failed_rows.append(entry)
                                metadata_update_handled = True
                            continue
                        elif (meta_changed or (dedup_changed and removed_evs)) and args.dry_run:
                            if meta_changed:
                                print(f"    🔍 DRY RUN — metadata would be updated")
                            if dedup_changed and removed_evs:
                                removed_names = ", ".join(
                                    ev.get("file", {}).get("fileName", "?") for ev in removed_evs
                                )
                                print(f"    🔍 DRY RUN — would remove {len(removed_evs)} duplicate file(s): {removed_names}")
                            counters["already_has_fev"] += 1
                            skipped_paths.append(safe_file_name)
                        else:
                            print(f"    ℹ️  Same filename and size, metadata up to date — skipping")
                            if matching_ev:
                                entry["pure_file_id"]      = matching_ev.get("file", {}).get("fileId", "")
                                entry["pure_file_pure_id"] = matching_ev.get("file", {}).get("pureId", "") if matching_ev else ""
                                entry["pure_file_name"]    = matching_ev.get("file", {}).get("fileName", "")
                                all_pure_file_ids.append(entry["pure_file_id"])
                                all_pure_file_pure_ids.append(str(entry["pure_file_pure_id"]))
                                all_pure_file_names.append(entry["pure_file_name"])
                            counters["already_has_fev"] += 1
                            skipped_paths.append(safe_file_name)
                        continue

            # 3. Dry run
            if args.dry_run:
                print(f"    🔍 DRY RUN — would upload {safe_file_name}")
                counters["dry_run_would"] += 1
                continue

            # 4. Upload PDF to Pure
            print(f"    📎 Uploading: {safe_file_name}")
            if args.source == "local":
                upload_data = upload_local_pdf_to_pure(
                    safe_file_name=disk_file_name,
                    pdf_dir=args.pdf_dir,
                    api_key=api_key,
                    pure_file_upload_url=pure_file_upload_url,
                    session=session,
                )
            else:
                upload_data = upload_pdf_to_pure(
                    full_pdf_url=full_pdf_url,
                    file_name=disk_file_name,       # sanitized name used on disk
                    api_key=api_key,
                    pure_file_upload_url=pure_file_upload_url,
                    save_locally=args.save_locally,
                    pdf_save_dir=args.pdf_dir,
                    session=session,
                )
                last_dspace_request = time.time()

            if upload_data is None:
                print(f"    ❌ Upload failed for: {safe_file_name}")
                failed_upload_paths.append(safe_file_name)
                failed_paths.append(safe_file_name)
                any_fail = True
                continue

            upload_key = upload_data.get("key", "")
            uploaded_keys.append(upload_key)
            print(f"    ✅ Uploaded — key: {upload_key}")

            # 5. Build FileElectronicVersion
            # Always use safe_file_name (original decoded) as the Pure-side fileName
            # so it matches the DSpace original exactly, even if the disk copy is sanitized.
            file_ev = build_file_electronic_version(upload_data, safe_file_name, row)

            # 6. PUT the Pure record immediately (within 2-hour window)
            print(f"    📤 PUTting Pure record {pure_uuid}...")
            success, detail, pure_id = put_pure_record(
                pure_record=pure_record,
                file_ev=file_ev,
                api_key=api_key,
                base_url=base_url,
                session=session,
            )
            entry["pure_id"] = pure_id

            if success:
                print(f"    ✅ PUT succeeded ({detail})")
                # GET the updated record to retrieve the assigned fileId/fileName
                p_file_id, p_file_pure_id, p_file_name = get_file_info(
                    session, base_url, pure_uuid,
                    expected_file_name=safe_file_name,
                )
                if p_file_id:
                    all_pure_file_ids.append(p_file_id)
                if p_file_pure_id:
                    all_pure_file_pure_ids.append(str(p_file_pure_id))
                if p_file_name:
                    all_pure_file_names.append(p_file_name)
                if p_file_id:
                    print(f"    🔎 File in Pure — fileId: {p_file_id}  fileName: {p_file_name}")
                any_success = True
                # Update in-memory record so subsequent files in this row
                # see the newly added FileEV for the skip check
                pure_record.setdefault("electronicVersions", []).append(file_ev)
            else:
                print(f"    ❌ PUT failed: {detail}")
                failed_put_paths.append(safe_file_name)
                failed_paths.append(safe_file_name)
                any_fail = True

        if metadata_update_handled and not uploaded_keys and not skipped_paths and not failed_paths:
            continue

        # Consolidate entry status across all paths for this row
        entry["upload_key"]         = "; ".join(uploaded_keys)          if uploaded_keys          else None
        entry["pure_file_id"]       = "; ".join(all_pure_file_ids)      if all_pure_file_ids      else None
        entry["pure_file_pure_id"]  = "; ".join(all_pure_file_pure_ids) if all_pure_file_pure_ids else None
        entry["pure_file_name"]     = "; ".join(all_pure_file_names)    if all_pure_file_names    else None
        entry["dspace_file_id"]     = "; ".join(pdf_paths)

        # A record belongs in matched_ref_csv only when every DSpace PDF has a
        # confirmed Pure file — i.e. all paths either uploaded successfully or
        # were already present. Partial or fully-failed rows must not appear there.
        all_dspace_files_in_pure = (
            len(all_pure_file_ids) > 0
            and len(all_pure_file_ids) == len(pdf_paths)
        )

        if args.dry_run:
            entry["status"] = "dry_run"
            entry["detail"] = f"Would upload: {'; '.join(unquote(p.rstrip('/').split('/')[-1]) for p in pdf_paths)}"
            results.append(entry)
            continue

        if not uploaded_keys and not skipped_paths:
            if failed_put_paths and not failed_upload_paths:
                # Files reached Pure temp storage but PUT to record failed
                entry["status"] = "put_failed"
                entry["detail"] = f"PUT failed: {'; '.join(failed_put_paths)}"
                results.append(entry)
                failed_rows.append(entry)
                counters["put_fail"] += 1
            else:
                # Every path failed at the upload stage
                entry["status"] = "pdf_upload_failed"
                entry["detail"] = f"Failed: {'; '.join(failed_upload_paths)}"
                results.append(entry)
                failed_rows.append(entry)
                counters["pdf_fail"] += 1
        elif any_fail and any_success:
            entry["status"] = "partial_success"
            entry["detail"] = (
                f"Uploaded: {'; '.join(uploaded_keys)}"
                + (f" | Upload failed: {'; '.join(failed_upload_paths)}" if failed_upload_paths else "")
                + (f" | PUT failed: {'; '.join(failed_put_paths)}" if failed_put_paths else "")
            )
            results.append(entry)
            success_rows.append(entry)
            counters["success"] += 1
        elif not uploaded_keys and skipped_paths:
            entry["status"] = "skipped_existing_fev"
            entry["detail"] = f"All files already exist in Pure with same name and size: {'; '.join(skipped_paths)}"
            results.append(entry)
            skipped_rows.append(entry)
            counters["already_has_fev"] += 1
        else:
            entry["status"] = "success"
            entry["detail"] = f"Uploaded {len(uploaded_keys)} file(s)"
            results.append(entry)
            success_rows.append(entry)
            counters["success"] += 1

        if all_dspace_files_in_pure:
            matched_ref_writer.writerow(entry)
            matched_ref_fh.flush()

    # ---- Summary & logs ----------------------------------------------------
    matched_ref_fh.close()   # flush and close the continuously-written CSV
    no_pdf_matched_fh.close()

    elapsed = time.time() - start_time
    h, rem  = divmod(int(elapsed), 3600)
    m, s    = divmod(rem, 60)

    print(f"\n{'='*70}")
    print(f"SUMMARY — {RUN_TS}")
    print(f"{'='*70}")
    print(f"  Total rows with PDF           : {counters['total']}")
    print(f"  No Pure match                 : {counters['no_match']}")
    print(f"  Already had FileEV            : {counters['already_has_fev']}")
    print(f"  Only file metadata updated    : {counters['metadata_updated']}")
    print(f"  Duplicate FileEVs removed     : {counters['duplicates_removed']}")
    print(f"  PDF upload failed             : {counters['pdf_fail']}")
    print(f"  PUT failed                    : {counters['put_fail']}")
    print(f"  Successfully uploaded         : {counters['success']}")
    if args.dry_run:
        print(f"  Would have processed          : {counters['dry_run_would']}")
    print(f"  Time elapsed                  : {h:02d}:{m:02d}:{s:02d}")
    print(f"{'='*70}")

    write_json_log(results, results_json)
    print(f"\n  Full log     : {run_log_path}")
    print(f"  Results JSON : {results_json}")
    print(f"  Matched records with PDFs : {matched_ref_csv}")
    print(f"  Matched records without PDFs : {no_pdf_matched_csv}")

    # All-columns CSV fields (used for success / failed / skipped CSVs)
    csv_fields = [
        "dspace_uuid", "title", "handle", "dspace_file_id", "pdf_url",
        "pure_uuid", "pure_id", "match_type", "upload_key",
        "pure_file_id", "pure_file_pure_id", "pure_file_name",
        "status", "detail", "timestamp",
    ]

    if success_rows:
        write_csv_log(success_rows, success_csv, csv_fields)
        print(f"  Success CSV  : {success_csv}")

    if failed_rows:
        write_csv_log(failed_rows, failed_csv, csv_fields)
        print(f"  Failed CSV   : {failed_csv}")

    if skipped_rows:
        write_csv_log(skipped_rows, skipped_csv, csv_fields)
        print(f"  Skipped CSV  : {skipped_csv}")

    logger.close()
    sys.stdout = logger._terminal


if __name__ == "__main__":
    main()