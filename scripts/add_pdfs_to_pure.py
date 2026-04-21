#!/usr/bin/env python3
"""
upload_pdfs_to_pure.py

For each record in the Pure JSON that has a matching DSpace row with a PDF path:
  1. Downloads the PDF from DSpace
  2. Uploads it to Pure's file-upload endpoint (temporary file, valid for 2 hours)
  3. Immediately PUTs the Pure record with a FileElectronicVersion referencing the upload
  4. Logs all outcomes

Records are processed one at a time to ensure each uploaded file is linked before
the 2-hour Pure expiry window.

Usage:
    python upload_pdfs_to_pure.py --dspace-csv <path> --pure-json <path> [options]

.env file must contain:
    PURE_ROOT_API_KEY_TEST=your_key_here   (UAT)
    PURE_ROOT_API_KEY=your_key_here        (Production)

Options:
    --test / --no-test   Use UAT (default) or Production environment
    --save-locally       Also save downloaded PDFs to disk
    --pdf-dir            Directory for locally saved PDFs (default: ./downloaded_dspace_pdfs)
    --log-dir            Directory for logs (default: ./pdf_upload_logs)
    --dry-run            Match records and report what would be done, but do not upload/PUT
    --skip-existing /
    --no-skip-existing   Skip Pure records that already have a FileElectronicVersion (default: skip)
"""

import os
import re
import sys
import csv
import json
import time
import argparse
import requests
from datetime import datetime, date
from dotenv import load_dotenv
from tqdm import tqdm


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
    key = LICENSE_MAP.get(rights_str.strip(), "cc_by_nc_nd") if rights_str else "cc_by_nc_nd"
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


def already_has_file_ev(pure_record: dict) -> bool:
    """Return True if the record already has at least one FileElectronicVersion."""
    for ev in pure_record.get("electronicVersions", []):
        if ev.get("typeDiscriminator") == "FileElectronicVersion":
            return True
    return False


# ---------------------------------------------------------------------------
# Matching: build lookup indices from Pure JSON
# ---------------------------------------------------------------------------

def build_pure_index(pure_items: list) -> dict:
    """
    Returns a dict with keys: by_doi, by_repo_doi, by_handle, by_uuid
    Each maps a normalised key -> Pure record dict.
    """
    by_doi      = {}
    by_repo_doi = {}
    by_handle   = {}
    by_uuid     = {}

    for item in pure_items:
        uuid = item.get("uuid")
        if uuid:
            by_uuid[uuid] = item

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

    return {"by_doi": by_doi, "by_repo_doi": by_repo_doi,
            "by_handle": by_handle, "by_uuid": by_uuid}


def find_pure_record(dspace_row: dict, pure_index: dict):
    """
    Try to match a DSpace row to a Pure record.
    Priority: Publisher DOI -> Repository DOI -> Handle.
    Returns (pure_record | None, match_type str | None)
    """
    # 1. Publisher DOI
    pub_doi = dspace_row.get("dc.identifier.doi", "").strip()
    if pub_doi:
        ndoi = normalize_doi(pub_doi)
        if ndoi in pure_index["by_doi"]:
            return pure_index["by_doi"][ndoi], "Publisher DOI"

    # 2. Repository DOI (from dc.identifier.uri)
    for rdoi in extract_dois_from_uri(dspace_row.get("dc.identifier.uri", "")):
        nrdoi = normalize_doi(rdoi)
        if nrdoi in pure_index["by_repo_doi"]:
            return pure_index["by_repo_doi"][nrdoi], "Repository DOI"

    # 3. Handle — prefer dedicated 'handle' column, then dc.identifier.uri
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
    """
    Download PDF from DSpace and upload to Pure's file-upload endpoint.
    Returns the upload response JSON dict, or None on any failure.
    """
    try:
        src = session.get(full_pdf_url, stream=True, timeout=60)
        if src.status_code != 200:
            print(f"    ❌ PDF download failed (HTTP {src.status_code}): {full_pdf_url}")
            return None

        if save_locally:
            local_path = os.path.join(pdf_save_dir, file_name)
            if os.path.exists(local_path):
                print(f"    ♻️  Reusing locally saved PDF: {local_path}")
            else:
                os.makedirs(pdf_save_dir, exist_ok=True)
                with open(local_path, "wb") as fh:
                    for chunk in src.iter_content(chunk_size=8192):
                        fh.write(chunk)
                print(f"    💾 PDF saved locally: {local_path}")
            upload_content = open(local_path, "rb")
        else:
            upload_content = src.iter_content(chunk_size=8192)

        upload_resp = session.put(
            pure_file_upload_url,
            data=upload_content,
            headers={
                "accept":       "application/json",
                "api-key":      api_key,
                "content-type": "*/*",
            },
            timeout=120,
        )

        if save_locally and hasattr(upload_content, "close"):
            upload_content.close()

    except requests.RequestException as exc:
        print(f"    ❌ PDF upload request error: {exc}")
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
        "accessType":  {"uri": access_uri},
        "licenseType": {"uri": license_uri},
        "file": {
            "fileName": file_name,
            "mimeType": upload_data.get("mimeType", "*/*"),
            "size":     upload_data.get("size", 0),
            "uploadedFile": {
                "digest":     upload_data.get("digest"),
                "digestType": upload_data.get("digestType"),
                "mimeType":   upload_data.get("mimeType", "*/*"),
                "size":       upload_data.get("size", 0),
                "key":        upload_data.get("key"),
            },
        },
    }

    if embargo_period:
        fev["embargoPeriod"] = embargo_period

    return fev


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
    parser = argparse.ArgumentParser(
        description="Download DSpace PDFs, upload to Pure, and attach as FileElectronicVersions"
    )
    parser.add_argument("--dspace-csv",       required=True,
                        help="Path to DSpace CSV export")
    parser.add_argument("--pure-json",        required=True,
                        help="Path to Pure research-outputs JSON")
    parser.add_argument("--test",             action="store_true", default=True,
                        help="Use UAT environment (default).")
    parser.add_argument("--no-test",          dest="test", action="store_false",
                        help="Use Production environment.")
    parser.add_argument("--save-locally",     action="store_true", default=False,
                        help="Also save downloaded PDFs to disk.")
    parser.add_argument("--pdf-dir",          default="./downloaded_dspace_pdfs",
                        help="Directory for locally saved PDFs (default: ./downloaded_dspace_pdfs)")
    parser.add_argument("--log-dir",          default="./pdf_upload_logs",
                        help="Directory for logs (default: ./pdf_upload_logs)")
    parser.add_argument("--dry-run",          action="store_true", default=False,
                        help="Match and report only — do not upload or PUT.")
    parser.add_argument("--skip-existing",    action="store_true", default=True,
                        help="Skip Pure records that already have a FileElectronicVersion (default).")
    parser.add_argument("--no-skip-existing", dest="skip_existing", action="store_false")
    args = parser.parse_args()

    # ---- Environment -------------------------------------------------------
    load_dotenv()
    api_key_var = "PURE_ROOT_API_KEY_TEST" if args.test else "PURE_ROOT_API_KEY"
    api_key     = os.getenv(api_key_var, "")

    base_url = (
        "https://galway-staging.elsevierpure.com/ws/api/"
        if args.test else
        "https://research.universityofgalway.ie/ws/api/"
    )
    dspace_bitstream_base = (
        "https://galway.dspace7-test.openrepository.com/bitstreams"
        if args.test else
        "https://researchrepository.universityofgalway.ie/bitstreams"
    )
    pure_file_upload_url = f"{base_url}research-outputs/file-uploads"

    # ---- Logging setup -----------------------------------------------------
    os.makedirs(args.log_dir, exist_ok=True)
    run_log_path = os.path.join(args.log_dir, f"run_{RUN_TS}.log")
    results_json = os.path.join(args.log_dir, f"results_{RUN_TS}.json")
    success_csv  = os.path.join(args.log_dir, f"success_{RUN_TS}.csv")
    failed_csv   = os.path.join(args.log_dir, f"failed_{RUN_TS}.csv")
    skipped_csv  = os.path.join(args.log_dir, f"skipped_{RUN_TS}.csv")

    logger     = RunLogger(run_log_path)
    sys.stdout = logger

    # ---- Validate inputs ---------------------------------------------------
    if not api_key:
        print(f"⚠️  WARNING: {api_key_var} not set in .env — API calls will fail.")

    for path, label in [(args.dspace_csv, "DSpace CSV"), (args.pure_json, "Pure JSON")]:
        if not os.path.isfile(path):
            print(f"❌ {label} not found: {path}")
            sys.exit(1)

    env_label = "UAT" if args.test else "PRODUCTION"
    print(f"{'='*70}")
    print(f"  PDF Upload to Pure — {RUN_TS}")
    print(f"  Environment       : {env_label}")
    print(f"  API key var       : {api_key_var}")
    print(f"  Base URL          : {base_url}")
    print(f"  DSpace CSV        : {args.dspace_csv}")
    print(f"  Pure JSON         : {args.pure_json}")
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
    print(f"  by_doi      : {len(pure_index['by_doi'])} entries")
    print(f"  by_repo_doi : {len(pure_index['by_repo_doi'])} entries")
    print(f"  by_handle   : {len(pure_index['by_handle'])} entries")
    print(f"  by_uuid     : {len(pure_index['by_uuid'])} entries\n")

    # ---- Filter to rows with a PDF path ------------------------------------
    rows_in_publications = [
        r for r in dspace_rows
        if "Publications" in r.get("collection_names", "")
    ]
    rows_with_pdf = [
        r for r in rows_in_publications
        if r.get("pdf_handle_paths", "").strip()
    ]
    print(f"DSpace rows in Publications collection  : {len(rows_in_publications)} / {len(dspace_rows)}")
    print(f"DSpace rows with pdf_handle_paths       : {len(rows_with_pdf)} / {len(rows_in_publications)}\n")

    # ---- Session -----------------------------------------------------------
    session = requests.Session()

    # ---- Process -----------------------------------------------------------
    results      = []
    success_rows = []
    failed_rows  = []
    skipped_rows = []

    counters = {
        "total":           len(rows_with_pdf),
        "no_match":        0,
        "already_has_fev": 0,
        "pdf_fail":        0,
        "put_fail":        0,
        "success":         0,
        "dry_run_would":   0,
    }

    start_time = time.time()

    print(f"Processing {len(rows_with_pdf)} DSpace rows with PDFs...")
    print(f"{'='*70}\n")

    for i, row in enumerate(tqdm(rows_with_pdf, desc="Uploading PDFs", unit="record"), start=1):
        # pdf_handle_paths is already in /10379/handle/seq/filename.pdf format
        pdf_path    = row.get("pdf_handle_paths", "").strip()
        title       = row.get("dc.title", "").strip()
        dspace_uuid = row.get("uuid", "").strip()

        # Prefer the dedicated 'handle' column; fall back to dc.identifier.uri
        handle_str = row.get("handle", "").strip()
        if not handle_str:
            uri_handles = extract_handles_from_uri(row.get("dc.identifier.uri", ""))
            handle_str  = uri_handles[0] if uri_handles else ""

        full_pdf_url = f"{dspace_bitstream_base}{pdf_path}"
        file_name    = pdf_path.rstrip("/").split("/")[-1]

        print(f"\n[{i}/{len(rows_with_pdf)}] {title[:70]}")
        print(f"  DSpace UUID    : {dspace_uuid}")
        print(f"  Handle         : {handle_str}")
        print(f"  DSpace file ID : {pdf_path}")
        print(f"  PDF URL        : {full_pdf_url}")

        entry = {
            "dspace_uuid":    dspace_uuid,
            "title":          title,
            "handle":         handle_str,
            "dspace_file_id": pdf_path,         # e.g. /10379/4728/1/file.pdf
            "pdf_url":        full_pdf_url,
            "pure_uuid":      None,
            "pure_id":        None,
            "match_type":     None,
            "upload_key":     None,
            "status":         None,
            "detail":         None,
            "timestamp":      datetime.now().isoformat(),
        }

        # 1a. Match to Pure record
        pure_record, match_type = find_pure_record(row, pure_index)
        if pure_record is None:
            print(f"  ⚠️  No Pure record matched — skipping")
            counters["no_match"] += 1
            entry["status"] = "no_match"
            entry["detail"] = "No Pure record found for this DSpace row"
            results.append(entry)
            skipped_rows.append(entry)
            continue

        pure_uuid          = pure_record.get("uuid", "")
        entry["pure_uuid"] = pure_uuid
        entry["pure_id"]   = str(pure_record.get("pureId", ""))
        entry["match_type"] = match_type
        print(f"  ✅ Matched Pure record ({match_type}): {pure_uuid}  pureId: {entry['pure_id']}")

        # 1b. Save PDF locally if requested, regardless of existing FileEV
        if args.save_locally and pdf_path:
            local_path = os.path.join(args.pdf_dir, file_name)
            if not os.path.exists(local_path):
                print(f"  💾 Saving PDF locally: {file_name}")
                try:
                    src = session.get(full_pdf_url, stream=True, timeout=60)
                    if src.status_code == 200:
                        os.makedirs(args.pdf_dir, exist_ok=True)
                        with open(local_path, "wb") as fh:
                            for chunk in src.iter_content(chunk_size=8192):
                                fh.write(chunk)
                        print(f"  💾 Saved: {local_path}")
                    else:
                        print(f"  ⚠️  Could not download PDF for local save (HTTP {src.status_code})")
                except requests.RequestException as exc:
                    print(f"  ⚠️  Local save failed: {exc}")
            else:
                print(f"  ℹ️  PDF already saved locally, skipping download: {local_path}")

        # 2. Skip if already has FileElectronicVersion
        if args.skip_existing and already_has_file_ev(pure_record):
            print(f"  ℹ️  Pure record already has FileElectronicVersion — skipping")
            counters["already_has_fev"] += 1
            entry["status"] = "skipped_existing_fev"
            entry["detail"] = "Record already has FileElectronicVersion"
            results.append(entry)
            skipped_rows.append(entry)
            continue

        # 3. Dry run
        if args.dry_run:
            print(f"  🔍 DRY RUN — would upload {file_name} and PUT {pure_uuid}")
            counters["dry_run_would"] += 1
            entry["status"] = "dry_run"
            entry["detail"] = f"Would upload {file_name}"
            results.append(entry)
            continue

        # 4. Upload PDF to Pure
        print(f"  📎 Uploading PDF: {file_name}")
        upload_data = upload_pdf_to_pure(
            full_pdf_url=full_pdf_url,
            file_name=file_name,
            api_key=api_key,
            pure_file_upload_url=pure_file_upload_url,
            save_locally=args.save_locally,
            pdf_save_dir=args.pdf_dir,
            session=session,
        )

        if upload_data is None:
            counters["pdf_fail"] += 1
            entry["status"] = "pdf_upload_failed"
            entry["detail"] = "PDF download or Pure file-upload failed"
            results.append(entry)
            failed_rows.append(entry)
            continue

        upload_key          = upload_data.get("key", "")
        entry["upload_key"] = upload_key
        print(f"  ✅ PDF uploaded — key: {upload_key}")

        # 5. Build FileElectronicVersion
        file_ev = build_file_electronic_version(upload_data, file_name, row)

        # 6. PUT the Pure record immediately (within 2-hour window)
        print(f"  📤 PUTting Pure record {pure_uuid}...")
        success, detail, pure_id = put_pure_record(
            pure_record=pure_record,
            file_ev=file_ev,
            api_key=api_key,
            base_url=base_url,
            session=session,
        )
        entry["pure_id"] = pure_id   # value captured inside function, before stripping

        if success:
            print(f"  ✅ PUT succeeded ({detail})")
            counters["success"] += 1
            entry["status"] = "success"
            entry["detail"] = detail
            results.append(entry)
            success_rows.append(entry)
        else:
            print(f"  ❌ PUT failed: {detail}")
            counters["put_fail"] += 1
            entry["status"] = "put_failed"
            entry["detail"] = detail
            results.append(entry)
            failed_rows.append(entry)

    # ---- Summary & logs ----------------------------------------------------
    elapsed = time.time() - start_time
    h, rem  = divmod(int(elapsed), 3600)
    m, s    = divmod(rem, 60)

    print(f"\n{'='*70}")
    print(f"SUMMARY — {RUN_TS}")
    print(f"{'='*70}")
    print(f"  Total rows with PDF     : {counters['total']}")
    print(f"  No Pure match           : {counters['no_match']}")
    print(f"  Already had FileEV      : {counters['already_has_fev']}")
    print(f"  PDF upload failed       : {counters['pdf_fail']}")
    print(f"  PUT failed              : {counters['put_fail']}")
    print(f"  Successfully uploaded   : {counters['success']}")
    if args.dry_run:
        print(f"  Would have processed    : {counters['dry_run_would']}")
    print(f"  Time elapsed            : {h:02d}:{m:02d}:{s:02d}")
    print(f"{'='*70}")

    write_json_log(results, results_json)
    print(f"\n  Full log     : {run_log_path}")
    print(f"  Results JSON : {results_json}")

    # All-columns CSV fields (used for success / failed / skipped CSVs)
    csv_fields = [
        "dspace_uuid", "title", "handle", "dspace_file_id", "pdf_url",
        "pure_uuid", "pure_id", "match_type", "upload_key",
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

    # Matched records reference CSV (all records matched to a Pure record, with or without a file)
    matched_ref_csv  = os.path.join(args.log_dir, f"matched_records_{RUN_TS}.csv")
    matched_ref_rows = [r for r in results if r.get("pure_uuid")]
    if matched_ref_rows:
        write_csv_log(
            matched_ref_rows,
            matched_ref_csv,
            ["dspace_uuid", "pure_uuid", "pure_id", "handle", "dspace_file_id"],
        )
        print(f"  Matched refs  : {matched_ref_csv}")

    logger.close()
    sys.stdout = logger._terminal


if __name__ == "__main__":
    main()