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
  - The XML element id attribute (e.g. <contributionToJournal id="…">) is the
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
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from urllib.parse import unquote, urlsplit, urlunsplit
from xml.etree import ElementTree as ET
from xml.dom import minidom

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

HANDLE_BASE_URL = "http://hdl.handle.net/"

# DSpace repository base URLs, selected via the --environment CLI argument.
# Used to (a) derive the DSpace "store" hostname written into
# <existingStores>/<existingStore>/<storeName> and <electronicVersionFile>/
# <source>, and (b) rewrite DSpace bitstream links (the "pdf_links" CSV
# column) onto the chosen environment's domain.
DSPACE_BASE_URLS: dict[str, str] = {
    "test": "https://galway.dspace7-test.openrepository.com/",
    "prod": "https://researchrepository.universityofgalway.ie/",
}

PUB_NS  = "v1.publication-import.base-uk.pure.atira.dk"
CMN_NS  = "v3.commons.pure.atira.dk"

# ── Pure typeDiscriminator / type URI → (XML element tag, subType) ──────────
#
# The XML tag is the Pure content-type element name used in the import schema.
# subType is the attribute on that element.
#
# DSpace dc.type values also contribute to this mapping when the Pure JSON
# typeDiscriminator alone is ambiguous.

PURE_TYPE_MAP: dict[str, tuple[str, str]] = {
    # Journal / periodical
    "/dk/atira/pure/researchoutput/researchoutputtypes/contributiontojournal/article":            ("contributionToJournal", "article"),
    "/dk/atira/pure/researchoutput/researchoutputtypes/contributiontojournal/review":             ("contributionToJournal", "review"),
    "/dk/atira/pure/researchoutput/researchoutputtypes/contributiontojournal/letter":             ("contributionToJournal", "letter"),
    "/dk/atira/pure/researchoutput/researchoutputtypes/contributiontojournal/editorialnote":      ("contributionToJournal", "editorialnote"),
    "/dk/atira/pure/researchoutput/researchoutputtypes/contributiontojournal/shortsurvey":        ("contributionToJournal", "shortsurvey"),
    "/dk/atira/pure/researchoutput/researchoutputtypes/contributiontojournal/comment":            ("contributionToJournal", "comment"),
    "/dk/atira/pure/researchoutput/researchoutputtypes/contributiontojournal/conferencearticle":  ("contributionToJournal", "conferencearticle"),
    # Conference
    "/dk/atira/pure/researchoutput/researchoutputtypes/contributiontoconference/paper":           ("contributionToConference", "paper"),
    "/dk/atira/pure/researchoutput/researchoutputtypes/contributiontoconference/poster":          ("contributionToConference", "poster"),
    "/dk/atira/pure/researchoutput/researchoutputtypes/contributiontoconference/abstract":        ("contributionToConference", "abstract"),
    "/dk/atira/pure/researchoutput/researchoutputtypes/contributiontoconference/other":           ("contributionToConference", "other"),
    # Book / anthology
    "/dk/atira/pure/researchoutput/researchoutputtypes/bookanthology/book":                       ("book", "book"),
    "/dk/atira/pure/researchoutput/researchoutputtypes/bookanthology/anthology":                  ("book", "anthology"),
    "/dk/atira/pure/researchoutput/researchoutputtypes/bookanthology/commissioned_report":        ("book", "book"),
    "/dk/atira/pure/researchoutput/researchoutputtypes/bookanthology/report":                     ("book", "book"),
    # Chapter
    "/dk/atira/pure/researchoutput/researchoutputtypes/contributiontobookanthology/chapter":      ("chapterInBook", "chapter"),
    "/dk/atira/pure/researchoutput/researchoutputtypes/contributiontobookanthology/conference":   ("chapterInBook", "conference"),
    "/dk/atira/pure/researchoutput/researchoutputtypes/contributiontobookanthology/foreword":     ("chapterInBook", "foreword"),
    "/dk/atira/pure/researchoutput/researchoutputtypes/contributiontobookanthology/other":        ("chapterInBook", "chapter"),
    # Working paper
    "/dk/atira/pure/researchoutput/researchoutputtypes/workingpaper/workingpaper":                ("workingPaper", "workingpaper"),
    # Thesis
    "/dk/atira/pure/researchoutput/researchoutputtypes/thesis/doctoral":                          ("thesis", "phd"),
    "/dk/atira/pure/researchoutput/researchoutputtypes/thesis/master":                            ("thesis", "master"),
    "/dk/atira/pure/researchoutput/researchoutputtypes/thesis/bachelor":                          ("thesis", "bachelor"),
    "/dk/atira/pure/researchoutput/researchoutputtypes/thesis/other":                             ("thesis", "other"),
    # Non-textual / digital
    "/dk/atira/pure/researchoutput/researchoutputtypes/nontextual/digitalorvisualproducts":       ("nonTextual", "digitalorvisualproducts"),
    "/dk/atira/pure/researchoutput/researchoutputtypes/nontextual/database":                      ("nonTextual", "database"),
    "/dk/atira/pure/researchoutput/researchoutputtypes/nontextual/software":                      ("nonTextual", "software"),
    # Other
    "/dk/atira/pure/researchoutput/researchoutputtypes/other/other":                              ("other", "other"),
    "/dk/atira/pure/researchoutput/researchoutputtypes/other/report":                             ("other", "report"),
    # Specialist publication
    "/dk/atira/pure/researchoutput/researchoutputtypes/contributiontospecialistpublication/article": ("contributionToSpecialist", "article"),
    "/dk/atira/pure/researchoutput/researchoutputtypes/contributiontospecialistpublication/review":  ("contributionToSpecialist", "review"),
    # Patent
    "/dk/atira/pure/researchoutput/researchoutputtypes/patent/patent":                            ("patent", "patent"),
    # Memorandum
    "/dk/atira/pure/researchoutput/researchoutputtypes/memorandum/academicmemorandum":            ("memorandum", "academicmemorandum"),
}

# Fallback: DSpace dc.type → (XML element tag, subType)
DSPACE_TYPE_MAP: dict[str, tuple[str, str]] = {
    "journal article":       ("contributionToJournal", "article"),
    "review article":        ("contributionToJournal", "review"),
    "review":                ("contributionToJournal", "review"),
    "book review":           ("contributionToJournal", "review"),
    "conference paper":      ("contributionToConference", "paper"),
    "conference output":     ("contributionToConference", "other"),
    "conference poster":     ("contributionToConference", "poster"),
    "conference proceedings": ("contributionToConference", "other"),
    "book":                  ("book", "book"),
    "book part":             ("chapterInBook", "chapter"),
    "report":                ("book", "book"),
    "working paper":         ("workingPaper", "workingpaper"),
    "newspaper article":     ("contributionToSpecialist", "article"),
    "video":                 ("nonTextual", "digitalorvisualproducts"),
    "interactive resource":  ("nonTextual", "digitalorvisualproducts"),
    "data management plan":  ("other", "other"),
    "other":                 ("other", "other"),
}

# Pure license URI suffix → XML import licence value
LICENSE_MAP: dict[str, str] = {
    "cc_by":          "cc_by",
    "cc_by_nc":       "cc_by_nc",
    "cc_by_nc_nd":    "cc_by_nc_nd",
    "cc_by_nc_sa":    "cc_by_nc_sa",
    "cc_by_nd":       "cc_by_nd",
    "cc_by_sa":       "cc_by_sa",
    "cc0":            "cc0",
}

# Pure access type URI suffix → XML publicAccess value
ACCESS_MAP: dict[str, str] = {
    "open":       "open",
    "closed":     "closed",
    "embargoed":  "embargoed",
    "unknown":    "unknown",
}

# Pure electronic version type URI suffix → XML version value
VERSION_MAP: dict[str, str] = {
    "publishersversion":  "publishersversion",
    "authorsversion":     "authorsversion",
    "preprintversion":    "preprintversion",
    "proofversion":       "proofversion",
}

# Pure workflow step → XML workflow value
WORKFLOW_MAP: dict[str, str] = {
    "entryInProgress": "entryInProgress",
    "forApproval": "forApproval",
    "approved":  "approved",
    "validated": "validated",
}

# Pure visibility key → XML visibility value
VISIBILITY_MAP: dict[str, str] = {
    "FREE":       "Public",
    "BACKEND":    "Restricted",
    "CAMPUS":     "Restricted",
    "RESTRICTED": "Restricted",
}

# Pure contributor role URI suffix → XML role value
ROLE_MAP: dict[str, str] = {
    "author":    "author",
    "editor":    "editor",
    "translator": "translator",
    "illustrator": "illustrator",
    "inventor":  "inventor",
    "supervisor": "supervisor",
}

# ---------------------------------------------------------------------------
# ID / handle utilities  (taken directly from get_file_ids.py)
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
    """
    Rewrite a DSpace bitstream URL onto the chosen environment's host,
    keeping the path/query/fragment untouched. If the URL can't be parsed
    as absolute (missing scheme/netloc), it is returned unchanged.
    """
    url = (url or "").strip()
    if not url:
        return url
    parts = urlsplit(url)
    if not parts.scheme or not parts.netloc:
        return url
    return urlunsplit((target_scheme, target_netloc, parts.path, parts.query, parts.fragment))


def normalize_filename_for_match(name: str) -> str:
    """Normalize a filename for cross-source comparison (Pure vs DSpace)."""
    return unquote(name or "").strip().casefold()


def parse_pdf_handle_path(path: str, handle: str) -> tuple[str, str] | tuple[None, None]:
    """
    Parse a DSpace "pdf_handle_paths" value such as
    "/10379/17513/1/10-Michael%20Bharry%20%C3%93%20Flatharta.pdf" into
    (sequence, filename), e.g. ("1", "10-Michael Bharry Ó Flatharta.pdf").

    The leading "/{handle}/" prefix is stripped if present (handle is
    expected to match the CSV's own "handle" column for that row); the
    remainder is split into "{sequence}/{filename}". Returns (None, None)
    if no usable filename can be derived.
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

    filename = unquote(filename).strip()
    sequence = sequence.strip() or "1"
    if not filename:
        return None, None
    return sequence, filename


def guess_mimetype(filename: str) -> str:
    guessed, _ = mimetypes.guess_type(filename or "")
    return guessed or "application/pdf"


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
    """Return the DSpace CSV row that matches this Pure JSON record, or None."""
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

def resolve_publication_type(
    pure_record: dict,
    dspace_record: dict,
) -> tuple[str, str]:
    """
    Return (xml_tag, subType) by inspecting the Pure type URI first,
    then falling back to the DSpace dc.type string.
    """
    type_uri = pure_record.get("type", {}).get("uri", "")
    if type_uri and type_uri in PURE_TYPE_MAP:
        return PURE_TYPE_MAP[type_uri]

    # Fallback: DSpace dc.type (lower-cased, stripped)
    dspace_type = (dspace_record.get("dc.type") or "").strip().lower()
    if dspace_type and dspace_type in DSPACE_TYPE_MAP:
        return DSPACE_TYPE_MAP[dspace_type]

    return ("other", "other")


# ---------------------------------------------------------------------------
# Language helpers
# ---------------------------------------------------------------------------

def parse_pure_language(lang_uri: str) -> tuple[str, str]:
    """
    Convert a Pure language URI like /dk/atira/pure/core/languages/en_IE
    into (lang, country) → ("en", "IE").
    Returns ("en", "GB") as a safe default.
    """
    code = lang_uri.split("/")[-1]   # e.g. "en_IE"
    if "_" in code:
        lang, country = code.split("_", 1)
        return lang, country
    return code, "GB"


def lang_attr(lang_uri: str) -> dict:
    """Build {lang=…, country=…} attribute dict from a Pure language URI."""
    lang, country = parse_pure_language(lang_uri)
    return {"lang": lang, "country": country}


# ---------------------------------------------------------------------------
# URI suffix helpers
# ---------------------------------------------------------------------------

def _uri_suffix(uri: str) -> str:
    """Return the last path segment of a URI."""
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
# Rights → licence fallback (from DSpace dc.rights)
# ---------------------------------------------------------------------------

RIGHTS_TO_LICENSE: dict[str, str] = {
    "cc by":          "cc_by",
    "cc-by":          "cc_by",
    "cc by-nc":       "cc_by_nc",
    "cc by-nc-nd":    "cc_by_nc_nd",
    "cc by-nc-sa":    "cc_by_nc_sa",
    "cc by-nd":       "cc_by_nd",
    "cc by-sa":       "cc_by_sa",
    "cc0":            "cc0",
}


def rights_to_licence(rights: str) -> str:
    """Convert a dc.rights string to a Pure licence code."""
    return RIGHTS_TO_LICENSE.get(rights.strip().lower(), "")


# ---------------------------------------------------------------------------
# XML element helpers
# ---------------------------------------------------------------------------

def sub(parent: ET.Element, tag: str, text: str | None = None,
        attrib: dict | None = None, ns: str = "") -> ET.Element:
    """Create and append a child element, optionally with text and attributes."""
    full_tag = f"{{{ns}}}{tag}" if ns else tag
    el = ET.SubElement(parent, full_tag, attrib=attrib or {})
    if text is not None:
        el.text = text
    return el


def ns2(tag: str) -> str:
    return f"{{{CMN_NS}}}{tag}"


def text_el(parent: ET.Element, tag: str, text: str,
            attrib: dict | None = None) -> ET.Element:
    """Append a <ns2:text> child to parent with optional lang/country attrs."""
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
                sub(date_el, ns2("year"), str(pub_date["year"]))
            if pub_date.get("month"):
                sub(date_el, ns2("month"), str(pub_date["month"]))
            if pub_date.get("day"):
                sub(date_el, ns2("day"), str(pub_date["day"]))


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
        # Try the language-specific key first, then any value
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

        person_el = sub(author_el, "person")
        if first:
            sub(person_el, "firstName", first)
        if last:
            sub(person_el, "lastName", last)

        if contrib.get("correspondingAuthor"):
            sub(author_el, "correspondingAuthor", "true")


def build_electronic_versions(
    parent: ET.Element,
    pure_record: dict,
    dspace_record: dict,
    lang_uri: str,
    store_host: str,
    default_version: str,
) -> None:
    evs = pure_record.get("electronicVersions", [])

    # Derive a fallback licence from dc.rights
    dc_rights_licence = rights_to_licence(dspace_record.get("dc.rights") or "")
    # Derive a fallback access value from dc.date.embargo
    has_embargo = bool((dspace_record.get("dc.date.embargo") or "").strip())
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

    matched_filenames: set[str] = set()
    file_idx = 0

    for ev in evs:
        disc = ev.get("typeDiscriminator", "")
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

            if file_name:
                matched_filenames.add(normalize_filename_for_match(file_name))

            if not file_url:
                continue

            fev_el = sub(ensure_ev_el(), "electronicVersionFile")
            _fill_common(fev_el, version, licence, access)

            file_el = sub(fev_el, "file", attrib={"id": file_id or f"file{file_idx}"})
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

    # If no electronicVersions were written but a DOI is on the CSV, add one
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

    # ── DSpace-only file fallback ───────────────────────────────────────────
    # If the DSpace CSV row points at a bitstream (pdf_links/pdf_handle_paths)
    # whose filename isn't already represented among Pure's
    # FileElectronicVersion entries, add it as a new electronicVersionFile so
    # it isn't lost on import.
    pdf_link = (dspace_record.get("pdf_links") or "").strip()
    handle   = (dspace_record.get("handle") or "").strip()
    if pdf_link and handle:
        sequence, filename = parse_pdf_handle_path(
            dspace_record.get("pdf_handle_paths") or "", handle
        )
        if filename and normalize_filename_for_match(filename) not in matched_filenames:
            file_location = rebuild_url_for_environment(pdf_link, store_host)
            fev_el = sub(ensure_ev_el(), "electronicVersionFile")
            _fill_common(
                fev_el,
                version=default_version,
                licence=dc_rights_licence,
                access="open",
            )
            file_el = sub(
                fev_el, "file",
                attrib={"id": f"{handle}:{sequence}:{filename}"},
            )
            sub(file_el, "filename", filename)
            sub(file_el, "fileLocation", file_location)
            sub(file_el, "mimetype", guess_mimetype(filename))
            # No <filesize> — the DSpace CSV export doesn't carry one for
            # these bitstreams.
            sub(file_el, "source", store_host)
            sub(file_el, "externalRepositoryState", "STORED")


def build_existing_stores(
    parent: ET.Element,
    dspace_record: dict,
    store_host: str,
) -> None:
    """
    Build <existingStores>/<existingStore>, telling Pure that this record's
    content already lives in the DSpace store at `store_host`, identified by
    the DSpace Handle (matching the format used in DSpace_XMl_example.xml).
    """
    handle = (dspace_record.get("handle") or "").strip()
    if not handle:
        return
    es_el = sub(parent, "existingStores")
    e_el  = sub(es_el, "existingStore")
    sub(e_el, "storeName", store_host)
    sub(e_el, "updateRequired", "true")
    sub(e_el, "storeContentId", handle)


def _build_external_ids(
    parent: ET.Element,
    pure_record: dict,
    dspace_uuid: str,
    dspace_record: dict,
) -> None:
    """Real implementation, called with full dspace_record available."""
    ext_el = sub(parent, "externalIds")
    sub(ext_el, "id", dspace_uuid, attrib={"type": "DSpace"})

    # Additional identifiers from the DSpace record
    isbn = (dspace_record.get("dc.identifier.isbn") or "").strip()
    if isbn:
        sub(ext_el, "id", isbn, attrib={"type": "isbn"})
    issn = (dspace_record.get("dc.identifier.issn") or "").strip()
    if issn:
        sub(ext_el, "id", issn, attrib={"type": "issn"})


def build_urls(parent: ET.Element, dspace_record: dict, pure_record: dict) -> None:
    """Write <urls> from dc.identifier.uri (handle URLs) and any Pure links."""
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


def build_journal(
    parent: ET.Element,
    dspace_record: dict,
) -> None:
    """
    Write <journal> from DSpace CSV fields:
    journal_title, dc.identifier.journal, dc.identifier.issn.
    """
    journal_title = (dspace_record.get("journal_title") or "").strip()
    if not journal_title:
        journal_title = (dspace_record.get("dc.identifier.journal") or "").strip()
    issn = (dspace_record.get("dc.identifier.issn") or "").strip()

    if not journal_title and not issn:
        return

    j_el = sub(parent, "journal")
    if journal_title:
        sub(j_el, "title", journal_title)
    if issn:
        issns_el = sub(j_el, "printIssns")
        sub(issns_el, "issn", issn)


def build_publisher(parent: ET.Element, dspace_record: dict) -> None:
    publisher = (dspace_record.get("publisher_name") or
                 dspace_record.get("dc.publisher") or "").strip()
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


def build_funding_text(
    parent: ET.Element,
    dspace_record: dict,
    lang_uri: str,
) -> None:
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
) -> None:
    """
    Append one publication element to root, using pureId as the XML id.
    """
    pure_id   = str(pure_record.get("pureId", ""))
    lang_uri  = pure_record.get("language", {}).get("uri", "")

    xml_tag, sub_type = resolve_publication_type(pure_record, dspace_record)

    rec_el = ET.SubElement(
        root,
        xml_tag,
        attrib={"id": pure_id, "subType": sub_type},
    )

    # peerReviewed
    peer = pure_record.get("peerReview")
    if peer is None:
        peer_str = (dspace_record.get("dc.description.peer-reviewed") or "").strip().lower()
        peer = peer_str == "peer-reviewed"
    sub(rec_el, "peerReviewed", "true" if peer else "false")

    # publicationStatuses
    build_publication_statuses(rec_el, pure_record)

    # workflow
    workflow_step = pure_record.get("workflow", {}).get("step", "")
    workflow_val  = map_workflow(workflow_step)
    if workflow_val:
        sub(rec_el, "workflow", workflow_val)

    # language
    if lang_uri:
        lang, country = parse_pure_language(lang_uri)
        sub(rec_el, "language", f"{lang}_{country}")
    else:
        dc_lang = (dspace_record.get("dc.language.iso") or "").strip()
        if dc_lang:
            sub(rec_el, "language", dc_lang)

    # title
    build_title(rec_el, pure_record, lang_uri or "/dk/atira/pure/core/languages/en_IE")

    # subTitle (from DSpace subtitle)
    build_subtitle(rec_el, dspace_record, lang_uri or "/dk/atira/pure/core/languages/en_IE")

    # abstract
    build_abstract(rec_el, pure_record, lang_uri or "/dk/atira/pure/core/languages/en_IE")

    # persons / contributors
    build_persons(rec_el, pure_record)

    # organisations — placeholder pointing to the managing org from Pure
    orgs = pure_record.get("organizations", [])
    if orgs:
        orgs_el = sub(rec_el, "organisations")
        for org in orgs:
            org_uuid = org.get("uuid", "")
            if org_uuid:
                sub(orgs_el, "organisation", attrib={"id": org_uuid})

    # owner — managing organisation
    managing_org = pure_record.get("managingOrganization", {})
    owner_uuid   = managing_org.get("uuid", "")
    if owner_uuid:
        sub(rec_el, "owner", attrib={"id": owner_uuid})

    # electronicVersions (from Pure + DSpace file metadata)
    build_electronic_versions(rec_el, pure_record, dspace_record, lang_uri, store_host, default_version)

    # existingStores — flags the DSpace store/handle this record already
    # lives in, per DSpace_XMl_example.xml
    build_existing_stores(rec_el, dspace_record, store_host)

    # visibility
    vis_key = pure_record.get("visibility", {}).get("key", "FREE")
    sub(rec_el, "visibility", map_visibility(vis_key))

    # externalIds — DSpace UUID + supplementary identifiers
    dspace_uuid = (dspace_record.get("uuid") or "").strip()
    _build_external_ids(rec_el, pure_record, dspace_uuid, dspace_record)

    # funding text
    build_funding_text(rec_el, dspace_record, lang_uri or "/dk/atira/pure/core/languages/en_IE")

    # urls
    build_urls(rec_el, dspace_record, pure_record)

    # Type-specific fields
    _add_type_specific_fields(rec_el, xml_tag, pure_record, dspace_record, lang_uri)


def _add_type_specific_fields(
    rec_el: ET.Element,
    xml_tag: str,
    pure_record: dict,
    dspace_record: dict,
    lang_uri: str,
) -> None:
    """Append type-specific child elements based on the resolved XML tag."""
    if xml_tag == "contributionToJournal":
        build_journal(rec_el, dspace_record)

    elif xml_tag in ("book",):
        build_publisher(rec_el, dspace_record)
        build_isbns(rec_el, dspace_record)

    elif xml_tag == "chapterInBook":
        build_isbns(rec_el, dspace_record)
        # host publication title from DSpace (no dedicated field in JSON sample,
        # so derive from dc.title.alternative when present)
        alt_title = (dspace_record.get("dc.title.alternative") or "").strip()
        if alt_title:
            sub(rec_el, "hostPublicationTitle", alt_title)
        build_publisher(rec_el, dspace_record)

    elif xml_tag == "workingPaper":
        build_publisher(rec_el, dspace_record)

    elif xml_tag == "contributionToSpecialist":
        build_journal(rec_el, dspace_record)

    elif xml_tag == "thesis":
        quality = (dspace_record.get("dc.type") or "").strip().lower()
        # Map rough dc.type to qualification
        if "phd" in quality or "doctoral" in quality:
            sub(rec_el, "qualification", "phd")
        elif "master" in quality:
            sub(rec_el, "qualification", "mphil")
        else:
            sub(rec_el, "qualification", "phd")
        build_publisher(rec_el, dspace_record)

    # Publisher / ISSN always carried for journal-like types
    if xml_tag in ("contributionToJournal", "contributionToSpecialist"):
        issn = (dspace_record.get("dc.identifier.issn") or "").strip()
        # issn is already inside <journal>; skip duplicate


# ---------------------------------------------------------------------------
# XML serialisation
# ---------------------------------------------------------------------------
#
# Python's ElementTree reserves all "ns\d+" prefixes internally.
# We work around this by registering the commons namespace under a temporary
# prefix "cmn", serialising to a string, then doing a literal substitution to
# restore the "ns2:" prefix that the Pure import schema expects.

_CMN_TEMP_PREFIX = "cmn"    # temporary prefix used during ET serialisation


def register_namespaces() -> None:
    ET.register_namespace("",               PUB_NS)
    ET.register_namespace(_CMN_TEMP_PREFIX, CMN_NS)


def build_xml(
    matched_pairs: list[tuple[dict, dict]],
    store_host: str,
    default_version: str,
) -> ET.Element:
    """
    Build the complete <publications> root element containing all
    matched records.
    """
    register_namespaces()
    # ET adds xmlns automatically from the registered namespaces.
    # Do NOT add xmlns manually to attrib — that causes duplicate attributes.
    root = ET.Element(f"{{{PUB_NS}}}publications")
    for pure_rec, dspace_rec in matched_pairs:
        try:
            build_record_element(root, pure_rec, dspace_rec, store_host, default_version)
        except Exception as exc:
            pure_id = pure_rec.get("pureId", "?")
            print(f"  WARNING: skipped pureId={pure_id} — {exc}", file=sys.stderr)
    return root


def pretty_print(root: ET.Element) -> str:
    """
    Serialise the element tree to a nicely indented XML string with the
    correct 'ns2:' prefix on all commons-namespace nodes.
    """
    raw = ET.tostring(root, encoding="unicode", xml_declaration=False)

    # ET serialises the commons namespace as "cmn:tag xmlns:cmn=…"; we need
    # "ns2:tag xmlns:ns2=…" to match the Pure import schema.
    raw = raw.replace(f"xmlns:{_CMN_TEMP_PREFIX}=", "xmlns:ns2=")
    raw = raw.replace(f"{_CMN_TEMP_PREFIX}:", "ns2:")

    from xml.dom import minidom
    dom = minidom.parseString('<?xml version="1.0"?>\n' + raw)
    pretty = dom.toprettyxml(indent="    ", encoding=None)
    # Strip the declaration minidom added and replace with ours
    lines = pretty.split("\n")
    if lines[0].startswith("<?xml"):
        lines = lines[1:]
    header = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    return header + "\n" + "\n".join(lines)


# ---------------------------------------------------------------------------
# Filtering helpers (reused from get_file_ids.py)
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
# Duplicate resolution (adapted from get_file_ids.py)
# ---------------------------------------------------------------------------

def resolve_duplicates(
    pairs: list[tuple[dict, dict]],          # (pure_rec, dspace_rec)
) -> list[tuple[dict, dict]]:
    """
    If the same DSpace UUID / Handle maps to more than one Pure record,
    keep the one most recently modified. Returns the deduplicated list.
    """
    # Group by dspace uuid
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
            losers = [p for p in group if p is not winner]
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
                            f"test → {DSPACE_BASE_URLS['test']} , "
                            f"prod → {DSPACE_BASE_URLS['prod']}"
                        ))
    parser.add_argument("--default-version", default="publishersversion",
                        metavar="VERSION_TYPE",
                        help=(
                            "electronicVersionFile/version value to use for "
                            "files that exist only in DSpace (no Pure "
                            "versionType to draw from). Default: publishersversion, "
                            "matching DSpace_XMl_example.xml."
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
    output_path = args.output or f"./xml_import/pure_import_{today}.xml"

    store_base_url = DSPACE_BASE_URLS[args.environment]
    store_host = urlsplit(store_base_url).netloc
    print(f"Environment: {args.environment} → {store_base_url} (store host: {store_host})")

    # ── Load CSV ──────────────────────────────────────────────────────────────
    print(f"Loading DSpace CSV: {args.csv}")
    csv_by_handle, csv_by_uuid = load_csv_records(args.csv)
    print(f"  → {len(csv_by_uuid)} records by UUID, "
          f"{len(csv_by_handle)} by Handle loaded.")

    # ── Load JSON ─────────────────────────────────────────────────────────────
    print(f"Loading Pure JSON: {args.json}")
    with open(args.json, encoding="utf-8") as fh:
        json_records: list[dict] = json.load(fh)
    if not isinstance(json_records, list):
        json_records = [json_records]
    print(f"  → {len(json_records)} records loaded.")

    # ── Optional filters ──────────────────────────────────────────────────────
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
        print(f"  → {len(json_records)} records after filtering.")

    # ── Match ─────────────────────────────────────────────────────────────────
    print("Matching records…")
    matched: list[tuple[dict, dict]] = []
    unmatched = 0
    for pure_rec in json_records:
        dspace_rec = find_csv_record(pure_rec, csv_by_uuid, csv_by_handle)
        if dspace_rec is None:
            unmatched += 1
            continue
        matched.append((pure_rec, dspace_rec))

    print(f"  → {len(matched)} matched, {unmatched} unmatched Pure records.")

    # ── Duplicate resolution ──────────────────────────────────────────────────
    matched = resolve_duplicates(matched)
    print(f"  → {len(matched)} records after duplicate resolution.")

    if not matched:
        print("No records to export. Exiting.", file=sys.stderr)
        sys.exit(0)

    # ── Build XML ─────────────────────────────────────────────────────────────
    print("Building XML…")
    root = build_xml(matched, store_host, args.default_version)

    # ── Write output ──────────────────────────────────────────────────────────
    xml_str = pretty_print(root)
    with open(output_path, "w", encoding="utf-8") as fh:
        fh.write(xml_str)
    print(f"XML written to: {output_path}")
    print(f"  → {len(root)} publication element(s) exported.")


if __name__ == "__main__":
    main()