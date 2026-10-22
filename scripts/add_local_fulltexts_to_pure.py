#!/usr/bin/env python3
"""
add_local_fulltexts_to_pure.py

Uploads full texts that are missing from DSpace, but were found elsewhere and
saved to local storage, to the matching Pure records.

Input:
  * the project folder (--root), holding at its top level
      - the metadata sheets, DSpace-Fulltexts-<from>-<to>_with_fulltext.csv
        (only *.csv files are read; .xlsx and other files are ignored), with
        the columns id (DSpace UUID), dc.identifier.doi[] (publisher DOI),
        Filename, Correct (result of the manual check), Full text Source and
        WORKING Full text link (used only for the Sci-Hub check)
      - the download folders, e.g. Full-Text-Downloads-1001-1999-CJ. A sheet's
        files are looked for in every folder whose number range overlaps the
        sheet's range (prefixes and suffixes such as Atmire / CJ / Ella do not
        matter), and in every folder without a number range (e.g.
        Full-Text-Downloads-Ella-misc). Folders are searched recursively.
  * the DSpace export CSV (used for the title, Handle, collection, type and
    embargo of each item, needed by the record matching)
  * the Pure research-outputs JSON export

For every sheet row whose Correct value is Y / Yes / True (case-insensitive,
surrounding spaces ignored), and whose full-text source is not Sci-Hub
(see --allow-sci-hub):
  1. Finds the local file named in Filename in the sheet's download folders.
     .doc / .docx files are converted to PDF with LibreOffice; every file
     uploaded must pass the PDF check (> 1 KB, "%PDF" header in the first 1 KB).
  2. Matches the row to a Pure record -- DSpace UUID first, publisher DOI as a
     back-up (same rules as add_pdfs_to_pure.py, see find_pure_record).
  3. Re-reads the record from Pure (unless --no-refresh-from-pure).
  4. Skips the file when the record already has a file with
       - the same name and the same size, or
       - a different name but exactly the same size,
     and logs it; otherwise uploads it to Pure (temporary upload, valid for
     2 hours) and immediately PUTs the record with a new FileElectronicVersion:
     Accepted author manuscript, CC BY, Open access.
     Files already on the record are left as they are (their licence, access
     type, version etc. are not changed).
  5. Logs every row with its outcome.

Usage:
    python add_local_fulltexts_to_pure.py --root <project folder> \\
        --dspace-csv <path> --pure-json <path> [options]

.env file must contain:
    PURE_ROOT_API_KEY_TEST=your_key_here   (UAT)
    PURE_ROOT_API_KEY=your_key_here        (Production & TEMP)

Helper functions marked "from add_pdfs_to_pure.py" are copies of that script's
functions and must stay identical to them.
"""


import os
import re
import sys
import csv
import json
import time
import html
import shutil
import hashlib
import tempfile
import subprocess
import unicodedata
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

# From add_pdfs_to_pure.py (identical to match_records.py).
DOI_REGEX    = re.compile(r'^(?:https?:/{1,2})?(?:(?:dx\.)?doi\.org/|doi\s*:?\s*|:)?(10\.\S+)$', re.IGNORECASE)
HANDLE_REGEX = re.compile(r'^(?:(?:https?://)?(?:www\.|hdl\.)?handle\.net/)?(10379/\S+?)/*$', re.IGNORECASE)
_MULTI_VALUE_SEPARATOR = re.compile(r"\s+;\s*|\s*;\s+")

# DSpace bulk-edit exports ("column[]" headers) separate multiple values with "||".
_DSPACE_BULK_SEPARATOR = "||"

PUBLICATIONS_COLLECTION = "publications"
EXCLUDED_DSPACE_TYPES = {"dataset", "doctoral thesis", "master thesis"}

# Metadata of every file uploaded by this script.
NEW_FILE_LICENSE_URI = "/dk/atira/pure/core/document/licenses/cc_by"
NEW_FILE_ACCESS_URI  = "/dk/atira/pure/core/openaccesspermission/open"
NEW_FILE_VERSION_URI = "/dk/atira/pure/researchoutput/electronicversion/versiontype/authorsversion"

# Values of the "Correct" column that mean "the file was checked and is correct".
CORRECT_VALUES = {"y", "yes", "true"}
# Values that explicitly mean "not correct" (used to detect conflicting rows);
# the column may carry a comment after the N ("N similar but not same").
NOT_CORRECT_RE = re.compile(r"^(n|no|false)\b", re.IGNORECASE)

# Sci-Hub as a full-text source ("Sci-Hub", "SciHub", "sci hub", any hyphen
# character, sci-hub.* links). Such rows are skipped unless --allow-sci-hub.
SCI_HUB_RE = re.compile(r"sci[\s\-\u2010-\u2015]?hub", re.IGNORECASE)

# File types that are converted to PDF before upload.
CONVERTIBLE_EXTENSIONS = {".doc", ".docx"}

# From add_pdfs_to_pure.py.
SYSTEM_FIELDS = {
    "pureId", "createdBy", "createdDate", "modifiedBy", "modifiedDate",
    "portalUrl", "prettyUrlIdentifiers", "previousUuids", "version",
    "systemName", "systemModified", "current",
}
NESTED_SYSTEM_FIELDS = {"pureId", "systemModified", "current"}
ENTITY_DIGIT_RUN_RE = re.compile(r'_\d{2,6}_')

PROD_BASE_URL = "https://research.universityofgalway.ie/ws/api/"
UAT_BASE_URL  = "https://galway-staging.elsevierpure.com/ws/api/"
TEMP_BASE_URL = "https://galway-test.elsevierpure.com/ws/api/"


# ---------------------------------------------------------------------------
# Helpers copied from add_pdfs_to_pure.py
# ---------------------------------------------------------------------------

def safe_path(path: str) -> str:
    if sys.platform == "win32":
        abs_path = os.path.abspath(path)
        if not abs_path.startswith("\\\\?\\"):
            return "\\\\?\\" + abs_path
        return abs_path
    return path


def pdf_problem(content: bytes) -> str | None:
    """
    Why the downloaded content is not an acceptable PDF, or None if it is.
    A PDF must be larger than 1 KB and contain the "%PDF" header within its
    first 1 KB -- PDF readers accept a header that is not at the very start
    (e.g. after a leading newline).
    """
    if not content:
        return "empty file (0 bytes) — the file needs to be fixed in DSpace"
    if len(content) <= 1024:
        return f"file is under 1 KB ({len(content)} bytes)"
    if b"%PDF" not in content[:1024]:
        return f"no %PDF header in the first 1 KB (first bytes: {content[:8]!r})"
    return None


def normalize_doi(value: str) -> str:
    """Same as match_records.normalize_doi: https://doi.org/<doi>, lower case, trailing full stops removed."""
    if not isinstance(value, str):
        return value
    match = DOI_REGEX.match(value.strip().lower())
    return f"https://doi.org/{match.group(1).rstrip('.')}" if match else value


def is_valid_doi(value) -> bool:
    """True if value is a DOI already normalised by normalize_doi()."""
    return isinstance(value, str) and value.startswith("https://doi.org/10.")


def parse_doi_field(raw: str) -> list:
    """All DOIs in a (possibly multi-valued) dc.identifier.doi cell, normalised, unique, in order."""
    dois = []
    for piece in _MULTI_VALUE_SEPARATOR.split((raw or "").strip()):
        doi = normalize_doi(piece.strip())
        if is_valid_doi(doi) and doi not in dois:
            dois.append(doi)
    return dois


def normalize_handle(value: str) -> str:
    if not isinstance(value, str):
        return value
    match = HANDLE_REGEX.match(value.strip().lower())
    return f"http://hdl.handle.net/{match.group(1)}" if match else value


def is_handle_url(value) -> bool:
    """True if value is a Handle URL (same rule as match_records.py)."""
    if not isinstance(value, str):
        return False
    lowered = value.lower()
    if "hdl.handle.net" in lowered:
        return True
    if "handle.net" not in lowered:
        return False
    return str(normalize_handle(value)).startswith("http://hdl.handle.net/")


def extract_dois_from_uri(uri_str: str) -> list:
    if not uri_str:
        return []
    result = []
    for u in uri_str.split(";"):
        u = u.strip().lower()
        m = DOI_REGEX.match(u)
        if m:
            result.append(f"https://doi.org/{m.group(1).rstrip('.')}")
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


# ---------------------------------------------------------------------------
# File-name comparison -- from add_pdfs_to_pure.py (same_file_name and the
# helpers it uses, identical there and in patch_records.py)
# ---------------------------------------------------------------------------

def _dedup_clean_name(name: str) -> str:
    """HTML-entity decoding + NFKC (as clean_dspace_filename)."""
    if not name:
        return ""
    return unicodedata.normalize("NFKC", html.unescape(name))


def _dedup_strip_accents(name: str) -> str:
    """Decompose accented characters and drop the combining marks ("é" -> "e")."""
    decomposed = unicodedata.normalize("NFKD", name)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def _dedup_drop_accented_letters(name: str) -> str:
    """Drop every accented letter entirely ("Méliacin" -> "Mliacin")."""
    decomposed = unicodedata.normalize("NFKD", name)
    out = []
    for c in decomposed:
        if unicodedata.combining(c):
            if out:
                out.pop()   # the base letter the mark belongs to
            continue
        out.append(c)
    return "".join(out)


def _dedup_pure_punctuation(name: str) -> str:
    """Pure replaces characters that are not alphanumeric, '-', '_', '.' or space with '_'."""
    return re.sub(r'[^\w.\- ]', '_', name)


def file_name_key(name: str) -> str:
    """Loose comparison key for a file name (check 1, exact match)."""
    step = _dedup_clean_name(name)
    step = _dedup_strip_accents(step)
    step = _dedup_pure_punctuation(step)
    return step.lower()


def accented_letter_count(name: str) -> int:
    """Number of accented letters in a name (after entity decoding)."""
    decomposed = unicodedata.normalize("NFKD", _dedup_clean_name(name))
    return sum(1 for c in decomposed if unicodedata.combining(c))


def is_garbled_copy_of(garbled: str, clean: str) -> bool:
    """
    True if `garbled` is `clean` with its accented letters turned into
    "_NNN_" runs (check 2). Pure leaves "_NNN_" both for a combining mark
    ("Me&#769;liacin" -> "Me_769_liacin", base letter kept) and for a
    precomposed letter ("M&#233;liacin" -> "M_233_liacin", letter gone), so
    both forms are compared. Plausibility: `clean` must have at least as many
    accented letters as `garbled` has "_NNN_" runs (each run replaces one
    accented letter), so an ordinary number in a name ("Report_2024_.pdf")
    is never read as garbling of an unrelated file without accents.
    """
    if not garbled or not clean:
        return False
    garbled_clean = _dedup_clean_name(garbled)
    runs = len(ENTITY_DIGIT_RUN_RE.findall(garbled_clean))
    if runs == 0:
        return False
    if accented_letter_count(clean) < runs:
        return False
    garbled_key = _dedup_pure_punctuation(
        ENTITY_DIGIT_RUN_RE.sub("", _dedup_strip_accents(garbled_clean))
    ).lower()
    clean_clean = _dedup_clean_name(clean)
    candidates = {
        _dedup_pure_punctuation(_dedup_strip_accents(clean_clean)).lower(),
        _dedup_pure_punctuation(_dedup_drop_accented_letters(clean_clean)).lower(),
    }
    return garbled_key in candidates


def same_file_name(name_a: str, name_b: str) -> bool:
    """True if two file names name the same file (exact or garbled match)."""
    if not name_a or not name_b:
        return False
    if file_name_key(name_a) == file_name_key(name_b):
        return True
    return is_garbled_copy_of(name_a, name_b) or is_garbled_copy_of(name_b, name_a)


def is_garbled_file_name(name: str) -> bool:
    """True if a name still carries literal "&#NNN;" markup. (A "_NNN_" run on
    its own is not proof of garbling; it is only judged against a sibling in
    is_garbled_copy_of.)"""
    return bool(name) and _dedup_clean_name(name) != unicodedata.normalize("NFKC", name)


def known_file_size(ev: dict):
    """The file's size as an int, or None if unknown (missing, null, -1, not a number)."""
    size = (ev.get("file") or {}).get("size")
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        return None
    return size


# ---------------------------------------------------------------------------
# Title comparison -- identical to match_records.py ("identical title" test)
# ---------------------------------------------------------------------------
# Punctuation set of match_records.py (same characters), used by strip_subtitle_from_title.
PUNC = set('!"#$%&\'()*+,-./0123456789:;<=>?@[\\]^_{|}~£©®¿‐–—€™')


def strip_subtitle_from_title(title, subtitle):
    """
    If title ends with subtitle (case-insensitive, punctuation-ignored),
    strip it from the title, including any preceding colon (and optional space).
    Returns the cleaned title string (original register/punctuation preserved).
    """
    if not title or not subtitle:
        return title

    def strip_punc(s):
        return "".join(ch for ch in s.lower() if ch not in PUNC and not ch.isspace())

    title_clean = strip_punc(title)
    sub_clean = strip_punc(subtitle)

    if not sub_clean or not title_clean.endswith(sub_clean):
        return title

    # Find how many original chars of `title` correspond to the subtitle suffix.
    # Walk backwards through title matching against sub_clean in reverse.
    sub_rev = sub_clean[::-1]
    matched = 0
    i = len(title) - 1
    for target_ch in sub_rev:
        while i >= 0:
            ch = title[i]
            i -= 1
            if ch.lower() not in PUNC and not ch.isspace():
                if ch.lower() == target_ch:
                    matched += 1
                    break
                else:
                    return title  # mismatch — safety exit
    # i now points just before the subtitle portion
    cut = i + 1  # index in original title where subtitle begins (approx)

    # Walk back over any whitespace then an optional colon (and its preceding space)
    trimmed = title[:cut].rstrip()
    if trimmed.endswith(":"):
        trimmed = trimmed[:-1].rstrip()

    return trimmed if trimmed else title


def full_title_key(title, subtitle):
    """
    Title + subtitle as one comparison key for the "identical title" test (an
    embedded copy of the subtitle is not counted twice). Case, punctuation and
    symbols are ignored, but -- unlike normalize_for_comparison -- digits are
    kept, so "An Reiviú 2015" and "An Reiviú 2024", or "Part 1" and "Part 2",
    are not identical.
    """
    title = (title or "").strip()
    subtitle = (subtitle or "").strip()
    if subtitle:
        title = strip_subtitle_from_title(title, subtitle)
    text = unicodedata.normalize("NFKC", f"{title} {subtitle}").lower()
    text = "".join(" " if unicodedata.category(ch)[0] in ("P", "S") else ch for ch in text)
    return " ".join(text.split())


def dspace_full_title_key(dspace_row: dict) -> str:
    """dc.title + subtitle (dc.title.subtitle, else dc.title.alternative), as in match_records.py."""
    subtitle = (dspace_row.get("dc.title.subtitle") or dspace_row.get("dc.title.alternative") or "").strip()
    return full_title_key((dspace_row.get("dc.title") or "").strip(), subtitle)


def pure_full_title_key(item: dict) -> str:
    """Pure title + subTitle."""
    return full_title_key(((item.get("title") or {}).get("value") or "").strip(),
                          ((item.get("subTitle") or {}).get("value") or "").strip())


def _pure_repository_ids(item: dict) -> set:
    """Normalised repository Handles and repository DOIs (10.13025) carried by a Pure record."""
    ids = set()
    for link in item.get("links") or []:
        url = (link or {}).get("url", "") if isinstance(link, dict) else ""
        if is_handle_url(url):
            ids.add(normalize_handle(url))
        elif "10.13025" in url:
            ids.add(normalize_doi(url))
    for ev in item.get("electronicVersions") or []:
        doi = (ev or {}).get("doi") if isinstance(ev, dict) else None
        if not doi:
            continue
        if is_handle_url(doi):
            ids.add(normalize_handle(doi))
        elif "10.13025" in doi:
            ids.add(normalize_doi(doi))
    return ids


def _dspace_uuids_of(item: dict) -> list:
    """All DSpace UUIDs (idSource "DSpace") of a Pure record, lower case."""
    uuids = []
    for id_entry in item.get("identifiers") or []:
        if isinstance(id_entry, dict) and id_entry.get("idSource") == "DSpace":
            value = str(id_entry.get("value") or "").strip().lower()
            if value and value not in uuids:
                uuids.append(value)
    return uuids


def build_pure_index(pure_items: list) -> dict:
    """
    Lookup indexes over the Pure records, as in match_records.py. Each maps a
    normalised key -> LIST of Pure records (several records can share a key).

      by_dspace_uuid  every DSpace UUID of every record (lower case)
      by_doi, by_repo_doi, by_handle
                      ONLY records without a DSpace UUID: a record that is
                      already linked to a DSpace item is matched solely through
                      that item's UUID, never by DOI or Handle.
    """
    by_doi         = defaultdict(list)
    by_repo_doi    = defaultdict(list)
    by_handle      = defaultdict(list)
    by_dspace_uuid = defaultdict(list)

    def add(index, key, item):
        if key and all(item is not existing for existing in index[key]):
            index[key].append(item)

    for item in pure_items:
        if not isinstance(item, dict):
            continue
        linked_uuids = _dspace_uuids_of(item)
        if linked_uuids:
            for dspace_uuid in linked_uuids:
                add(by_dspace_uuid, dspace_uuid, item)
            continue

        for ev in item.get("electronicVersions") or []:
            if not isinstance(ev, dict):
                continue
            doi = ev.get("doi") or ""
            if not doi:
                continue
            if is_handle_url(doi):
                add(by_handle, normalize_handle(doi), item)
                continue
            ndoi = normalize_doi(doi)
            if not is_valid_doi(ndoi):
                continue
            add(by_repo_doi if "10.13025" in ndoi else by_doi, ndoi, item)

        for link in item.get("links") or []:
            if not isinstance(link, dict):
                continue
            url = link.get("url") or ""
            if not url:
                continue
            if is_handle_url(url):
                add(by_handle, normalize_handle(url), item)
            elif "doi.org" in url.lower():
                ndoi = normalize_doi(url)
                if is_valid_doi(ndoi):
                    add(by_repo_doi if "10.13025" in ndoi else by_doi, ndoi, item)

    return {
        "by_doi":         by_doi,
        "by_repo_doi":    by_repo_doi,
        "by_handle":      by_handle,
        "by_dspace_uuid": by_dspace_uuid,
    }


def find_pure_record(dspace_row: dict, pure_index: dict):
    """
    Match a DSpace row to a Pure record by identifiers, in this order:

      0. DSpace UUID (case-insensitive). A Pure record that carries a DSpace
         UUID is matched ONLY through it.
      Pure records WITHOUT a DSpace UUID:
      1. Handle(s) -- the 'handle' column, then dc.identifier.uri
      2. Repository DOI(s) -- every 10.13025 DOI in dc.identifier.uri or dc.identifier.doi
      3. Publisher DOI(s) -- every non-10.13025 DOI in dc.identifier.doi, and
         ONLY together with an identical title: title + subtitle on both sides,
         case and punctuation ignored, numbers kept (full_title_key, as in
         match_records.py).

    The first step that finds anything decides. If it finds two or more
    DIFFERENT Pure records, the match is ambiguous: no record is returned.

    Returns (pure_record | None, match_type | None, candidates list):
      - match:      (record, match_type, [record])
      - ambiguous:  (None, "Ambiguous (<step>)", [candidates])
      - no match:   (None, None, []) -- or (None, <reason>, []) when a publisher
                    DOI matched but the title did not.
    """
    def unique(found):
        result = []
        for item in found:
            if all(item is not u for u in result):
                result.append(item)
        return result

    def decide(found, match_type):
        found = unique(found)
        if len(found) == 1:
            return found[0], match_type, found
        return None, f"Ambiguous ({match_type})", found

    # 0. DSpace UUID
    dspace_uuid = (dspace_row.get("uuid") or "").strip().lower()
    if dspace_uuid and pure_index["by_dspace_uuid"].get(dspace_uuid):
        return decide(pure_index["by_dspace_uuid"][dspace_uuid], "DSpace UUID")

    # 1. Handle(s) -- the 'handle' column first, then dc.identifier.uri
    handles = []
    handle_col = (dspace_row.get("handle") or "").strip()
    if handle_col:
        handles.append(normalize_handle(handle_col))
    for h in extract_handles_from_uri(dspace_row.get("dc.identifier.uri", "")):
        if normalize_handle(h) not in handles:
            handles.append(normalize_handle(h))
    found = [item for h in handles for item in pure_index["by_handle"].get(h, [])]
    if found:
        return decide(found, "Handle")

    field_dois = parse_doi_field(dspace_row.get("dc.identifier.doi", ""))

    # 2. Repository DOI(s)
    repo_dois = [d for d in extract_dois_from_uri(dspace_row.get("dc.identifier.uri", "")) if "10.13025" in d]
    repo_dois += [d for d in field_dois if "10.13025" in d and d not in repo_dois]
    found = [item for doi in repo_dois for item in pure_index["by_repo_doi"].get(doi, [])]
    if found:
        return decide(found, "Repository DOI")

    # 3. Publisher DOI(s) + identical title
    found = unique([item for doi in field_dois if "10.13025" not in doi
                    for item in pure_index["by_doi"].get(doi, [])])
    if found:
        row_title = dspace_full_title_key(dspace_row)
        same_title = [item for item in found if row_title and pure_full_title_key(item) == row_title]
        # A record carrying a repository Handle / DOI, none of which is this
        # row's, belongs to another DSpace item (e.g. a separately deposited
        # item with the same title) -- not this row's record.
        row_repo_ids = set(handles) | set(repo_dois)
        other_item = [item for item in same_title
                      if _pure_repository_ids(item) and not (_pure_repository_ids(item) & row_repo_ids)]
        same_title = [item for item in same_title if all(item is not o for o in other_item)]
        if same_title:
            return decide(same_title, "Publisher DOI + title")
        return None, (f"publisher DOI matches {len(found)} Pure record(s) "
                      f"({'; '.join(i.get('uuid', '') for i in found)}) but the title differs"), []

    return None, None, []


def fetch_pure_record(uuid: str, base_url: str, session) -> tuple:
    """GET a research output from Pure. Returns (record | None, error | None)."""
    try:
        resp = session.get(f"{base_url}research-outputs/{uuid}", headers={"accept": "application/json"}, timeout=60)
    except requests.RequestException as exc:
        return None, str(exc)
    if resp.status_code != 200:
        return None, f"{resp.status_code} - {resp.text[:300]}"
    try:
        return resp.json(), None
    except ValueError as exc:
        return None, f"invalid JSON: {exc}"


# ---------------------------------------------------------------------------
# Metadata sheets
# ---------------------------------------------------------------------------

def is_correct(value: str) -> bool:
    """True for Y / Yes / True (any case, surrounding whitespace ignored)."""
    return (value or "").strip().lower() in CORRECT_VALUES


def is_explicitly_not_correct(value: str) -> bool:
    """True for N / No / False, also followed by a comment ("N similar but not same")."""
    return bool(NOT_CORRECT_RE.match((value or "").strip()))


def parse_sheet_doi_cell(raw: str) -> list:
    """DOIs in a sheet cell: values separated by "||" (DSpace bulk-edit style) or " ; "."""
    dois = []
    for part in (raw or "").split(_DSPACE_BULK_SEPARATOR):
        for doi in parse_doi_field(part):
            if doi not in dois:
                dois.append(doi)
    return dois


def _read_text(path: str) -> str:
    """Read a sheet as text: UTF-8 (with or without BOM), else Windows-1252."""
    with open(path, "rb") as fh:
        raw = fh.read()
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("cp1252")


def _header_index(header: list) -> dict:
    """Map lower-cased, stripped column names to their FIRST position."""
    index = {}
    for pos, name in enumerate(header):
        key = (name or "").strip().lower()
        if key and key not in index:
            index[key] = pos
    return index


def read_sheet(path: str) -> tuple:
    """
    Read one metadata sheet (comma, semicolon or tab separated; detected).
    Returns (rows, error). rows are dicts with: sheet, line, uuid, dois,
    doi_raw, filename, correct, source, link. error is a message when the file is not a
    metadata sheet (required columns missing) or cannot be read.
    """
    try:
        text = _read_text(path)
    except OSError as exc:
        return [], f"cannot read: {exc}"
    sample = text[:20000]
    try:
        delimiter = csv.Sniffer().sniff(sample, delimiters=",;\t").delimiter
    except csv.Error:
        delimiter = ","
    reader = csv.reader(text.splitlines(), delimiter=delimiter)
    try:
        header = next(reader)
    except StopIteration:
        return [], "empty file"
    cols = _header_index(header)
    doi_col = None
    for name in ("dc.identifier.doi[]", "dc.identifier.doi"):
        if name in cols:
            doi_col = cols[name]
            break
    if doi_col is None:
        doi_col = next((pos for name, pos in cols.items() if name.startswith("dc.identifier.doi")), None)
    missing = [c for c in ("id", "filename", "correct") if c not in cols]
    if missing:
        return [], f"not a metadata sheet (missing column(s): {', '.join(missing)})"

    def cell(values, pos):
        return values[pos] if pos is not None and pos < len(values) else ""

    rows = []
    for line_no, values in enumerate(reader, start=2):
        if not any((v or "").strip() for v in values):
            continue
        doi_raw = cell(values, doi_col)
        rows.append({
            "source":   cell(values, cols.get("full text source")).strip(),
            "link":     cell(values, cols.get("working full text link")).strip(),
            "sheet":    path,
            "line":     line_no,
            "uuid":     cell(values, cols["id"]).strip().lower(),
            "doi_raw":  doi_raw.strip(),
            "dois":     parse_sheet_doi_cell(doi_raw),
            "filename": cell(values, cols["filename"]),
            "correct":  cell(values, cols["correct"]),
        })
    return rows, None


# A number range in a sheet or folder name: "0001-1000" in
# "DSpace-Fulltexts-0001-1000_with_fulltext.csv" or
# "Full-Text-Downloads-CJ-2000-3000". The last range in the name counts.
NUMBER_RANGE_RE = re.compile(r"(?<!\d)(\d{1,6})-(\d{1,6})(?!\d)")


def parse_number_range(name: str):
    """(start, end) of the last number range in a name, or None."""
    found = [(int(a), int(b)) for a, b in NUMBER_RANGE_RE.findall(name or "")]
    found = [(a, b) for a, b in found if a <= b]
    return found[-1] if found else None


def ranges_overlap(a: tuple, b: tuple) -> bool:
    return a[0] <= b[1] and b[0] <= a[1]


def discover_project(root: str, exclude_dirs: list) -> tuple:
    """
    Top level of the project folder:
      sheets   [(path, range | None)]  every *.csv file
      folders  [(path, range | None)]  every sub-folder, except hidden ones
                                       and folders that are, or contain, one
                                       of exclude_dirs (log / converted dirs,
                                       e.g. ./logs holding the default log dir)
    """
    excluded = {os.path.abspath(d) for d in exclude_dirs if d}

    def is_excluded(path):
        folder = os.path.abspath(path)
        return any(e == folder or e.startswith(folder + os.sep) for e in excluded)

    sheets, folders = [], []
    for name in sorted(os.listdir(root)):
        path = os.path.join(root, name)
        if os.path.isdir(path):
            if name.startswith(".") or is_excluded(path):
                continue
            folders.append((path, parse_number_range(name)))
        elif name.lower().endswith(".csv") and not name.startswith("~$"):
            sheets.append((path, parse_number_range(name)))
    return sheets, folders


def folders_for_sheets(sheets: list, folders: list) -> tuple:
    """
    Which download folders belong to which sheet.
    Returns (sheet_folders, warnings): sheet path -> [folder paths] (overlapping
    ranges first, then the folders without a range), and messages about
    folders that belong to no sheet or to several sheets.
    """
    shared = [path for path, rng in folders if rng is None]
    sheet_folders, warnings = {}, []
    for sheet, srng in sheets:
        if srng is None:
            continue
        own = [path for path, frng in folders if frng is not None and ranges_overlap(srng, frng)]
        sheet_folders[sheet] = own + shared
    ranged_sheets = [(p, r) for p, r in sheets if r is not None]
    for path, frng in folders:
        if frng is None:
            continue
        owners = [os.path.basename(p) for p, r in ranged_sheets if ranges_overlap(r, frng)]
        if not owners:
            warnings.append(f"folder {os.path.basename(path)} matches no sheet — not searched")
        elif len(owners) > 1:
            warnings.append(f"folder {os.path.basename(path)} matches several sheets "
                            f"({', '.join(owners)}) — searched for each of them")
    return sheet_folders, warnings


def sheet_file_key(filename: str) -> str:
    """Comparison key of a Filename cell, used to spot repeated rows."""
    candidates = local_name_candidates(filename)
    return candidates[-1].lower() if candidates else ""


# ---------------------------------------------------------------------------
# Local files
# ---------------------------------------------------------------------------

def build_file_index(files_dir: str, exclude_dirs: list) -> tuple:
    """
    Index every file under files_dir (recursive) by its name.
    Returns (by_name, by_lower_name): name -> [paths].
    """
    excluded = [os.path.abspath(d) for d in exclude_dirs if d]
    by_name = defaultdict(list)
    by_lower = defaultdict(list)
    for root, dirs, files in os.walk(files_dir):
        root_abs = os.path.abspath(root)
        dirs[:] = sorted(d for d in dirs
                         if not any(os.path.join(root_abs, d) == e for e in excluded))
        for name in sorted(files):
            path = os.path.join(root, name)
            by_name[name].append(path)
            by_lower[name.lower()].append(path)
    return by_name, by_lower


def merge_file_indexes(indexes: list) -> tuple:
    """One (by_name, by_lower) index over several folder indexes."""
    by_name, by_lower = defaultdict(list), defaultdict(list)
    for names, lowers in indexes:
        for key, paths in names.items():
            by_name[key].extend(paths)
        for key, paths in lowers.items():
            by_lower[key].extend(paths)
    return by_name, by_lower


def local_name_candidates(filename: str) -> list:
    """
    The file names a Filename cell can stand for, most literal first:
    the cell as written (trimmed), its last path component (so a stray
    leading "/" or a folder prefix is ignored) and the percent-decoded
    form of that ("%20" -> space).
    """
    raw = (filename or "").strip()
    if not raw:
        return []
    candidates = []
    base = raw.replace("\\", "/").rstrip("/").split("/")[-1].strip()
    for name in (raw, base, unquote(base), unquote(base).strip()):
        if name and "/" not in name and "\\" not in name and name not in candidates:
            candidates.append(name)
    return candidates


def sha256_of(path: str) -> str:
    digest = hashlib.sha256()
    with open(safe_path(path), "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_local_file(filename: str, by_name: dict, by_lower: dict) -> tuple:
    """
    Find the local file for a Filename cell.
    Returns (path | None, problem | None, note).
    Exact name first, then case-insensitive. A name found in several folders
    is accepted only when all copies have identical content.
    """
    candidates = local_name_candidates(filename)
    if not candidates:
        return None, "no file name in the sheet", ""
    paths, note = [], ""
    for name in candidates:
        if by_name.get(name):
            paths = by_name[name]
            break
    if not paths:
        for name in candidates:
            if by_lower.get(name.lower()):
                paths = by_lower[name.lower()]
                note = "file name matched ignoring case"
                break
    if not paths:
        return None, f"local file not found: {filename.strip()!r}", ""
    if len(paths) > 1:
        try:
            digests = {sha256_of(p) for p in paths}
        except OSError as exc:
            return None, f"cannot read local file copies: {exc}", ""
        if len(digests) > 1:
            return None, f"{len(paths)} different local files with this name: {'; '.join(paths)}", ""
        note = (note + "; " if note else "") + f"{len(paths)} identical copies, using {paths[0]}"
    return paths[0], None, note


def convert_to_pdf(source: str, converted_dir: str, soffice: str | None, timeout: int = 300) -> tuple:
    """
    Convert a .doc / .docx file to PDF with LibreOffice.
    The PDF is kept in converted_dir/<file stem>.pdf and reused on later runs
    (as long as it is newer than the source), so a re-run uploads -- and
    compares against Pure -- exactly the same bytes.
    Returns (pdf_path | None, problem | None, reused bool).
    """
    stem = os.path.splitext(os.path.basename(source))[0]
    target = os.path.join(converted_dir, stem + ".pdf")
    if os.path.exists(target) and os.path.getmtime(target) >= os.path.getmtime(source):
        return target, None, True
    if not soffice:
        return None, "LibreOffice (soffice) not found — cannot convert to PDF (use --soffice)", False
    os.makedirs(converted_dir, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="lo_convert_") as tmp:
        profile = os.path.join(tmp, "profile")
        outdir = os.path.join(tmp, "out")
        os.makedirs(outdir)
        cmd = [soffice, f"-env:UserInstallation=file://{os.path.abspath(profile)}",
               "--headless", "--convert-to", "pdf", "--outdir", outdir, os.path.abspath(source)]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return None, f"conversion failed: {exc}", False
        produced = os.path.join(outdir, stem + ".pdf")
        if not os.path.exists(produced):
            message = (proc.stderr or proc.stdout or "").strip().replace("\n", " ")[:300]
            return None, f"conversion produced no PDF (exit {proc.returncode}): {message}", False
        shutil.move(produced, target)
    return target, None, False


# ---------------------------------------------------------------------------
# Existing files on the Pure record
# ---------------------------------------------------------------------------

def file_evs(pure_record: dict) -> list:
    return [ev for ev in (pure_record.get("electronicVersions") or [])
            if isinstance(ev, dict) and ev.get("typeDiscriminator") == "FileElectronicVersion"]


def find_same_file(pure_record: dict, names: list, size: int) -> tuple:
    """
    Decide whether the record already holds this file.
    Returns (reason | None, ev | None), reason being
      "same_name_and_size"   a file with the same name (same_file_name) and size
      "same_size"            a file with another name but exactly the same size
      "same_name_size_unknown"  a file with the same name whose size Pure does
                             not report (cannot be ruled out, so not uploaded)
    or (None, None) when the file has to be uploaded.
    """
    names = [n for n in names if n]
    evs = file_evs(pure_record)

    def name_matches(ev):
        stored = (ev.get("file") or {}).get("fileName", "")
        return any(same_file_name(stored, n) for n in names)

    for ev in evs:
        if name_matches(ev) and known_file_size(ev) == size:
            return "same_name_and_size", ev
    for ev in evs:
        if known_file_size(ev) == size:
            return "same_size", ev
    for ev in evs:
        if name_matches(ev) and known_file_size(ev) is None:
            return "same_name_size_unknown", ev
    return None, None


def describe_ev(ev: dict) -> str:
    f = (ev or {}).get("file") or {}
    return f"{f.get('fileName', '?')} ({f.get('size', '?')} bytes, fileId={f.get('fileId', '?')})"


# ---------------------------------------------------------------------------
# Pure upload / PUT
# ---------------------------------------------------------------------------

def upload_file_to_pure(local_path: str, api_key: str, pure_file_upload_url: str,
                        session: requests.Session) -> tuple:
    """Upload a local PDF to Pure's file-upload endpoint. Returns (upload_json | None, error | None)."""
    try:
        with open(safe_path(local_path), "rb") as fh:
            resp = session.put(
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
        return None, f"cannot open local file: {exc}"
    except requests.RequestException as exc:
        return None, f"upload error: {exc}"
    if resp.status_code not in (200, 201):
        return None, f"file-upload HTTP {resp.status_code}: {resp.text[:200]}"
    try:
        return resp.json(), None
    except ValueError:
        return None, "could not parse the file-upload response as JSON"


def build_new_file_ev(upload_data: dict, file_name: str) -> dict:
    """FileElectronicVersion for a new file: Accepted author manuscript, CC BY, Open."""
    return {
        "typeDiscriminator":   "FileElectronicVersion",
        "visibleOnPortalDate": TODAY,
        "accessType":  {"uri": NEW_FILE_ACCESS_URI},
        "licenseType": {"uri": NEW_FILE_LICENSE_URI},
        "versionType": {"uri": NEW_FILE_VERSION_URI},
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


def put_record_with_new_file(pure_record: dict, file_ev: dict, api_key: str, base_url: str,
                             session: requests.Session) -> tuple:
    """
    Clean the record (as add_pdfs_to_pure.put_pure_record), append the new
    FileElectronicVersion and PUT it. Unlike add_pdfs_to_pure.py, the
    existing electronic versions are sent back unchanged (no licence rewrite).
    Returns (success, status_or_error).
    """
    uuid = pure_record.get("uuid")
    if not uuid:
        return False, "record has no uuid"
    cleaned = remove_nulls(strip_system_fields(pure_record, top_level=True))
    cleaned["electronicVersions"] = list(cleaned.get("electronicVersions") or []) + [file_ev]
    try:
        resp = session.put(
            f"{base_url}research-outputs/{uuid}",
            json=cleaned,
            headers={
                "accept":       "application/json",
                "content-type": "application/json",
                "api-key":      api_key,
            },
            timeout=60,
        )
    except requests.RequestException as exc:
        return False, str(exc)
    if resp.status_code in (200, 201):
        return True, str(resp.status_code)
    return False, f"{resp.status_code} - {resp.text[:300]}"


def find_uploaded_ev(pure_record: dict, file_name: str, size: int):
    """The record's file with this name and size (the one just added), newest last."""
    match = None
    for ev in file_evs(pure_record):
        stored = (ev.get("file") or {}).get("fileName", "")
        if same_file_name(stored, file_name) and known_file_size(ev) in (size, None):
            match = ev
    return match


# ---------------------------------------------------------------------------
# Logging (from add_pdfs_to_pure.py)
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


RESULT_FIELDS = [
    "sheet", "sheet_line", "dspace_uuid", "publisher_doi", "sheet_filename", "full_text_source",
    "local_path", "converted_from", "upload_file_name", "file_size",
    "title", "handle", "pure_uuid", "pure_id", "match_type",
    "existing_pure_file", "upload_key", "pure_file_id", "pure_file_pure_id",
    "pure_file_name", "status", "detail", "timestamp",
]

SKIPPED_STATUSES = {
    "duplicate_row", "conflicting_rows", "skipped_sci_hub_source", "skipped_dspace_collection",
    "skipped_dspace_type", "skipped_active_embargo", "no_match", "ambiguous_match",
    "skipped_same_name_and_size", "skipped_same_size", "skipped_same_name_size_unknown",
}
FAILED_STATUSES = {
    "file_not_found", "unsupported_file_type", "conversion_failed", "invalid_pdf",
    "refresh_failed", "upload_failed", "put_failed",
}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Upload checked local full texts (listed in metadata sheets) to the matching Pure records"
    )
    parser.add_argument("--root", required=True,
                        help="Project folder: the metadata sheets (*.csv, top level) and the download "
                             "folders (top-level sub-folders, searched recursively).")
    parser.add_argument("--dspace-csv", required=True,
                        help="DSpace export CSV (uuid, handle, dc.title, collection_names, ...).")
    parser.add_argument("--pure-json", required=True,
                        help="Pure research-outputs JSON export.")
    parser.add_argument("--test", action="store_true", default=False, help="Use UAT environment.")
    parser.add_argument("--temp", action="store_true", default=False,
                        help="Use TEMP environment (uses same API key as Production).")
    parser.add_argument("--base-url", default=None,
                        help="Override the Pure API base URL (must end with /ws/api/).")
    parser.add_argument("--log-dir", default=None,
                        help="Directory for logs (default: ./logs/local_fulltext_upload_logs/test with "
                             "--test, .../temp with --temp, otherwise .../prod).")
    parser.add_argument("--converted-dir", default="./converted_pdfs",
                        help="Where PDFs converted from .doc/.docx are kept (default: ./converted_pdfs).")
    parser.add_argument("--soffice", default=shutil.which("soffice") or shutil.which("libreoffice"),
                        help="LibreOffice executable used for .doc/.docx conversion (default: found on PATH).")
    parser.add_argument("--dry-run", action="store_true", default=False,
                        help="Match and check everything, but do not upload or PUT.")
    parser.add_argument("--refresh-from-pure", dest="refresh_from_pure", action="store_true", default=True,
                        help="Re-read each matched record from Pure before checking / updating it (default).")
    parser.add_argument("--no-refresh-from-pure", dest="refresh_from_pure", action="store_false",
                        help="Use the --pure-json export as is.")
    parser.add_argument("--include-all-collections", action="store_true", default=False,
                        help="Also process DSpace items outside the Publications collection and of the "
                             "excluded types (dataset, doctoral thesis, master thesis).")
    parser.add_argument("--allow-sci-hub", action="store_true", default=False,
                        help="Also upload rows whose Full text Source or WORKING Full text link mentions "
                             "Sci-Hub (skipped by default, see the project README).")
    args = parser.parse_args()
    if args.log_dir is None:
        args.log_dir = os.path.join(".", "logs", "local_fulltext_upload_logs", "test" if args.test else "temp" if args.temp else "prod")

    # ---- Environment -------------------------------------------------------
    load_dotenv()
    api_key_var = "PURE_ROOT_API_KEY_TEST" if args.test else "PURE_ROOT_API_KEY"
    api_key = os.getenv(api_key_var, "")
    if args.base_url:
        base_url = args.base_url if args.base_url.endswith("/") else args.base_url + "/"
        env_label = "CUSTOM"
    elif args.test:
        base_url, env_label = UAT_BASE_URL, "UAT"
    elif args.temp:
        base_url, env_label = TEMP_BASE_URL, "TEMP"
    else:
        base_url, env_label = PROD_BASE_URL, "PRODUCTION"
    pure_file_upload_url = f"{base_url}research-outputs/file-uploads"

    # ---- Logging setup -----------------------------------------------------
    os.makedirs(args.log_dir, exist_ok=True)
    run_log_path = os.path.join(args.log_dir, f"run_{RUN_TS}.log")
    results_json = os.path.join(args.log_dir, f"results_{RUN_TS}.json")
    results_csv  = os.path.join(args.log_dir, f"results_{RUN_TS}.csv")
    success_csv  = os.path.join(args.log_dir, f"success_{RUN_TS}.csv")
    failed_csv   = os.path.join(args.log_dir, f"failed_{RUN_TS}.csv")
    skipped_csv  = os.path.join(args.log_dir, f"skipped_{RUN_TS}.csv")
    sheets_csv   = os.path.join(args.log_dir, f"sheets_{RUN_TS}.csv")

    logger = RunLogger(run_log_path)
    sys.stdout = logger

    # All rows are written to results_csv as they are processed, so the log
    # survives an interrupted run.
    results_fh = open(results_csv, "w", newline="", encoding="utf-8")
    results_writer = csv.DictWriter(results_fh, fieldnames=RESULT_FIELDS, extrasaction="ignore")
    results_writer.writeheader()
    results_fh.flush()

    # ---- Validate inputs ---------------------------------------------------
    if not api_key and not args.dry_run:
        print(f"⚠️  WARNING: {api_key_var} not set in .env — API calls will fail.")
    if not os.path.isdir(args.root):
        print(f"❌ --root '{args.root}' does not exist or is not a directory")
        sys.exit(1)
    for path, label in [(args.dspace_csv, "DSpace CSV"), (args.pure_json, "Pure JSON")]:
        if not os.path.isfile(path):
            print(f"❌ {label} not found: {path}")
            sys.exit(1)

    print(f"{'='*70}")
    print(f"  Local full-text upload to Pure — {RUN_TS}")
    print(f"  Environment       : {env_label}")
    print(f"  API key var       : {api_key_var}")
    print(f"  Base URL          : {base_url}")
    print(f"  Project folder    : {args.root}")
    print(f"  DSpace CSV        : {args.dspace_csv}")
    print(f"  Pure JSON         : {args.pure_json}")
    print(f"  Converted PDFs    : {args.converted_dir}")
    print(f"  LibreOffice       : {args.soffice or 'not found'}")
    print(f"  Refresh from Pure : {args.refresh_from_pure and not args.dry_run}")
    print(f"  All collections   : {args.include_all_collections}")
    print(f"  Allow Sci-Hub     : {args.allow_sci_hub}")
    print(f"  Dry run           : {args.dry_run}")
    print(f"  Log dir           : {args.log_dir}")
    print(f"{'='*70}\n")

    exclude_dirs = [args.log_dir, args.converted_dir]

    # ---- Sheets and download folders --------------------------------------
    sheets, folders = discover_project(args.root, exclude_dirs)
    sheet_folders, folder_warnings = folders_for_sheets(sheets, folders)
    print("Download folders:")
    for path, rng in folders:
        print(f"  {os.path.basename(path)}: "
              + (f"{rng[0]:04d}-{rng[1]:04d}" if rng else "no number range — searched for every sheet"))
    for warning in folder_warnings:
        print(f"  ⚠️  {warning}")
    print()

    print("Reading metadata sheets...")
    sheet_rows = []
    sheet_report = []
    for path, rng in sheets:
        if rng is None:
            note = "no number range in the file name — cannot tell which download folders belong to it"
            print(f"  ⚠️  {os.path.basename(path)}: {note} — ignored")
            sheet_report.append({"sheet": path, "rows": 0, "correct_rows": 0, "folders": "", "note": note})
            continue
        rows, error = read_sheet(path)
        if error:
            print(f"  ⚠️  {os.path.basename(path)}: {error} — ignored")
            sheet_report.append({"sheet": path, "rows": 0, "correct_rows": 0, "folders": "", "note": error})
            continue
        n_correct = sum(1 for r in rows if is_correct(r["correct"]))
        folder_names = [os.path.basename(f) for f in sheet_folders[path]]
        print(f"  ✅ {os.path.basename(path)}: {len(rows)} rows, {n_correct} marked correct; "
              f"files from: {', '.join(folder_names) or '(no folder!)'}")
        sheet_report.append({"sheet": path, "rows": len(rows), "correct_rows": n_correct,
                             "folders": "; ".join(folder_names),
                             "note": "" if folder_names else "no download folder"})
        sheet_rows.extend(rows)
    write_csv_log(sheet_report, sheets_csv, ["sheet", "rows", "correct_rows", "folders", "note"])
    correct_rows = [r for r in sheet_rows if is_correct(r["correct"])]
    print(f"✅ {len(sheet_rows)} rows in {len(sheet_report)} CSV file(s); {len(correct_rows)} marked correct\n")

    # Rows explicitly marked not correct, per (DSpace UUID, file name) -- a
    # file marked correct in one sheet and not correct in another is not uploaded.
    negative_keys = defaultdict(list)
    for r in sheet_rows:
        if is_explicitly_not_correct(r["correct"]):
            negative_keys[(r["uuid"], sheet_file_key(r["filename"]))].append(r)

    # ---- Local files -------------------------------------------------------
    print("Indexing download folders...")
    folder_index = {}
    for path, _ in folders:
        folder_index[path] = build_file_index(path, exclude_dirs)
        print(f"  {os.path.basename(path)}: {sum(len(v) for v in folder_index[path][0].values())} files")
    sheet_index = {sheet: merge_file_indexes([folder_index[f] for f in fs])
                   for sheet, fs in sheet_folders.items()}
    print()

    # ---- DSpace export -----------------------------------------------------
    print("Loading DSpace CSV...")
    csv.field_size_limit(min(sys.maxsize, 2**31 - 1))
    dspace_by_uuid = {}
    with open(args.dspace_csv, "r", encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            uuid = (row.get("uuid") or "").strip().lower()
            if uuid:
                dspace_by_uuid[uuid] = row
    print(f"✅ Loaded {len(dspace_by_uuid)} DSpace rows")

    print("Loading Pure JSON...")
    with open(args.pure_json, "r", encoding="utf-8-sig") as fh:
        pure_items = json.load(fh)
    print(f"✅ Loaded {len(pure_items)} Pure records\n")

    print("Building Pure lookup index...")
    pure_index = build_pure_index(pure_items)
    linked_count = sum(1 for item in pure_items if isinstance(item, dict) and _dspace_uuids_of(item))
    print(f"  Pure records linked to a DSpace item: {linked_count} (matched only by DSpace UUID)")
    print(f"  by_doi         : {len(pure_index['by_doi'])} entries")
    print(f"  by_dspace_uuid : {len(pure_index['by_dspace_uuid'])} entries\n")

    session = requests.Session()
    session.headers.update({"api-key": api_key})

    # ---- Process -----------------------------------------------------------
    results = []
    counters = defaultdict(int)
    seen_keys = {}             # (uuid, file key) -> "sheet:line" of the first row
    refreshed = set()          # Pure UUIDs already re-read in this run
    start_time = time.time()

    print(f"Processing {len(correct_rows)} rows marked correct...")
    print(f"{'='*70}")

    for i, srow in enumerate(tqdm(correct_rows, desc="Uploading files", unit="row"), start=1):
        dspace_uuid = srow["uuid"]
        entry = {f: None for f in RESULT_FIELDS}
        entry.update({
            "sheet":          srow["sheet"],
            "sheet_line":     srow["line"],
            "dspace_uuid":    dspace_uuid,
            "publisher_doi":  "; ".join(srow["dois"]) or srow["doi_raw"],
            "sheet_filename": srow["filename"],
            "full_text_source": srow["source"],
            "timestamp":      datetime.now().isoformat(),
        })

        def finish(status, detail):
            entry["status"] = status
            entry["detail"] = detail
            entry["timestamp"] = datetime.now().isoformat()
            counters[status] += 1
            results.append(entry)
            results_writer.writerow(entry)
            results_fh.flush()

        print(f"\n[{i}/{len(correct_rows)}] {os.path.basename(srow['sheet'])}:{srow['line']}  "
              f"{dspace_uuid}  {srow['filename'].strip()}")

        # 1. Repeated / conflicting rows
        key = (dspace_uuid, sheet_file_key(srow["filename"]))
        if key in seen_keys:
            print(f"  ⏭️  Same DSpace UUID and file as {seen_keys[key]} — skipping")
            finish("duplicate_row", f"same DSpace UUID and file name as {seen_keys[key]}")
            continue
        seen_keys[key] = f"{srow['sheet']}:{srow['line']}"
        if key in negative_keys:
            where = "; ".join(f"{r['sheet']}:{r['line']} ({r['correct'].strip()[:40]})" for r in negative_keys[key])
            print(f"  ⚠️  The same file is marked NOT correct elsewhere: {where} — skipping")
            finish("conflicting_rows", f"also marked not correct in {where}")
            continue
        if not args.allow_sci_hub and SCI_HUB_RE.search(f"{srow['source']} {srow['link']}"):
            print(f"  ⏭️  Full-text source is Sci-Hub ({srow['source'] or srow['link']}) — skipping "
                  "(record a legitimate open-access source, or use --allow-sci-hub)")
            finish("skipped_sci_hub_source", f"Full text Source: {srow['source']}; link: {srow['link']}")
            continue

        # 2. DSpace item (title, Handle, collection, embargo for matching)
        dspace_row = dspace_by_uuid.get(dspace_uuid)
        if dspace_row is not None:
            match_row = dict(dspace_row)
            dois = parse_doi_field(match_row.get("dc.identifier.doi", ""))
            dois += [d for d in srow["dois"] if d not in dois]
            match_row["dc.identifier.doi"] = " ; ".join(dois)
            entry["title"] = (dspace_row.get("dc.title") or "").strip()
            handle = (dspace_row.get("handle") or "").strip()
            entry["handle"] = f"https://hdl.handle.net/{handle}" if handle and not handle.startswith("http") else handle
            print(f"  Title          : {entry['title'][:70]}")

            collection = (dspace_row.get("collection_names") or "").strip()
            dc_type = (dspace_row.get("dc.type") or "").strip()
            if not args.include_all_collections and collection.lower() != PUBLICATIONS_COLLECTION:
                print(f"  ⏭️  DSpace item is in collection '{collection}', not Publications — skipping")
                finish("skipped_dspace_collection", f"DSpace collection: {collection or '(none)'}")
                continue
            if not args.include_all_collections and dc_type.lower() in EXCLUDED_DSPACE_TYPES:
                print(f"  ⏭️  DSpace type '{dc_type}' is not uploaded to Pure — skipping")
                finish("skipped_dspace_type", f"DSpace type: {dc_type}")
                continue
            embargo_date, embargo_active, _ = resolve_embargo_and_access(dspace_row)
            if embargo_active:
                print(f"  ⏭️  DSpace item is under embargo until {embargo_date} — not uploading as open access")
                finish("skipped_active_embargo", f"DSpace embargo until {embargo_date}")
                continue
        else:
            match_row = {"uuid": dspace_uuid, "dc.identifier.doi": " ; ".join(srow["dois"])}
            print("  ⚠️  DSpace UUID not in the DSpace export — matching by UUID only "
                  "(the publisher-DOI back-up needs the title)")

        # 3. Local file
        by_name, by_lower = sheet_index[srow["sheet"]]
        local_path, problem, note = resolve_local_file(srow["filename"], by_name, by_lower)
        if local_path is None:
            problem += f" (searched: {', '.join(os.path.basename(f) for f in sheet_folders[srow['sheet']])})"
            print(f"  ❌ {problem}")
            finish("file_not_found", problem)
            continue
        entry["local_path"] = local_path
        print(f"  Local file     : {local_path}" + (f"  ({note})" if note else ""))

        ext = os.path.splitext(local_path)[1].lower()
        names_to_compare = [os.path.basename(local_path)]
        if ext in CONVERTIBLE_EXTENSIONS:
            upload_path, problem, reused = convert_to_pdf(local_path, args.converted_dir, args.soffice)
            if upload_path is None:
                print(f"  ❌ {problem}")
                finish("conversion_failed", problem)
                continue
            entry["converted_from"] = local_path
            print(f"  🔁 {'Using earlier conversion' if reused else 'Converted to PDF'}: {upload_path}")
        elif ext == ".pdf":
            upload_path = local_path
        else:
            problem = f"file type '{ext or '(none)'}' is not supported (only .pdf, .doc, .docx)"
            print(f"  ❌ {problem}")
            finish("unsupported_file_type", problem)
            continue

        upload_name = os.path.basename(upload_path)
        names_to_compare.insert(0, upload_name)
        try:
            with open(safe_path(upload_path), "rb") as fh:
                content = fh.read()
        except OSError as exc:
            print(f"  ❌ Cannot read {upload_path}: {exc}")
            finish("file_not_found", f"cannot read {upload_path}: {exc}")
            continue
        problem = pdf_problem(content)
        if problem:
            print(f"  ❌ Not a valid PDF: {problem}")
            finish("invalid_pdf", problem)
            continue
        file_size = len(content)
        entry["upload_file_name"] = upload_name
        entry["file_size"] = file_size
        print(f"  Upload as      : {upload_name} ({file_size:,} bytes)")

        # 4. Match the Pure record
        pure_record, match_type, candidates = find_pure_record(match_row, pure_index)
        if pure_record is None and candidates:
            candidate_uuids = [c.get("uuid", "") for c in candidates]
            print(f"  ⚠️  Ambiguous match — {len(candidates)} Pure records ({match_type}): {candidate_uuids} — skipping")
            entry["match_type"] = match_type
            finish("ambiguous_match", f"{len(candidates)} Pure records match: {'; '.join(candidate_uuids)}")
            continue
        if pure_record is None:
            reason = "No Pure record found for this DSpace UUID or publisher DOI" + (f": {match_type}" if match_type else "")
            print(f"  ⚠️  {reason} — skipping")
            finish("no_match", reason)
            continue
        pure_uuid = pure_record.get("uuid", "")
        entry["pure_uuid"] = pure_uuid
        entry["pure_id"] = str(pure_record.get("pureId", ""))
        entry["match_type"] = match_type
        print(f"  ✅ Matched Pure record ({match_type}): {pure_uuid}  pureId: {entry['pure_id']}")

        # 5. Current version of the record
        if args.refresh_from_pure and not args.dry_run and pure_uuid not in refreshed:
            fresh, fetch_error = fetch_pure_record(pure_uuid, base_url, session)
            if fresh is None:
                print(f"  ❌ Could not re-read the record from Pure: {fetch_error} — skipping")
                finish("refresh_failed", f"GET research-outputs/{pure_uuid} failed: {fetch_error}")
                continue
            # In place, so every index entry pointing at this record sees it.
            pure_record.clear()
            pure_record.update(fresh)
            refreshed.add(pure_uuid)
            print("  🔄 Re-read the current record from Pure")

        # 6. Is the file already there?
        reason, existing_ev = find_same_file(pure_record, names_to_compare, file_size)
        if reason:
            entry["existing_pure_file"] = describe_ev(existing_ev)
            f = existing_ev.get("file") or {}
            entry["pure_file_id"] = f.get("fileId", "")
            entry["pure_file_pure_id"] = f.get("pureId", "")
            entry["pure_file_name"] = f.get("fileName", "")
            text = {
                "same_name_and_size":     "file with the same name and size already in Pure",
                "same_size":              "file with a different name but exactly the same size already in Pure",
                "same_name_size_unknown": "file with the same name already in Pure (Pure reports no size)",
            }[reason]
            print(f"  ⏭️  {text}: {entry['existing_pure_file']} — skipping")
            finish(f"skipped_{reason}", f"{text}: {entry['existing_pure_file']}")
            continue

        n_existing = len(file_evs(pure_record))
        if args.dry_run:
            print(f"  🔍 DRY RUN — would upload {upload_name} (record has {n_existing} file(s))")
            finish("dry_run", f"would upload {upload_name} ({file_size} bytes); record has {n_existing} file(s)")
            continue

        # 7. Upload, then PUT immediately (the upload expires after 2 hours)
        print(f"  📎 Uploading {upload_name}...")
        upload_data, error = upload_file_to_pure(upload_path, api_key, pure_file_upload_url, session)
        if upload_data is None:
            print(f"  ❌ Upload failed: {error}")
            finish("upload_failed", error)
            continue
        entry["upload_key"] = upload_data.get("key", "")
        print(f"  ✅ Uploaded — key: {entry['upload_key']}")

        file_ev = build_new_file_ev(upload_data, upload_name)
        print(f"  📤 PUTting Pure record {pure_uuid}...")
        success, detail = put_record_with_new_file(pure_record, file_ev, api_key, base_url, session)
        if not success:
            print(f"  ❌ PUT failed: {detail}")
            finish("put_failed", f"PUT failed: {detail}")
            continue
        print(f"  ✅ PUT succeeded ({detail})")

        # Re-read the record: gives the new file's fileId and keeps the
        # in-memory record current for later rows of the same record.
        fresh, fetch_error = fetch_pure_record(pure_uuid, base_url, session)
        if fresh is not None:
            pure_record.clear()
            pure_record.update(fresh)
            refreshed.add(pure_uuid)
            new_ev = find_uploaded_ev(pure_record, upload_name, file_size)
            if new_ev:
                f = new_ev.get("file") or {}
                entry["pure_file_id"] = f.get("fileId", "")
                entry["pure_file_pure_id"] = f.get("pureId", "")
                entry["pure_file_name"] = f.get("fileName", "")
                print(f"  🔎 File in Pure — fileId: {entry['pure_file_id']}  fileName: {entry['pure_file_name']}")
            finish("success", f"uploaded {upload_name}")
        else:
            # The PUT went through; keep the record usable for later rows.
            pure_record.setdefault("electronicVersions", []).append(file_ev)
            refreshed.discard(pure_uuid)
            print(f"  ⚠️  Could not re-read the record after the PUT: {fetch_error}")
            finish("success", f"uploaded {upload_name} (record not re-read: {fetch_error})")

    # ---- Summary & logs ----------------------------------------------------
    results_fh.close()
    elapsed = time.time() - start_time
    h, rem = divmod(int(elapsed), 3600)
    m, s = divmod(rem, 60)

    labels = [
        ("success",                        "Uploaded"),
        ("dry_run",                        "Would upload (dry run)"),
        ("skipped_same_name_and_size",     "Skipped: same name and size in Pure"),
        ("skipped_same_size",              "Skipped: same size, other name"),
        ("skipped_same_name_size_unknown", "Skipped: same name, size unknown"),
        ("duplicate_row",                  "Skipped: repeated sheet row"),
        ("conflicting_rows",               "Skipped: also marked not correct"),
        ("skipped_sci_hub_source",         "Skipped: Sci-Hub source"),
        ("skipped_dspace_collection",      "Skipped: not in Publications"),
        ("skipped_dspace_type",            "Skipped: excluded DSpace type"),
        ("skipped_active_embargo",         "Skipped: active DSpace embargo"),
        ("no_match",                       "No Pure match"),
        ("ambiguous_match",                "Ambiguous Pure match"),
        ("file_not_found",                 "Local file not found"),
        ("unsupported_file_type",          "Unsupported file type"),
        ("conversion_failed",              "Conversion to PDF failed"),
        ("invalid_pdf",                    "Not a valid PDF"),
        ("refresh_failed",                 "Re-read from Pure failed"),
        ("upload_failed",                  "Upload failed"),
        ("put_failed",                     "PUT failed"),
    ]
    print(f"\n{'='*70}")
    print(f"SUMMARY — {RUN_TS}")
    print(f"{'='*70}")
    print(f"  Sheets read                         : {sum(1 for r in sheet_report if r['rows'])}")
    print(f"  Rows in sheets                      : {len(sheet_rows)}")
    print(f"  Rows marked correct (processed)     : {len(correct_rows)}")
    for status, label in labels:
        if counters.get(status) or status in ("success", "no_match", "file_not_found"):
            print(f"  {label:<36}: {counters.get(status, 0)}")
    print(f"  Time elapsed                        : {h:02d}:{m:02d}:{s:02d}")
    print(f"{'='*70}")

    write_json_log(results, results_json)
    success_rows = [r for r in results if r["status"] in ("success", "dry_run")]
    failed_rows  = [r for r in results if r["status"] in FAILED_STATUSES]
    skipped_rows = [r for r in results if r["status"] in SKIPPED_STATUSES]
    print(f"\n  Full log     : {run_log_path}")
    print(f"  Results CSV  : {results_csv}")
    print(f"  Results JSON : {results_json}")
    print(f"  Sheets CSV   : {sheets_csv}")
    if success_rows:
        write_csv_log(success_rows, success_csv, RESULT_FIELDS)
        print(f"  Success CSV  : {success_csv}")
    if failed_rows:
        write_csv_log(failed_rows, failed_csv, RESULT_FIELDS)
        print(f"  Failed CSV   : {failed_csv}")
    if skipped_rows:
        write_csv_log(skipped_rows, skipped_csv, RESULT_FIELDS)
        print(f"  Skipped CSV  : {skipped_csv}")

    logger.close()
    sys.stdout = logger._terminal


if __name__ == "__main__":
    main()
