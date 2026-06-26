#!/usr/bin/env python3
"""
create_pure_xml.py

Match records between a DSpace CSV export and a Pure JSON export, then produce
a Pure Research Output Import XML file conforming to the
v1.publication-import.base-uk.pure.atira.dk schema.

Matching strategy (same priority order as get_file_ids.py):
  1. DSpace UUID stored in Pure identifiers (idSource == "DSpace")
  2. Handle URL stored in Pure links (alias == "Handle") matched against the
     DSpace CSV "handle" column

ID rules:
  - The XML element id attribute (e.g. <contributionToJournal id="..."/>) is the
    Pure pureId.
  - The DSpace UUID is written as <id type="DSpace"> inside <externalIds>.

Data source priority:
  Pure JSON is authoritative. The DSpace CSV is only used to fill in fields
  Pure doesn't have (e.g. journal/publisher details, embargo info, and any
  bitstream that exists in DSpace but isn't yet recorded as an electronic
  version in Pure).

existingStores:
  Every matched record gets an <existingStores>/<existingStore> block built
  from the DSpace CSV "handle" column (storeContentId) and the chosen
  --environment's hostname (storeName), with <updateRequired>true</updateRequired>,
  matching the structure of DSpace_XMl_example.xml.

DSpace-only files:
  If the DSpace CSV row references a bitstream (via "pdf_links" /
  "pdf_handle_paths") whose filename doesn't match any FileElectronicVersion
  already present in the Pure JSON record, a new <electronicVersionFile> is
  added for it. Its <v1:file id="..."> follows the example's
  "{handle}:{sequence}:{filename}" pattern, and <fileLocation> is the
  "pdf_links" URL rewritten onto the selected --environment's domain.
  No <filesize> is written for these files since the CSV doesn't carry one.

Journal (<journal>):
  Mandatory (minOccurs=1) for contributionToJournal and
  contributionToSpecialist, so it is always emitted -- resolved with
  priority Pure JSON > DSpace CSV > Pure Journals API:
    1. Pure JSON: journalAssociation.journal.uuid (-> id attribute) and
       journalAssociation.title.title (-> title).
    2. DSpace CSV: journal_title / dc.identifier.journal (-> title),
       dc.identifier.issn (-> printIssns), used only for whichever piece
       Pure JSON didn't already provide.
    3. Pure Journals API (GET /ws/api/journals/{uuid}): only called when a
       journal UUID is known from Pure JSON but title and/or ISSNs are
       still missing after (1) and (2) -- the API is looked up by UUID, so
       it can't help when Pure JSON has no journalAssociation at all. The
       API key is read from a .env file under
       PURE_ROOT_API_KEY_TEST (--environment test) or PURE_ROOT_API_KEY
       (--environment prod), falling back to real process environment
       variables of the same name.
  If nothing is found anywhere, an empty <journal/> is emitted (still
  schema-valid, since all its children are optional) and a warning is
  printed so the gap can be filled in manually.

Usage:
    python create_pure_xml.py --csv input.csv --json input.json --environment test
    python create_pure_xml.py --csv input.csv --json input.json --environment prod --output out.xml
    python create_pure_xml.py --csv input.csv --json input.json --environment test \\
        --modified-by "john@example.com" --modified-after "2025-01-01"
"""

import argparse
import csv
import json
import mimetypes
import os
import re
import sys
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from urllib.parse import unquote, urlsplit, urlunsplit
from xml.etree import ElementTree as ET
from xml.dom import minidom

from dotenv import load_dotenv


# ---------------------------------------------------------------------------
# Console → log-file tee
# ---------------------------------------------------------------------------

class _Tee:
    """Write to both *stream* and *log_file* simultaneously."""

    def __init__(self, stream, log_file):
        self._stream = stream
        self._log = log_file

    def write(self, data):
        self._stream.write(data)
        self._log.write(data)

    def flush(self):
        self._stream.flush()
        self._log.flush()

    def fileno(self):
        return self._stream.fileno()

    # Delegate everything else (isatty, etc.) to the real stream.
    def __getattr__(self, name):
        return getattr(self._stream, name)


def _start_logging(log_path: str):
    """Open *log_path* for writing and tee both stdout and stderr into it."""
    os.makedirs(os.path.dirname(os.path.abspath(log_path)), exist_ok=True)
    log_fh = open(log_path, "w", encoding="utf-8")
    sys.stdout = _Tee(sys.__stdout__, log_fh)
    sys.stderr = _Tee(sys.__stderr__, log_fh)
    return log_fh

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

HANDLE_BASE_URL = "http://hdl.handle.net/"

DSPACE_BASE_URLS: dict[str, str] = {
    "test": "https://galway.dspace7-test.openrepository.com/",
    "prod": "https://researchrepository.universityofgalway.ie/",
}

PURE_JOURNAL_API_URLS: dict[str, str] = {
    "test": "https://cust-uk-cc-dspace3.devel.elsevierpure.com/ws/api/journals/{uuid}",
    "prod": "https://research.universityofgalway.ie/ws/api/journals/{uuid}",
}

PURE_API_KEY_VARS: dict[str, str] = {
    "test": "PURE_ROOT_API_KEY_TEST",
    "prod": "PURE_ROOT_API_KEY",
}

PUB_NS  = "v1.publication-import.base-uk.pure.atira.dk"
CMN_NS  = "v3.commons.pure.atira.dk"

PURE_TYPE_MAP: dict[str, tuple[str, str]] = {
    "/dk/atira/pure/researchoutput/researchoutputtypes/contributiontojournal/article":                  ("contributionToJournal", "article"),
    "/dk/atira/pure/researchoutput/researchoutputtypes/contributiontojournal/systematicreview":         ("contributionToJournal", "systematicreview"),
    "/dk/atira/pure/researchoutput/researchoutputtypes/contributiontojournal/letter":                   ("contributionToJournal", "letter"),
    "/dk/atira/pure/researchoutput/researchoutputtypes/contributiontojournal/editorialnote":            ("contributionToJournal", "editorialnote"),
    "/dk/atira/pure/researchoutput/researchoutputtypes/contributiontojournal/shortsurvey":              ("contributionToJournal", "shortsurvey"),
    "/dk/atira/pure/researchoutput/researchoutputtypes/contributiontojournal/comment":                  ("contributionToJournal", "comment"),
    "/dk/atira/pure/researchoutput/researchoutputtypes/contributiontojournal/conferencearticle":        ("contributionToJournal", "conferencearticle"),
    "/dk/atira/pure/researchoutput/researchoutputtypes/contributiontojournal/abstract":                 ("contributionToJournal", "abstract"),
    "/dk/atira/pure/researchoutput/researchoutputtypes/contributiontojournal/editorial":                ("contributionToJournal", "editorial"),
    "/dk/atira/pure/researchoutput/researchoutputtypes/contributiontojournal/journal_editing_peer_non_peer_": ("contributionToJournal", "journal_editing_peer_non_peer_"),
    "/dk/atira/pure/researchoutput/researchoutputtypes/contributiontojournal/scientific":               ("contributionToJournal", "scientific"),
    "/dk/atira/pure/researchoutput/researchoutputtypes/contributiontoconference/paper":                 ("contributionToConference", "paper"),
    "/dk/atira/pure/researchoutput/researchoutputtypes/contributiontoconference/poster":                ("contributionToConference", "poster"),
    "/dk/atira/pure/researchoutput/researchoutputtypes/contributiontoconference/abstract":              ("contributionToConference", "abstract"),
    "/dk/atira/pure/researchoutput/researchoutputtypes/contributiontoconference/other":                 ("contributionToConference", "other"),
    "/dk/atira/pure/researchoutput/researchoutputtypes/contributiontoconference/international_refereed_conference_paper": ("contributionToConference", "international_refereed_conference_paper"),
    "/dk/atira/pure/researchoutput/researchoutputtypes/contributiontoconference/invited_paper":         ("contributionToConference", "invited_paper"),
    "/dk/atira/pure/researchoutput/researchoutputtypes/contributiontoconference/national_refereed_conference_paper": ("contributionToConference", "national_refereed_conference_paper"),
    "/dk/atira/pure/researchoutput/researchoutputtypes/bookanthology/book":                                          ("book", "book"),
    "/dk/atira/pure/researchoutput/researchoutputtypes/bookanthology/anthology":                                    ("book", "anthology"),
    "/dk/atira/pure/researchoutput/researchoutputtypes/bookanthology/commissioned":                                 ("book", "commissioned"),
    "/dk/atira/pure/researchoutput/researchoutputtypes/bookanthology/report":                                       ("book", "report"),
    "/dk/atira/pure/researchoutput/researchoutputtypes/bookanthology/commissioned_report":                          ("book", "commissioned_report"),
    "/dk/atira/pure/researchoutput/researchoutputtypes/bookanthology/edited_book":                                  ("book", "edited_book"),
    "/dk/atira/pure/researchoutput/researchoutputtypes/contributiontobookanthology/chapter":                        ("chapterInBook", "chapter"),
    "/dk/atira/pure/researchoutput/researchoutputtypes/contributiontobookanthology/conference":                     ("chapterInBook", "conference"),
    "/dk/atira/pure/researchoutput/researchoutputtypes/contributiontobookanthology/foreword":                       ("chapterInBook", "foreword"),
    "/dk/atira/pure/researchoutput/researchoutputtypes/contributiontobookanthology/other":                          ("chapterInBook", "chapter"),
    "/dk/atira/pure/researchoutput/researchoutputtypes/contributiontobookanthology/entry":                          ("chapterInBook", "entry"),
    "/dk/atira/pure/researchoutput/researchoutputtypes/workingpaper/workingpaper":                                  ("workingPaper", "workingpaper"),
    "/dk/atira/pure/researchoutput/researchoutputtypes/workingpaper/discussionpaper":                               ("workingPaper", "discussionpaper"),
    "/dk/atira/pure/researchoutput/researchoutputtypes/workingpaper/preprint":                                      ("workingPaper", "preprint"),
    "/dk/atira/pure/researchoutput/researchoutputtypes/thesis/doctoral":                                            ("thesis", "phd"),
    "/dk/atira/pure/researchoutput/researchoutputtypes/thesis/master":                                              ("thesis", "master"),
    "/dk/atira/pure/researchoutput/researchoutputtypes/thesis/bachelor":                                            ("thesis", "bachelor"),
    "/dk/atira/pure/researchoutput/researchoutputtypes/thesis/other":                                               ("thesis", "other"),
    "/dk/atira/pure/researchoutput/researchoutputtypes/thesis/doc":                                                 ("thesis", "doc"),
    "/dk/atira/pure/researchoutput/researchoutputtypes/nontextual/audiovisual_material":                            ("nonTextual", "audiovisual_material"),
    "/dk/atira/pure/researchoutput/researchoutputtypes/nontextual/web_publication":                                 ("nonTextual", "web_publication"),
    "/dk/atira/pure/researchoutput/researchoutputtypes/nontextual/database":                                        ("nonTextual", "database"),
    "/dk/atira/pure/researchoutput/researchoutputtypes/nontextual/software":                                        ("nonTextual", "software"),
    "/dk/atira/pure/researchoutput/researchoutputtypes/nontextual/artefact":                                        ("nonTextual", "artefact"),
    "/dk/atira/pure/researchoutput/researchoutputtypes/nontextual/artwork":                                         ("nonTextual", "artwork"),
    "/dk/atira/pure/researchoutput/researchoutputtypes/nontextual/composition":                                     ("nonTextual", "composition"),
    "/dk/atira/pure/researchoutput/researchoutputtypes/nontextual/creative_practice":                               ("nonTextual", "creative_practice"),
    "/dk/atira/pure/researchoutput/researchoutputtypes/nontextual/design":                                          ("nonTextual", "design"),
    "/dk/atira/pure/researchoutput/researchoutputtypes/nontextual/digitalorvisualproducts":                         ("nonTextual", "digitalorvisualproducts"),
    "/dk/atira/pure/researchoutput/researchoutputtypes/nontextual/documentary":                                     ("nonTextual", "documentary"),
    "/dk/atira/pure/researchoutput/researchoutputtypes/nontextual/exhibition":                                      ("nonTextual", "exhibition"),
    "/dk/atira/pure/researchoutput/researchoutputtypes/nontextual/map":                                             ("nonTextual", "map"),
    "/dk/atira/pure/researchoutput/researchoutputtypes/nontextual/media_community_engagement":                      ("nonTextual", "media_community_engagement"),
    "/dk/atira/pure/researchoutput/researchoutputtypes/nontextual/musical_composition":                             ("nonTextual", "musical_composition"),
    "/dk/atira/pure/researchoutput/researchoutputtypes/nontextual/online_multimedia":                               ("nonTextual", "online_multimedia"),
    "/dk/atira/pure/researchoutput/researchoutputtypes/nontextual/performance":                                     ("nonTextual", "performance"),
    "/dk/atira/pure/researchoutput/researchoutputtypes/nontextual/poetry":                                          ("nonTextual", "poetry"),
    "/dk/atira/pure/researchoutput/researchoutputtypes/nontextual/screenplay":                                      ("nonTextual", "screenplay"),
    "/dk/atira/pure/researchoutput/researchoutputtypes/nontextual/sound_recording":                                 ("nonTextual", "sound_recording"),
    "/dk/atira/pure/researchoutput/researchoutputtypes/othercontribution/other":                                    ("other", "other"),
    "/dk/atira/pure/researchoutput/researchoutputtypes/othercontribution/community_engagement_publications":        ("other", "community_engagement_publications"),
    "/dk/atira/pure/researchoutput/researchoutputtypes/othercontribution/electronic_articles":                      ("other", "electronic_articles"),
    "/dk/atira/pure/researchoutput/researchoutputtypes/othercontribution/policy_contribution":                      ("other", "policy_contribution"),
    "/dk/atira/pure/researchoutput/researchoutputtypes/contributiontospecialistpublication/article":                ("contributionToSpecialist", "article"),
    "/dk/atira/pure/researchoutput/researchoutputtypes/contributiontoperiodical/article":                           ("contributionToSpecialist", "article"),
    "/dk/atira/pure/researchoutput/researchoutputtypes/contributiontoperiodical/book":                              ("contributionToSpecialist", "book"),
    "/dk/atira/pure/researchoutput/researchoutputtypes/contributiontoperiodical/editorial":                         ("contributionToSpecialist", "editorial"),
    "/dk/atira/pure/researchoutput/researchoutputtypes/contributiontoperiodical/featured":                          ("contributionToSpecialist", "featured"),
    "/dk/atira/pure/researchoutput/researchoutputtypes/contributiontoperiodical/letter":                            ("contributionToSpecialist", "letter"),
    "/dk/atira/pure/researchoutput/researchoutputtypes/contributiontoperiodical/special":                           ("contributionToSpecialist", "special"),
    "/dk/atira/pure/researchoutput/researchoutputtypes/patent/patent":                                              ("patent", "patent"),
    "/dk/atira/pure/researchoutput/researchoutputtypes/memorandum/academicmemorandum":                              ("memorandum", "academicmemorandum"),
    "/dk/atira/pure/researchoutput/researchoutputtypes/memorandum/qahearing":                                       ("memorandum", "qahearing"),
    "/dk/atira/pure/researchoutput/researchoutputtypes/contributiontomemorandum/contributiontoacademicmemorandum":  ("contributionToMemorandum", "contributiontoacademicmemorandum"),
    "/dk/atira/pure/researchoutput/researchoutputtypes/contributiontomemorandum/contributiontoqahearing":           ("contributionToMemorandum", "contributiontoqahearing"),
}

DSPACE_TYPE_MAP: dict[str, tuple[str, str]] = {
    "journal article":        ("contributionToJournal", "article"),
    "review article":         ("contributionToJournal", "systematicreview"),
    "review":                 ("contributionToJournal", "systematicreview"),
    "book review":            ("contributionToSpecialist", "book"),
    "conference paper":       ("contributionToConference", "paper"),
    "conference output":      ("contributionToConference", "other"),
    "conference poster":      ("contributionToConference", "poster"),
    "conference proceedings": ("contributionToConference", "other"),
    "book":                   ("book", "book"),
    "book part":              ("chapterInBook", "chapter"),
    "report":                 ("book", "book"),
    "working paper":          ("workingPaper", "workingpaper"),
    "newspaper article":      ("contributionToSpecialist", "article"),
    "video":                  ("nonTextual", "audiovisual_material"),
    "interactive resource":   ("nonTextual", "web_publication"),
    "data management plan":   ("other", "other"),
    "other":                  ("other", "other"),
}

LICENSE_MAP: dict[str, str] = {
    "cc_by":       "cc_by",
    "cc_by_nc":    "cc_by_nc",
    "cc_by_nc_nd": "cc_by_nc_nd",
    "cc_by_nc_sa": "cc_by_nc_sa",
    "cc_by_nd":    "cc_by_nd",
    "cc_by_sa":    "cc_by_sa",
    "cc0":         "cc0",
}

ACCESS_MAP: dict[str, str] = {
    "open":      "open",
    "closed":    "closed",
    "embargoed": "embargoed",
    "unknown":   "unknown",
}

VERSION_MAP: dict[str, str] = {
    "publishersversion": "publishersversion",
    "authorsversion":    "authorsversion",
    "preprintversion":   "preprintversion",
    "proofversion":      "proofversion",
}

WORKFLOW_MAP: dict[str, str] = {
    "entryInProgress": "entryInProgress",
    "forApproval":     "forApproval",
    "approved":        "approved",
    "validated":       "validated",
}

VISIBILITY_MAP: dict[str, str] = {
    "FREE":       "Public",
    "BACKEND":    "Restricted",
    "CAMPUS":     "Restricted",
    "RESTRICTED": "Restricted",
}

ROLE_MAP: dict[str, str] = {
    "author":      "author",
    "editor":      "editor",
    "translator":  "translator",
    "illustrator": "illustrator",
    "inventor":    "inventor",
    "supervisor":  "supervisor",
}

 
# ---------------------------------------------------------------------------
# ID / handle utilities
# ---------------------------------------------------------------------------
 
def build_handle_url(raw_handle: str) -> str:
    raw_handle = raw_handle.strip()
    if raw_handle.startswith("http"):
        return raw_handle
    return HANDLE_BASE_URL + raw_handle
 
 
def extract_dspace_uuid_from_json_record(record: dict) -> str | None:
    for identifier in record.get("identifiers", []):
        if identifier.get("idSource") == "DSpace":
            value = identifier.get("value", "").strip()
            if value:
                return value
    return None
 
 
def extract_handles_from_json_record(record: dict) -> list[str]:
    handles = []
    for link in record.get("links", []):
        if link.get("alias") == "Handle":
            url = link.get("url", "").strip()
            if url:
                handles.append(url)
    return handles
 
 
def pure_normalize_filename(name: str) -> str:
    return re.sub(r'[^\w.\- ]', '_', name)
 
 
def rebuild_url_for_environment(url: str, target_netloc: str, target_scheme: str = "https") -> str:
    url = (url or "").strip()
    if not url:
        return url
    parts = urlsplit(url)
    if not parts.scheme or not parts.netloc:
        return url
    return urlunsplit((target_scheme, target_netloc, parts.path, parts.query, parts.fragment))
 
 
def normalize_filename_for_match(name: str) -> str:
    return unquote(name or "").strip().casefold()
 
 
def parse_pdf_handle_path(path: str, handle: str) -> tuple[str, str] | tuple[None, None]:
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
    filename = unquote(filename).strip()
    sequence = sequence.strip() or "1"
    if not filename:
        return None, None
    return sequence, filename
 
 
def guess_mimetype(filename: str) -> str:
    guessed, _ = mimetypes.guess_type(filename or "")
    return guessed or "application/pdf"


def parse_dspace_files(
    pdf_links: str,
    pdf_handle_paths: str,
    handle: str,
    store_host: str,
) -> list[tuple[str, str, str]]:
    """
    Parse the (possibly semicolon-separated) pdf_links and pdf_handle_paths
    CSV columns into a list of (sequence, filename, file_location) tuples,
    one per file, with the URL rewritten onto store_host.

    A row with a single file produces a one-element list.
    A row like:
        pdf_links:        "https://…/uuid1/content ; https://…/uuid2/content"
        pdf_handle_paths: "/10379/123/1/a.pdf ; /10379/123/4/b.pdf"
    produces two tuples.

    If pdf_handle_paths is absent or has fewer entries than pdf_links, the
    filename falls back to the last path-segment of the corresponding URL.
    """
    links = [l.strip() for l in (pdf_links or "").split(";") if l.strip()]
    paths = [p.strip() for p in (pdf_handle_paths or "").split(";") if p.strip()]

    result: list[tuple[str, str, str]] = []
    for i, raw_url in enumerate(links):
        file_location = rebuild_url_for_environment(raw_url, store_host)
        raw_path = paths[i] if i < len(paths) else ""
        sequence, filename = parse_pdf_handle_path(raw_path, handle)
        if not filename:
            # Fallback: decode the last URL path segment
            from urllib.parse import urlsplit, unquote as _unq
            filename = _unq(urlsplit(raw_url).path.rstrip("/").split("/")[-1]) or raw_url
            sequence = sequence or "1"
        result.append((sequence, filename, file_location))
    return result
 
 
# ---------------------------------------------------------------------------
# Pure Journals API fallback
# ---------------------------------------------------------------------------
 
def parse_journal_api_response(data: dict) -> dict:
    titles = data.get("titles") or []
    active_titles = [t for t in titles if not t.get("endDate")]
    title_pool = active_titles or titles
    title = (title_pool[-1].get("title") or "").strip() if title_pool else ""
 
    all_issns = data.get("issns") or []
    active_issns = [i for i in all_issns if not i.get("endDate")]
    issn_pool = active_issns or all_issns
    issns = [i["issn"].strip() for i in issn_pool if (i.get("issn") or "").strip()]
 
    publisher_pure_id = str((data.get("publisher") or {}).get("pureId") or "").strip()
    return {"title": title, "issns": issns, "publisher_pure_id": publisher_pure_id}
 
 
def fetch_journal_from_api(
    journal_uuid: str,
    environment: str,
    api_token: str | None,
    cache: dict,
    timeout: float = 10.0,
) -> dict | None:
    if not journal_uuid:
        return None
    cache_key = (environment, journal_uuid)
    if cache_key in cache:
        return cache[cache_key]
    if not api_token:
        print(
            f"  WARNING: no Pure API key available -- cannot fetch journal "
            f"{journal_uuid} from the API.", file=sys.stderr,
        )
        cache[cache_key] = None
        return None
    url = PURE_JOURNAL_API_URLS[environment].format(uuid=journal_uuid)
    request = urllib.request.Request(
        url, headers={"api-key": api_token, "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.load(response)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError) as exc:
        print(
            f"  WARNING: Pure journals API call failed for {journal_uuid} "
            f"({url}): {exc}", file=sys.stderr,
        )
        cache[cache_key] = None
        return None
    parsed = parse_journal_api_response(data)
    cache[cache_key] = parsed
    return parsed
 
 
# ---------------------------------------------------------------------------
# CSV loading
# ---------------------------------------------------------------------------
 
def load_csv_records(csv_path: str) -> tuple[dict[str, dict], dict[str, dict]]:
    by_handle: dict[str, dict] = {}
    by_uuid:   dict[str, dict] = {}
    with open(csv_path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            raw_handle = row.get("handle", "").strip()
            if raw_handle:
                by_handle[build_handle_url(raw_handle)] = row
            dspace_uuid = row.get("uuid", "").strip()
            if dspace_uuid:
                by_uuid[dspace_uuid] = row
    return by_handle, by_uuid
 
 
# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------
 
def find_csv_record(
    json_rec: dict,
    csv_by_uuid: dict[str, dict],
    csv_by_handle: dict[str, dict],
) -> dict | None:
    dspace_uuid = extract_dspace_uuid_from_json_record(json_rec)
    if dspace_uuid and dspace_uuid in csv_by_uuid:
        return csv_by_uuid[dspace_uuid]
    for url in extract_handles_from_json_record(json_rec):
        if url in csv_by_handle:
            return csv_by_handle[url]
    return None
 
 
# ---------------------------------------------------------------------------
# Type resolution
# ---------------------------------------------------------------------------
 
def resolve_publication_type(pure_record: dict, dspace_record: dict) -> tuple[str, str]:
    type_uri = pure_record.get("type", {}).get("uri", "")
    if type_uri and type_uri in PURE_TYPE_MAP:
        return PURE_TYPE_MAP[type_uri]
    dspace_type = (dspace_record.get("dc.type") or "").strip().lower()
    if dspace_type and dspace_type in DSPACE_TYPE_MAP:
        return DSPACE_TYPE_MAP[dspace_type]
    return ("other", "other")
 
 
# ---------------------------------------------------------------------------
# Language helpers
# ---------------------------------------------------------------------------
 
def parse_pure_language(lang_uri: str) -> tuple[str, str]:
    code = lang_uri.split("/")[-1]
    if "_" in code:
        lang, country = code.split("_", 1)
        return lang, country
    return code, "IE"
 
 
def lang_attr(lang_uri: str) -> dict:
    lang, country = parse_pure_language(lang_uri)
    return {"lang": lang, "country": country}
 
 
# ---------------------------------------------------------------------------
# URI suffix helpers
# ---------------------------------------------------------------------------
 
def _uri_suffix(uri: str) -> str:
    return uri.rstrip("/").split("/")[-1]
 
 
def map_license(uri: str) -> str:
    return LICENSE_MAP.get(_uri_suffix(uri), "")
 
def map_access(uri: str) -> str:
    return ACCESS_MAP.get(_uri_suffix(uri), "open")
 
def map_version(uri: str) -> str:
    return VERSION_MAP.get(_uri_suffix(uri), "authorsversion")
 
def map_workflow(step: str) -> str:
    return WORKFLOW_MAP.get(step, "forApproval")
 
def map_visibility(key: str) -> str:
    return VISIBILITY_MAP.get(key, "Public")
 
def map_role(uri: str) -> str:
    return ROLE_MAP.get(_uri_suffix(uri), "author")
 
 
# ---------------------------------------------------------------------------
# Rights -> licence fallback
# ---------------------------------------------------------------------------
 
RIGHTS_TO_LICENSE: dict[str, str] = {
    "cc by":       "cc_by",
    "cc-by":       "cc_by",
    "cc by-nc":    "cc_by_nc",
    "cc by-nc-nd": "cc_by_nc_nd",
    "cc by-nc-sa": "cc_by_nc_sa",
    "cc by-nd":    "cc_by_nd",
    "cc by-sa":    "cc_by_sa",
    "cc0":         "cc0",
}
 
def rights_to_licence(rights: str) -> str:
    return RIGHTS_TO_LICENSE.get(rights.strip().lower(), "")
 
 
# ---------------------------------------------------------------------------
# XML element helpers
# ---------------------------------------------------------------------------
 
def sub(parent: ET.Element, tag: str, text: str | None = None,
        attrib: dict | None = None, ns: str = PUB_NS) -> ET.Element:
    full_tag = f"{{{ns}}}{tag}" if ns else tag
    el = ET.SubElement(parent, full_tag, attrib=attrib or {})
    if text is not None:
        el.text = text
    return el
 
 
def ns2(tag: str) -> str:
    return f"{{{CMN_NS}}}{tag}"
 
 
def text_el(parent: ET.Element, tag: str, text: str, attrib: dict | None = None) -> ET.Element:
    el = ET.SubElement(parent, ns2("text"), attrib=attrib or {})
    el.text = text
    return el
 
 
# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------
 
def build_publication_statuses(parent: ET.Element, pure_record: dict) -> None:
    statuses_el = sub(parent, "publicationStatuses")
    for status in pure_record.get("publicationStatuses", []):
        st_el = sub(statuses_el, "publicationStatus")
        status_uri = status.get("publicationStatus", {}).get("uri", "")
        sub(st_el, "statusType", _uri_suffix(status_uri))
        pub_date = status.get("publicationDate", {})
        if pub_date:
            date_el = sub(st_el, "date")
            if pub_date.get("year"):
                sub(date_el, "year", str(pub_date["year"]), ns=CMN_NS)
            if pub_date.get("month"):
                sub(date_el, "month", str(pub_date["month"]), ns=CMN_NS)
            if pub_date.get("day"):
                sub(date_el, "day", str(pub_date["day"]), ns=CMN_NS)
 
 
def build_title(parent: ET.Element, pure_record: dict, lang_uri: str) -> None:
    title_val = ""
    title_obj = pure_record.get("title", {})
    if isinstance(title_obj, dict):
        title_val = title_obj.get("value", "").strip()
    if not title_val:
        return
    title_el = sub(parent, "title")
    text_el(title_el, "text", title_val, attrib=lang_attr(lang_uri))
 
 
def build_subtitle(parent: ET.Element, dspace_record: dict, lang_uri: str) -> None:
    subtitle = (dspace_record.get("dc.title.subtitle") or "").strip()
    if not subtitle:
        return
    st_el = sub(parent, "subTitle")
    text_el(st_el, "text", subtitle)
 
 
def build_abstract(parent: ET.Element, pure_record: dict, lang_uri: str) -> None:
    abstract_obj = pure_record.get("abstract", {})
    abstract_text = ""
    if isinstance(abstract_obj, dict):
        for v in abstract_obj.values():
            if v:
                abstract_text = v
                break
    if not abstract_text:
        return
    abs_el = sub(parent, "abstract")
    text_el(abs_el, "text", abstract_text, attrib=lang_attr(lang_uri))
 
 
def build_persons(parent: ET.Element, pure_record: dict) -> None:
    contributors = pure_record.get("contributors", [])
    if not contributors:
        return
    persons_el = sub(parent, "persons")
    for contrib in contributors:
        role_uri = contrib.get("role", {}).get("uri", "")
        role_val = map_role(role_uri)
        author_el = sub(persons_el, "author")
        sub(author_el, "role", role_val)
        name = contrib.get("name", {})
        first = (name.get("firstName") or "").strip()
        last  = (name.get("lastName")  or "").strip()
        pure_person_uuid = (
            (contrib.get("person") or {}).get("uuid")
            or (contrib.get("externalPerson") or {}).get("uuid")
            or ""
        )
        person_attrib = {"id": pure_person_uuid} if pure_person_uuid else None
        person_el = sub(author_el, "person", attrib=person_attrib)
        if first:
            sub(person_el, "firstName", first)
        if last:
            sub(person_el, "lastName", last)
        if contrib.get("correspondingAuthor"):
            sub(author_el, "correspondingAuthor", "true")
 
 
REPOSITORY_DOI_PREFIX = "10.13025/"

def is_repository_doi(doi_or_url: str) -> bool:
    """Return True if the value is a repository DOI (10.13025/ prefix)."""
    s = (doi_or_url or "").strip()
    # Accept bare DOIs ("10.13025/xxxxx") and URL forms ("https://doi.org/10.13025/…")
    return "10.13025/" in s


def extract_doi_from_link(url: str) -> str:
    """Extract the bare DOI from a doi.org URL or return the value unchanged."""
    url = (url or "").strip()
    for prefix in ("https://doi.org/", "http://doi.org/", "https://dx.doi.org/", "http://dx.doi.org/"):
        if url.lower().startswith(prefix):
            return url[len(prefix):]
    return url


def build_electronic_versions(
    parent: ET.Element,
    pure_record: dict,
    dspace_record: dict,
    lang_uri: str,
    store_host: str,
    default_version: str,
) -> None:
    evs = pure_record.get("electronicVersions", [])
    dc_rights_licence = rights_to_licence(dspace_record.get("dc.rights") or "")
    has_embargo   = bool((dspace_record.get("dc.date.embargo") or "").strip())
    embargo_start = (dspace_record.get("dc.date.embargo") or "").strip()
    embargo_desc  = (dspace_record.get("dc.description.embargo") or "").strip()

    ev_el: ET.Element | None = None

    def ensure_ev_el() -> ET.Element:
        nonlocal ev_el
        if ev_el is None:
            ev_el = sub(parent, "electronicVersions")
        return ev_el

    def _fill_common(node: ET.Element, version: str, licence: str, access: str) -> None:
        sub(node, "version", version)
        if licence:
            sub(node, "licence", licence)
        if has_embargo:
            sub(node, "publicAccess", "embargoed")
            if embargo_start:
                sub(node, "embargoStartDate", embargo_start)
            if embargo_desc:
                sub(node, "embargoEndDate", embargo_desc)
        else:
            if access:
                sub(node, "publicAccess", access)

    # ── Repository DOIs stored as links[] in the Pure JSON ──────────────────
    # Pure sometimes records a repository DOI (prefix 10.13025/) as a plain
    # URL link rather than a DoiElectronicVersion. Emit them here as
    # DoiElectronicVersion: authorsversion, open access, CC BY 4.0.
    for link in pure_record.get("links", []):
        link_url = (link.get("url") or "").strip()
        if is_repository_doi(link_url):
            doi_val = extract_doi_from_link(link_url)
            doi_el = sub(ensure_ev_el(), "electronicVersionDOI")
            sub(doi_el, "version", "authorsversion")
            sub(doi_el, "licence", "cc_by")
            sub(doi_el, "publicAccess", "open")
            sub(doi_el, "doi", doi_val)

    # ── Build filename → (sequence, dspace_url) lookup from the CSV ─────────
    handle   = (dspace_record.get("handle") or "").strip()
    pdf_link = (dspace_record.get("pdf_links") or "").strip()
    dspace_files = parse_dspace_files(
        pdf_link,
        dspace_record.get("pdf_handle_paths") or "",
        handle,
        store_host,
    )
    dspace_by_filename: dict[str, tuple[str, str]] = {
        normalize_filename_for_match(fn): (seq, url)
        for seq, fn, url in dspace_files
    }
    # Track which DSpace files were consumed by a Pure FileElectronicVersion
    matched_dspace_norms: set[str] = set()
    file_idx = 0

    for ev in evs:
        disc        = ev.get("typeDiscriminator", "")
        licence_uri = ev.get("licenseType", {}).get("uri", "")
        licence     = map_license(licence_uri) or dc_rights_licence
        access_uri  = ev.get("accessType", {}).get("uri", "")
        access      = map_access(access_uri)
        version_uri = ev.get("versionType", {}).get("uri", "")
        version     = map_version(version_uri)

        if disc == "DoiElectronicVersion":
            doi_val = ev.get("doi", "").strip()
            if not doi_val:
                continue
            doi_el = sub(ensure_ev_el(), "electronicVersionDOI")
            _fill_common(doi_el, version, licence, access)
            sub(doi_el, "doi", doi_val)

        elif disc == "FileElectronicVersion":
            file_block = ev.get("file", {})
            file_url   = file_block.get("url", "").strip()
            file_name  = file_block.get("fileName", "").strip()
            mime_type  = file_block.get("mimeType", "application/pdf").strip()
            file_size  = str(file_block.get("size", ""))
            file_id    = file_block.get("fileId", "").strip()
            file_idx  += 1

            if not file_url and not file_name:
                continue

            norm = normalize_filename_for_match(file_name) if file_name else ""

            if norm and norm in dspace_by_filename:
                # ── Case 1: DSpace + Pure match ─────────────────────────────
                # Use DSpace filename and URL; keep Pure file ID.
                seq, dspace_url = dspace_by_filename[norm]
                matched_dspace_norms.add(norm)
                fev_el  = sub(ensure_ev_el(), "electronicVersionFile")
                _fill_common(fev_el, version, licence, access)
                # Pure ID preserved as the file element's id attribute.
                file_el = sub(fev_el, "file", attrib={"id": file_id} if file_id else None)
                sub(file_el, "filename", file_name)
                sub(file_el, "fileLocation", dspace_url)
                sub(file_el, "mimetype", mime_type)
                if file_size:
                    sub(file_el, "filesize", file_size)
            else:
                # ── Case 3: Pure only (no DSpace counterpart) ───────────────
                # Use Pure filename, Pure link, and Pure file ID.
                if not file_url:
                    continue
                fev_el  = sub(ensure_ev_el(), "electronicVersionFile")
                _fill_common(fev_el, version, licence, access)
                file_el = sub(fev_el, "file", attrib={"id": file_id} if file_id else None)
                sub(file_el, "filename", file_name or file_url.split("/")[-1])
                sub(file_el, "fileLocation", file_url)
                sub(file_el, "mimetype", mime_type)
                if file_size:
                    sub(file_el, "filesize", file_size)

        elif disc == "LinkElectronicVersion":
            link_url = ev.get("url", "").strip()
            if not link_url:
                continue
            lev_el = sub(ensure_ev_el(), "electronicVersionLink")
            _fill_common(lev_el, version, licence, access)
            sub(lev_el, "link", link_url)

    if ev_el is None or len(ev_el) == 0:
        dois = (dspace_record.get("dc.identifier.doi") or "").strip()
        if dois:
            for doi_raw in dois.split(";"):
                doi_raw = doi_raw.strip()
                if doi_raw:
                    doi_el = sub(ensure_ev_el(), "electronicVersionDOI")
                    if dc_rights_licence:
                        sub(doi_el, "licence", dc_rights_licence)
                    sub(doi_el, "doi", doi_raw)

    # ── Case 2: DSpace only (no matching Pure FileElectronicVersion) ─────────
    # Use DSpace filename and URL; no id (Pure will assign one after upload).
    for seq, filename, file_location in dspace_files:
        if normalize_filename_for_match(filename) in matched_dspace_norms:
            continue
        fev_el  = sub(ensure_ev_el(), "electronicVersionFile")
        _fill_common(fev_el, version=default_version, licence=dc_rights_licence, access="open")
        # No id attribute — Pure assigns one after the file is uploaded.
        file_el = sub(fev_el, "file")
        sub(file_el, "filename", filename)
        sub(file_el, "fileLocation", file_location)
        sub(file_el, "mimetype", guess_mimetype(filename))
        sub(file_el, "source", store_host)
        sub(file_el, "externalRepositoryState", "STORED")
 
 
def build_existing_stores(parent: ET.Element, dspace_record: dict, store_host: str) -> None:
    handle = (dspace_record.get("handle") or "").strip()
    if not handle:
        return
    es_el = sub(parent, "existingStores")
    e_el  = sub(es_el, "existingStore")
    sub(e_el, "storeName", "DSpace")
    sub(e_el, "updateRequired", "true")
    sub(e_el, "storeContentId", handle)
 
 
def _build_external_ids(
    parent: ET.Element,
    pure_record: dict,
    dspace_uuid: str,
    dspace_record: dict,
) -> None:
    ext_el = sub(parent, "externalIds")
    sub(ext_el, "id", dspace_uuid, attrib={"type": "DSpace"})
    isbn = (dspace_record.get("dc.identifier.isbn") or "").strip()
    if isbn:
        sub(ext_el, "id", isbn, attrib={"type": "isbn"})
    issn = (dspace_record.get("dc.identifier.issn") or "").strip()
    if issn:
        sub(ext_el, "id", issn, attrib={"type": "issn"})
 
 
def build_urls(parent: ET.Element, dspace_record: dict, pure_record: dict) -> None:
    handle_raw = (dspace_record.get("dc.identifier.uri") or "").strip()
    portal_url = pure_record.get("portalUrl", "").strip()
    urls_el = None

    def ensure_urls() -> ET.Element:
        nonlocal urls_el
        if urls_el is None:
            urls_el = sub(parent, "urls")
        return urls_el

    if handle_raw:
        for raw in handle_raw.split(";"):
            raw = raw.strip()
            if raw:
                url_el = sub(ensure_urls(), "url")
                sub(url_el, "url", raw)
                desc_el = sub(url_el, "description")
                text_el(desc_el, "text", "Repository Handle")
                sub(url_el, "type", "unspecified")
    if portal_url:
        url_el = sub(ensure_urls(), "url")
        sub(url_el, "url", portal_url)
        desc_el = sub(url_el, "description")
        text_el(desc_el, "text", "Pure portal link")
        sub(url_el, "type", "unspecified")

    # Add any Pure links[] that are NOT repository DOIs (those are promoted to
    # DoiElectronicVersion in build_electronic_versions).
    for link in pure_record.get("links", []):
        alias    = (link.get("alias") or "").strip()
        link_url = (link.get("url") or "").strip()
        # Skip Handle links (used for matching only) and repository DOIs
        # (promoted to electronicVersionDOI with authorsversion/cc_by/open).
        if not link_url or alias == "Handle" or is_repository_doi(link_url):
            continue
        url_el = sub(ensure_urls(), "url")
        sub(url_el, "url", link_url)
        if alias:
            desc_el = sub(url_el, "description")
            text_el(desc_el, "text", alias)
        sub(url_el, "type", "unspecified")
 
 
def resolve_journal_info(
    pure_record: dict,
    dspace_record: dict,
    environment: str,
    api_token: str | None,
    api_cache: dict,
) -> dict:
    journal_assoc  = pure_record.get("journalAssociation") or {}
    journal_obj    = journal_assoc.get("journal") or {}
    journal_uuid   = (journal_obj.get("uuid") or "").strip()
    journal_pure_id = str(journal_obj.get("pureId") or "").strip()
    title          = ((journal_assoc.get("title") or {}).get("title") or "").strip()
    if not title:
        title = (dspace_record.get("journal_title") or "").strip()
    if not title:
        title = (dspace_record.get("dc.identifier.journal") or "").strip()
    issns: list[str] = []
    csv_issn = (dspace_record.get("dc.identifier.issn") or "").strip()
    if csv_issn:
        issns = [v.strip() for v in re.split(r"[;,]", csv_issn) if v.strip()]
    publisher_pure_id = ""
    if journal_uuid and (not title or not issns):
        api_data = fetch_journal_from_api(journal_uuid, environment, api_token, api_cache)
        if api_data:
            if not title and api_data.get("title"):
                title = api_data["title"]
            if not issns and api_data.get("issns"):
                issns = api_data["issns"]
            if api_data.get("publisher_pure_id"):
                publisher_pure_id = api_data["publisher_pure_id"]
    return {
        "uuid": journal_uuid,
        "pure_id": journal_pure_id,
        "title": title,
        "issns": issns,
        "publisher_pure_id": publisher_pure_id,
    }
 
 
def build_journal(
    parent: ET.Element,
    pure_record: dict,
    dspace_record: dict,
    environment: str,
    api_token: str | None,
    api_cache: dict,
) -> bool:
    """
    Write <journal> and return True.
    Returns False (without writing anything) when no identifying information
    could be found anywhere -- the caller should then skip the record, because
    an empty <journal/> fails schema validation.
    """
    info = resolve_journal_info(pure_record, dspace_record, environment, api_token, api_cache)
    if not info["uuid"] and not info["pure_id"] and not info["title"] and not info["issns"]:
        return False
    j_el = sub(parent, "journal", attrib={"id": info["pure_id"]} if info["pure_id"] else None)
    if info["title"]:
        sub(j_el, "title", info["title"])
    if info["issns"]:
        issns_el = sub(j_el, "printIssns")
        for issn in info["issns"]:
            sub(issns_el, "issn", issn)
    if info["publisher_pure_id"]:
        sub(j_el, "publisher", attrib={"id": info["publisher_pure_id"]})
    return True
 
 
def build_publisher(parent: ET.Element, dspace_record: dict) -> None:
    publisher = (dspace_record.get("publisher_name") or dspace_record.get("dc.publisher") or "").strip()
    if not publisher:
        return
    pub_el = sub(parent, "publisher")
    sub(pub_el, "name", publisher)
 
 
def build_isbns(parent: ET.Element, dspace_record: dict) -> None:
    isbn = (dspace_record.get("dc.identifier.isbn") or "").strip()
    if not isbn:
        return
    isbns_el = sub(parent, "printIsbns")
    for raw in isbn.split(";"):
        raw = raw.strip()
        if raw:
            sub(isbns_el, "isbn", raw)
 
 
def build_funding_text(parent: ET.Element, dspace_record: dict, lang_uri: str) -> None:
    sponsorship = (dspace_record.get("dc.description.sponsorship") or "").strip()
    if not sponsorship:
        return
    ft_el = sub(parent, "fundingText")
    text_el(ft_el, "text", sponsorship, attrib=lang_attr(lang_uri))
 
 
# ---------------------------------------------------------------------------
# Main record builder
# ---------------------------------------------------------------------------
 
def build_record_element(
    root: ET.Element,
    pure_record: dict,
    dspace_record: dict,
    store_host: str,
    default_version: str,
    environment: str,
    api_token: str | None,
    api_cache: dict,
) -> None:
    pure_id  = str(pure_record.get("pureId", ""))
    lang_uri = pure_record.get("language", {}).get("uri", "")
    xml_tag, sub_type = resolve_publication_type(pure_record, dspace_record)
    rec_el = ET.SubElement(
        root,
        f"{{{PUB_NS}}}{xml_tag}",
        attrib={"id": pure_id, "subType": sub_type},
    )
    peer = pure_record.get("peerReview")
    if peer is None:
        peer_str = (dspace_record.get("dc.description.peer-reviewed") or "").strip().lower()
        peer = peer_str == "peer-reviewed"
    sub(rec_el, "peerReviewed", "true" if peer else "false")

    # publicationCategory must come immediately after peerReviewed and before
    # publicationStatuses (XSD sequence order). Pure's category URIs live under
    # /dk/atira/pure/researchoutput/category/. All records default to "research"
    # which avoids the "No publication category specified" importer warning.
    category_uri = (
        (pure_record.get("category") or {}).get("uri")
        or "/dk/atira/pure/researchoutput/category/research"
    )
    # publicationCategory takes only the trailing classification token,
    # not the full URI. Strip everything up to and including the last "/".
    category_val = category_uri.rstrip("/").rsplit("/", 1)[-1]
    sub(rec_el, "publicationCategory", category_val)

    build_publication_statuses(rec_el, pure_record)
    workflow_step = pure_record.get("workflow", {}).get("step", "")
    workflow_val  = map_workflow(workflow_step)
    if workflow_val:
        sub(rec_el, "workflow", workflow_val)
    if lang_uri:
        lang, country = parse_pure_language(lang_uri)
        sub(rec_el, "language", f"{lang}_{country}")
    else:
        dc_lang = (dspace_record.get("dc.language.iso") or "").strip()
        if dc_lang:
            sub(rec_el, "language", dc_lang)
    build_title(rec_el, pure_record, lang_uri or "/dk/atira/pure/core/languages/en_IE")
    build_subtitle(rec_el, dspace_record, lang_uri or "/dk/atira/pure/core/languages/en_IE")
    build_abstract(rec_el, pure_record, lang_uri or "/dk/atira/pure/core/languages/en_IE")
    build_persons(rec_el, pure_record)
    orgs = pure_record.get("organizations", [])
    if orgs:
        orgs_el = sub(rec_el, "organisations")
        for org in orgs:
            org_uuid = org.get("uuid", "")
            if org_uuid:
                sub(orgs_el, "organisation", attrib={"id": org_uuid})
        # If no org had a uuid, remove the empty <organisations/> element
        # to avoid schema validation failure (minOccurs=1 on <organisation>).
        if len(orgs_el) == 0:
            rec_el.remove(orgs_el)
    managing_org = pure_record.get("managingOrganization", {})
    owner_uuid   = managing_org.get("uuid", "")
    if owner_uuid:
        sub(rec_el, "owner", attrib={"id": owner_uuid})
    build_urls(rec_el, dspace_record, pure_record)
    build_electronic_versions(rec_el, pure_record, dspace_record, lang_uri, store_host, default_version)
    build_existing_stores(rec_el, dspace_record, store_host)
    vis_key = pure_record.get("visibility", {}).get("key", "FREE")
    sub(rec_el, "visibility", map_visibility(vis_key))
    dspace_uuid = (dspace_record.get("uuid") or "").strip()
    _build_external_ids(rec_el, pure_record, dspace_uuid, dspace_record)
    build_funding_text(rec_el, dspace_record, lang_uri or "/dk/atira/pure/core/languages/en_IE")
    _add_type_specific_fields(
        rec_el, xml_tag, pure_record, dspace_record, lang_uri,
        environment, api_token, api_cache,
    )
 
 
def _add_type_specific_fields(
    rec_el: ET.Element,
    xml_tag: str,
    pure_record: dict,
    dspace_record: dict,
    lang_uri: str,
    environment: str,
    api_token: str | None,
    api_cache: dict,
) -> None:
    if xml_tag in ("contributionToJournal", "contributionToSpecialist", "contributionToSpecialist"):
        if not build_journal(rec_el, pure_record, dspace_record, environment, api_token, api_cache):
            # No journal information available — downgrade to <other> rather than
            # skipping the record entirely, and warn so the gap can be reviewed.
            print(
                f"  WARNING: pureId={pure_record.get('pureId', '?')} mapped as "
                f"{xml_tag!r} but no journal info found; downgrading to <other>.",
                file=sys.stderr,
            )
            rec_el.tag = f"{{{PUB_NS}}}other"
            rec_el.set("subType", "other")
            # <other> needs no type-specific child elements — nothing more to do.
    elif xml_tag in ("book",):
        build_isbns(rec_el, dspace_record)
        build_publisher(rec_el, dspace_record)
    elif xml_tag == "chapterInBook":
        build_isbns(rec_el, dspace_record)
        alt_title = (
            (dspace_record.get("dc.title.alternative") or "").strip()
            or (dspace_record.get("dc.relation.ispartof") or "").strip()
            or "\u2014"
        )
        sub(rec_el, "hostPublicationTitle", alt_title)
        build_publisher(rec_el, dspace_record)
    elif xml_tag == "workingPaper":
        build_publisher(rec_el, dspace_record)
    elif xml_tag == "thesis":
        quality = (dspace_record.get("dc.type") or "").strip().lower()
        if "phd" in quality or "doctoral" in quality:
            sub(rec_el, "qualification", "phd")
        elif "master" in quality:
            sub(rec_el, "qualification", "mphil")
        else:
            sub(rec_el, "qualification", "phd")
        build_publisher(rec_el, dspace_record)
 
 
# ---------------------------------------------------------------------------
# XML serialisation
# ---------------------------------------------------------------------------
 
_CMN_TEMP_PREFIX = "cmn"
 
 
def register_namespaces() -> None:
    ET.register_namespace("",               PUB_NS)
    ET.register_namespace(_CMN_TEMP_PREFIX, CMN_NS)
 
 
def build_xml(
    matched_pairs: list[tuple[dict, dict]],
    store_host: str,
    default_version: str,
    environment: str,
    api_token: str | None,
    api_cache: dict,
) -> ET.Element:
    register_namespaces()
    root = ET.Element(f"{{{PUB_NS}}}publications")
    for pure_rec, dspace_rec in matched_pairs:
        try:
            build_record_element(
                root, pure_rec, dspace_rec, store_host, default_version,
                environment, api_token, api_cache,
            )
        except Exception as exc:
            pure_id = pure_rec.get("pureId", "?")
            print(f"  WARNING: skipped pureId={pure_id} -- {exc}", file=sys.stderr)
    return root
 
 
def pretty_print(root: ET.Element) -> str:
    raw = ET.tostring(root, encoding="unicode", xml_declaration=False)
    raw = raw.replace(f"xmlns:{_CMN_TEMP_PREFIX}=", "xmlns:ns2=")
    raw = raw.replace(f"{_CMN_TEMP_PREFIX}:", "ns2:")
    from xml.dom import minidom
    dom = minidom.parseString('<?xml version="1.0"?>\n' + raw)
    pretty = dom.toprettyxml(indent="    ", encoding=None)
    lines = pretty.split("\n")
    if lines[0].startswith("<?xml"):
        lines = lines[1:]
    header = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    return header + "\n" + "\n".join(lines)
 
 
# ---------------------------------------------------------------------------
# Filtering helpers
# ---------------------------------------------------------------------------
 
def parse_iso_datetime(dt_string: str) -> datetime:
    dt_string = dt_string.rstrip("Z") + "+00:00"
    return datetime.fromisoformat(dt_string)
 
 
def filter_json_records(
    records: list[dict],
    modified_by: str | None,
    modified_after: datetime | None,
) -> list[dict]:
    filtered = []
    for rec in records:
        if modified_by and rec.get("modifiedBy", "") != modified_by:
            continue
        if modified_after:
            mod_str = rec.get("modifiedDate", "")
            if not mod_str:
                continue
            try:
                if parse_iso_datetime(mod_str) <= modified_after:
                    continue
            except ValueError:
                continue
        filtered.append(rec)
    return filtered
 
 
# ---------------------------------------------------------------------------
# Duplicate resolution
# ---------------------------------------------------------------------------
 
def resolve_duplicates(pairs: list[tuple[dict, dict]]) -> list[tuple[dict, dict]]:
    by_dspace: dict[str, list[tuple[dict, dict]]] = defaultdict(list)
    for pure_rec, dspace_rec in pairs:
        dspace_uuid = (dspace_rec.get("uuid") or "").strip()
        by_dspace[dspace_uuid].append((pure_rec, dspace_rec))
    result: list[tuple[dict, dict]] = []
    seen_pure_ids: set[str] = set()
    for dspace_uuid, group in by_dspace.items():
        if len(group) > 1:
            def _mod_dt(pair: tuple) -> datetime:
                try:
                    return parse_iso_datetime(pair[0].get("modifiedDate", ""))
                except Exception:
                    return datetime.min.replace(tzinfo=timezone.utc)
            winner = max(group, key=_mod_dt)
            print(
                f"  WARNING: DSpace UUID {dspace_uuid} matches "
                f"{len(group)} Pure records. Keeping pureId="
                f"{winner[0].get('pureId')} (most recent).",
                file=sys.stderr,
            )
            group = [winner]
        for pair in group:
            pid = str(pair[0].get("pureId", ""))
            if pid not in seen_pure_ids:
                seen_pure_ids.add(pid)
                result.append(pair)
    return result
 
 
# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
 
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Match DSpace CSV and Pure JSON records, then emit a Pure "
            "Research Output Import XML file."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--csv",  required=True, metavar="CSV_FILE",
                        help="Path to the DSpace CSV export.")
    parser.add_argument("--json", required=True, metavar="JSON_FILE",
                        help="Path to the Pure JSON export.")
    parser.add_argument("--environment", required=True, choices=sorted(DSPACE_BASE_URLS),
                        help=(
                            "Which DSpace environment's base URL to use for "
                            "<existingStores>/<storeName>, <electronicVersionFile>/"
                            "<source>, and rewriting DSpace bitstream links: "
                            f"test -> {DSPACE_BASE_URLS['test']} , "
                            f"prod -> {DSPACE_BASE_URLS['prod']}"
                        ))
    parser.add_argument("--default-version", default="publishersversion",
                        metavar="VERSION_TYPE",
                        help=(
                            "electronicVersionFile/version value to use for "
                            "files that exist only in DSpace (no Pure "
                            "versionType to draw from). Default: publishersversion."
                        ))
    parser.add_argument("--output", default=None, metavar="XML_FILE",
                        help=("Output XML path. Defaults to "
                              "pure_import_YYYY-MM-DD.xml in the current directory."))
    parser.add_argument("--modified-by", default=None, metavar="USER",
                        help="Only include Pure records whose modifiedBy equals USER.")
    parser.add_argument("--modified-after", default=None, metavar="YYYY-MM-DD",
                        help="Only include Pure records modified after this date.")
    return parser.parse_args()
 
 
def main() -> None:
    args = parse_args()
 
    today = datetime.now().strftime("%Y-%m-%d")
    output_path = args.output or f"./xml_import/{args.environment}_pure_import_{today}.xml"

    # Derive the log path from the XML output path (same dir + base name, .log).
    log_path = os.path.splitext(output_path)[0] + ".log"
    _log_fh = _start_logging(log_path)

    store_base_url = DSPACE_BASE_URLS[args.environment]
    store_host = urlsplit(store_base_url).netloc
    print(f"Environment: {args.environment} -> {store_base_url} (store host: {store_host})")
 
    # ---- Pure API key (last-resort journal fallback) -----------------------
    # load_dotenv loads variables from the .env file into os.environ without
    # overwriting variables already present in the process environment
    # (override=False is the default, so real env vars take precedence).
    load_dotenv()
    api_key_var = PURE_API_KEY_VARS[args.environment]
    api_token = os.environ.get(api_key_var)
    if api_token:
        print(f"Pure API key: found ({api_key_var}).")
    else:
        print(f"Pure API key: not found ({api_key_var} not set in .env or "
              f"the environment) -- journal lookups will be limited to "
              f"Pure JSON and the DSpace CSV.")
    api_cache: dict = {}
 
    # ---- Load CSV ----------------------------------------------------------
    print(f"Loading DSpace CSV: {args.csv}")
    csv_by_handle, csv_by_uuid = load_csv_records(args.csv)
    print(f"  -> {len(csv_by_uuid)} records by UUID, "
          f"{len(csv_by_handle)} by Handle loaded.")
 
    # ---- Load JSON ---------------------------------------------------------
    print(f"Loading Pure JSON: {args.json}")
    with open(args.json, encoding="utf-8") as fh:
        json_records: list[dict] = json.load(fh)
    if not isinstance(json_records, list):
        json_records = [json_records]
    print(f"  -> {len(json_records)} records loaded.")
 
    # ---- Optional filters --------------------------------------------------
    modified_after_dt: datetime | None = None
    if args.modified_after:
        try:
            modified_after_dt = datetime.strptime(
                args.modified_after, "%Y-%m-%d"
            ).replace(tzinfo=timezone.utc)
        except ValueError:
            print(
                f"Error: --modified-after '{args.modified_after}' "
                "is not YYYY-MM-DD.",
                file=sys.stderr,
            )
            sys.exit(1)
    if args.modified_by or modified_after_dt:
        json_records = filter_json_records(
            json_records,
            modified_by=args.modified_by,
            modified_after=modified_after_dt,
        )
        print(f"  -> {len(json_records)} records after filtering.")
 
    # ---- Match -------------------------------------------------------------
    print("Matching records...")

    # Build a set of all DSpace UUIDs and handles present in the CSV so we can
    # report which DSpace rows were never claimed by any Pure record.
    all_dspace_uuids: set[str] = set(csv_by_uuid.keys())
    all_dspace_handles: set[str] = set(csv_by_handle.keys())
    matched_dspace_uuids: set[str] = set()
    matched_dspace_handles: set[str] = set()

    matched: list[tuple[dict, dict]] = []
    for pure_rec in json_records:
        dspace_rec = find_csv_record(pure_rec, csv_by_uuid, csv_by_handle)
        if dspace_rec is None:
            continue
        # Track which DSpace identifiers were consumed.
        d_uuid = (dspace_rec.get("uuid") or "").strip()
        if d_uuid:
            matched_dspace_uuids.add(d_uuid)
        d_handle_raw = (dspace_rec.get("handle") or "").strip()
        if d_handle_raw:
            matched_dspace_handles.add(build_handle_url(d_handle_raw))
        matched.append((pure_rec, dspace_rec))

    # Identify DSpace rows that were never matched to any Pure record.
    unmatched_dspace_uuids = all_dspace_uuids - matched_dspace_uuids
    # A row may have been matched via UUID even if its handle appears in the
    # unmatched-handle set, so exclude handles whose UUID was matched.
    unmatched_dspace_rows: list[dict] = []
    seen_unmatched_uuids: set[str] = set()
    for uid in unmatched_dspace_uuids:
        row = csv_by_uuid[uid]
        if uid not in seen_unmatched_uuids:
            seen_unmatched_uuids.add(uid)
            unmatched_dspace_rows.append(row)
    # Also catch rows that have a handle but no UUID, and were not matched.
    for handle_url, row in csv_by_handle.items():
        uid = (row.get("uuid") or "").strip()
        if uid in matched_dspace_uuids:
            continue  # already matched via UUID
        if handle_url in matched_dspace_handles:
            continue  # matched via handle
        if uid and uid in seen_unmatched_uuids:
            continue  # already listed
        if uid:
            seen_unmatched_uuids.add(uid)
        unmatched_dspace_rows.append(row)

    for row in unmatched_dspace_rows:
        title = (row.get("dc.title") or row.get("title") or "").strip() or "(no title)"
        handle = (row.get("handle") or "").strip() or "(no handle)"
        uuid   = (row.get("uuid")   or "").strip() or "(no uuid)"
        print(
            f"  WARNING: DSpace row not matched to any Pure record -- "
            f"uuid={uuid!r} handle={handle!r} title={title!r}",
            file=sys.stderr,
        )

    print(
        f"  -> {len(matched)} matched, "
        f"{len(unmatched_dspace_rows)} DSpace rows without a Pure match."
    )
 
    # ---- Duplicate resolution ----------------------------------------------
    matched = resolve_duplicates(matched)
    print(f"  -> {len(matched)} records after duplicate resolution.")
 
    if not matched:
        print("No records to export. Exiting.", file=sys.stderr)
        sys.exit(0)
 
    # ---- Build XML ---------------------------------------------------------
    print("Building XML...")
    root = build_xml(matched, store_host, args.default_version, args.environment, api_token, api_cache)
 
    # ---- Write output ------------------------------------------------------
    xml_str = pretty_print(root)
    with open(output_path, "w", encoding="utf-8") as fh:
        fh.write(xml_str)
    print(f"XML written to: {output_path}")
    print(f"  -> {len(root)} publication element(s) exported.")
    print(f"Log written to: {log_path}")

    # Flush and close the log file, restore real stdout/stderr.
    sys.stdout.flush()
    sys.stderr.flush()
    sys.stdout = sys.__stdout__
    sys.stderr = sys.__stderr__
    _log_fh.close()
 
 
if __name__ == "__main__":
    main()