"""
patch_records.py — Pure Research Output Batch Patcher
======================================================
Merge, clean, and patch Pure research output JSON records.

Patch modes (one or more may be combined):
  --patch-nulls          Remove null items from lists across the record
  --patch-titles         Strip subtitle from title when title ends with subtitle
  --patch-workflow       Set workflow step to "validated" for successful records
  --patch-external-orgs  Clear externalOrganizations at record and contributor level
  --patch-author-keywords  Remove the /dk/atira/pure/authors keyword group
  --patch-publishers     Inject publisher from DSpace dc.publisher into Pure records
                         that lack one. Requires --publisher-mapping and --dspace-csv.
  --patch-file-versions  Set a default versionType on file electronic versions
                         that don't have one assigned.
  --patch-urls            Clean links[]: keep one Handle link (desc "Repository
                           Handle"), drop DOIs/portal links, dedupe the rest.
  --patch-duplicate-files Remove duplicate FileElectronicVersion entries (same
                           normalized fileName + size), keeping the most
                           complete one.

See README.md for full usage examples.
"""

import os
import re
import csv
import html
import json
import argparse
import unicodedata
from collections import defaultdict
from datetime import date, datetime, timezone
from tqdm import tqdm


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TODAY = date.today().isoformat()

SYSTEM_FIELDS_TO_EXCLUDE = {
    "createdBy",
    "createdDate",
    "modifiedBy",
    "modifiedDate",
    "prettyUrlIdentifiers",
    "version",
    "pureId",
    "portalUrl",
}

PUNC = set("""—!–¿()-[]{};:'"''""‐\\,<>./?@#$%^&=+|£€*_~®™©0123456789""")

AUTHOR_KEYWORD_LOGICAL_NAME = "/dk/atira/pure/authors"

PUBLISHER_TYPES = {
    "BookAnthology",
    "ContributionToBookAnthology",
    "OtherContribution",
    "WorkingPaper",
    "NonTextual",
}

DEFAULT_VERSION_TYPE = {
    "uri": "/dk/atira/pure/researchoutput/electronicversion/versiontype/authorsversion",
    "term": {"en_IE": "Accepted author manuscript"},
}

# Creator values treated as system/import accounts rather than real Pure
# users (see _is_uploaded_by_real_user, used when weighing identical
# duplicate FileElectronicVersions in patch_duplicate_files).
SYSTEM_CREATORS = {"root", "atira", "sync_user", "admin", "system", ""}

# FileElectronicVersion fields counted towards "metadata completeness" when
# weighing identical duplicates (see _completeness_count). Deliberately
# excludes: licenseType/versionType (part of the duplicate-matching key,
# already guaranteed identical within a group) and pureId/creator/created/
# file.fileId/file.url/file.mimeType (system/internal fields, not
# descriptive metadata).
IDENTICAL_DUPLICATE_METADATA_FIELDS = ("accessType", "visibleOnPortalDate", "embargoPeriod", "title")


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def load_records(path: str) -> list:
    with open(path, "r", encoding="utf-8") as f:
        records = json.load(f)
    if not isinstance(records, list):
        raise ValueError("Expected a JSON array at the top level.")
    return records


def write_json(path: str, data) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def parse_modified_date(modified_date_str: str):
    """Parse ISO 8601 date string → date object, or None on failure."""
    if not modified_date_str:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return datetime.strptime(modified_date_str, fmt).date()
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(modified_date_str.replace("Z", "+00:00")).date()
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Patch: nulls
# ---------------------------------------------------------------------------

def _strip_system_fields(record: dict) -> dict:
    return {k: v for k, v in record.items() if k not in SYSTEM_FIELDS_TO_EXCLUDE}


def _strip_pure_id(obj):
    """Recursively remove pureId keys at any nesting level."""
    if isinstance(obj, dict):
        return {k: _strip_pure_id(v) for k, v in obj.items() if k != "pureId"}
    if isinstance(obj, list):
        return [_strip_pure_id(item) for item in obj]
    return obj


def _remove_null_list_items(obj):
    """Recursively remove None items from lists; leave None dict-values intact."""
    if isinstance(obj, dict):
        return {k: _remove_null_list_items(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_remove_null_list_items(item) for item in obj if item is not None]
    return obj


def _has_null_list_items(obj) -> bool:
    if isinstance(obj, dict):
        return any(_has_null_list_items(v) for v in obj.values())
    if isinstance(obj, list):
        if any(item is None for item in obj):
            return True
        return any(_has_null_list_items(item) for item in obj if item is not None)
    return False


def patch_nulls(records: list, output_dir: str) -> dict:
    """
    Produce two output files:
      null_patch_delete_YYYY-MM-DD.json  — metadata log of records to delete
      null_patch_create_YYYY-MM-DD.json  — cleaned records to re-create

    Returns summary counts.
    """
    delete_log = []
    create_patches = []
    skipped = 0
    patched = 0

    for record in tqdm(records, desc="[nulls] Processing", unit="rec"):
        if not isinstance(record, dict):
            skipped += 1
            continue
        if not _has_null_list_items(record):
            skipped += 1
            continue

        uuid = record.get("uuid", "")

        def _count_null_list_items(obj) -> dict:
            """Return {field_path: count} for every list position containing nulls."""
            results = {}
            if isinstance(obj, dict):
                for k, v in obj.items():
                    for subpath, count in _count_null_list_items(v).items():
                        full = f"{k}.{subpath}" if subpath else k
                        results[full] = results.get(full, 0) + count
            elif isinstance(obj, list):
                null_count = sum(1 for item in obj if item is None)
                if null_count:
                    results[""] = null_count
                for item in obj:
                    if isinstance(item, (dict, list)):
                        for subpath, count in _count_null_list_items(item).items():
                            if subpath:
                                results[subpath] = results.get(subpath, 0) + count
            return results

        null_locations = _count_null_list_items(record)
        for field_path, count in sorted(null_locations.items()):
            label = field_path if field_path else "(root list)"
            tqdm.write(f"  🧹 [{uuid}] {label}: {count} null(s) removed")

        delete_log.append({
            "data":                 "research-outputs",
            "uuid":                 record.get("uuid"),
            "title":                record.get("title", {}).get("value", "")
                                    if isinstance(record.get("title"), dict) else "",
            "type":                 record.get("type", {}).get("uri", "")
                                    if isinstance(record.get("type"), dict) else "",
            "createdBy":            record.get("createdBy"),
            "createdDate":          record.get("createdDate"),
            "modifiedBy":           record.get("modifiedBy"),
            "modifiedDate":         record.get("modifiedDate"),
            "portalUrl":            record.get("portalUrl"),
            "prettyUrlIdentifiers": record.get("prettyUrlIdentifiers"),
            "previousUuids":        record.get("previousUuids"),
        })

        cleaned = _strip_pure_id(_remove_null_list_items(_strip_system_fields(record)))
        cleaned.pop("uuid", None)
        create_patches.append(cleaned)
        patched += 1

    delete_path = os.path.join(output_dir, f"null_patch_delete_{TODAY}.json")
    create_path = os.path.join(output_dir, f"null_patch_create_{TODAY}.json")
    write_json(delete_path, delete_log)
    write_json(create_path, create_patches)

    return {
        "total":   len(records),
        "skipped": skipped,
        "patched": patched,
        "files":   [delete_path, create_path],
    }


# ---------------------------------------------------------------------------
# Patch: titles
# ---------------------------------------------------------------------------

def _strip_punc(s: str) -> str:
    return "".join(ch for ch in s.lower() if ch not in PUNC and not ch.isspace())


def _strip_subtitle_from_title(title: str, subtitle: str):
    """
    Return cleaned title string if the title ends with the subtitle
    (case- and punctuation-insensitive), else None.
    """
    if not title or not subtitle:
        return None

    title_clean = _strip_punc(title)
    sub_clean   = _strip_punc(subtitle)

    if not sub_clean or not title_clean.endswith(sub_clean):
        return None

    sub_rev = sub_clean[::-1]
    i = len(title) - 1
    for target_ch in sub_rev:
        while i >= 0:
            ch = title[i]
            i -= 1
            if ch.lower() not in PUNC and not ch.isspace():
                if ch.lower() == target_ch:
                    break
                else:
                    return None

    cut     = i + 1
    trimmed = title[:cut].rstrip()
    if trimmed.endswith(":"):
        trimmed = trimmed[:-1].rstrip()

    if trimmed == title or not trimmed:
        return None
    return trimmed


def patch_titles(
    records: list,
    output_dir: str,
    modified_after: date = date.fromisoformat("1970-01-01"),
) -> dict:
    patches = []
    skipped_no_both = 0
    skipped_date = 0
    checked = 0

    for record in tqdm(records, desc="[titles] Processing", unit="rec"):
        mod_date = parse_modified_date(record.get("modifiedDate", ""))
        if mod_date is None or mod_date <= modified_after:
            skipped_date += 1
            continue

        uuid         = record.get("uuid", "")
        title_obj    = record.get("title", {})
        subtitle_obj = record.get("subTitle", {})

        title    = title_obj.get("value", "").strip()    if isinstance(title_obj, dict)    else ""
        subtitle = subtitle_obj.get("value", "").strip() if isinstance(subtitle_obj, dict) else ""

        if not title or not subtitle:
            skipped_no_both += 1
            continue

        checked += 1
        cleaned = _strip_subtitle_from_title(title, subtitle)
        if cleaned is not None:
            patches.append({"uuid": uuid, "title": {"value": cleaned}})
            tqdm.write(f"  ✂️  [{uuid}]")
            tqdm.write(f"       Before  : {title}")
            tqdm.write(f"       After   : {cleaned}")
            tqdm.write(f"       Subtitle: {subtitle}")

    output_path = os.path.join(output_dir, f"title_patch_{TODAY}.json")
    write_json(output_path, patches)

    return {
        "total":               len(records),
        "skipped_date_filter": skipped_date,
        "skipped_no_both":     skipped_no_both,
        "checked":             checked,
        "patched":             len(patches),
        "files":               [output_path],
    }


# ---------------------------------------------------------------------------
# Patch: workflow step
# ---------------------------------------------------------------------------

def patch_workflow(
    records: list,
    output_dir: str,
    modified_after: date = date.fromisoformat("1970-01-01"),
    from_upload_log: bool = False,
) -> dict:
    """
    Set workflow.step = "validated" for every qualifying record.

    Default mode (from_upload_log=False):
      Input is standard Pure research output records.  Every record that passes
      the date filter AND whose current workflow step is not already "validated"
      is included in the patch.

    Upload-log mode (from_upload_log=True):
      Input is the JSON log produced by a Pure upload operation.  Each entry is
      expected to have 'uuid', 'success' (bool), and optionally 'type'.  Only
      entries where success=True and type="research-outputs" are patched.
    """
    result = []
    skipped_date = 0
    skipped_log = 0
    skipped_already_validated = 0

    for record in tqdm(records, desc="[workflow] Processing", unit="rec"):
        if not isinstance(record, dict):
            continue

        if from_upload_log:
            # Log format: filter by success flag and record type
            if record.get("type") != "research-outputs":
                skipped_log += 1
                continue
            if record.get("success") is not True:
                skipped_log += 1
                continue
            result.append({"uuid": record["uuid"], "workflow": {"step": "validated"}})
            tqdm.write(f"  ✅ [{record['uuid']}] → validated")
        else:
            # Standard research output records: apply date filter, then skip
            # anything already validated, patch the rest
            mod_date = parse_modified_date(record.get("modifiedDate", ""))
            if mod_date is None or mod_date <= modified_after:
                skipped_date += 1
                continue

            current_workflow = record.get("workflow") or {}
            if current_workflow.get("step") == "validated":
                skipped_already_validated += 1
                continue

            result.append({"uuid": record["uuid"], "workflow": {"step": "validated"}})

    output_path = os.path.join(output_dir, f"workflow_patch_{TODAY}.json")
    write_json(output_path, result)

    stats = {
        "total":   len(records),
        "patched": len(result),
        "files":   [output_path],
    }
    if from_upload_log:
        stats["skipped_not_research_outputs_or_failed"] = skipped_log
    else:
        stats["skipped_date_filter"] = skipped_date
        stats["skipped_already_validated"] = skipped_already_validated
    return stats


# ---------------------------------------------------------------------------
# Patch: external organisations
# ---------------------------------------------------------------------------

def _clean_contributor_ext_orgs(contributors: list):
    """
    Strip externalOrganizations from each contributor.
    Returns (cleaned_list, was_changed).
    """
    if not contributors:
        return contributors, False

    cleaned = []
    changed = False
    for contrib in contributors:
        if contrib.get("externalOrganizations"):
            changed = True
            new_contrib = {k: v for k, v in contrib.items() if k != "externalOrganizations"}
            new_contrib["externalOrganizations"] = []
            cleaned.append(new_contrib)
        else:
            cleaned.append(contrib)
    return cleaned, changed


def patch_external_orgs(
    records: list,
    output_dir: str,
    modified_after: date = date.fromisoformat("1970-01-01"),
) -> dict:
    patches = []
    skipped = 0
    skipped_date = 0
    record_cleared = 0
    contributor_cleared = 0

    for record in tqdm(records, desc="[ext-orgs] Processing", unit="rec"):
        uuid = record.get("uuid", "")

        mod_date = parse_modified_date(record.get("modifiedDate", ""))
        if mod_date is None or mod_date <= modified_after:
            skipped_date += 1
            continue

        patch = {"uuid": uuid, "externalOrganizations": []}
        changed = bool(record.get("externalOrganizations"))
        if changed:
            record_cleared += 1

        contributors = record.get("contributors", [])
        cleaned_contribs, contribs_changed = _clean_contributor_ext_orgs(contributors)
        patch["contributors"] = cleaned_contribs if contributors else []
        if contribs_changed:
            changed = True
            contributor_cleared += 1

        if changed:
            patches.append(patch)
            tqdm.write(
                f"  🧹 [{uuid}] — "
                f"record ext orgs: {'cleared' if record.get('externalOrganizations') else 'already empty'}, "
                f"contributors patched: {contribs_changed}"
            )
        else:
            skipped += 1

    output_path = os.path.join(output_dir, f"external_org_patch_{TODAY}.json")
    write_json(output_path, patches)

    return {
        "total":                len(records),
        "skipped_date_filter":  skipped_date,
        "skipped_no_change":    skipped,
        "patched":              len(patches),
        "record_ext_orgs_cleared":      record_cleared,
        "contributor_ext_orgs_cleared": contributor_cleared,
        "files":                [output_path],
    }


# ---------------------------------------------------------------------------
# Patch: author keywords
# ---------------------------------------------------------------------------

def patch_author_keywords(
    records: list,
    output_dir: str,
    modified_after: date = date.fromisoformat("1970-01-01"),
) -> dict:
    """
    Remove the /dk/atira/pure/authors keyword group from each record.
    Produces a standard PATCH-compatible JSON (uuid + keywordGroups only).
    """
    patches = []
    skipped = 0
    skipped_date = 0
    patched = 0

    for record in tqdm(records, desc="[author-kw] Processing", unit="rec"):
        mod_date = parse_modified_date(record.get("modifiedDate", ""))
        if mod_date is None or mod_date <= modified_after:
            skipped_date += 1
            continue

        uuid = record.get("uuid", "")
        existing_groups = record.get("keywordGroups", [])

        if not existing_groups:
            skipped += 1
            continue

        # Check whether the author keyword group is present
        has_author_group = any(
            kg.get("logicalName") == AUTHOR_KEYWORD_LOGICAL_NAME
            for kg in existing_groups
            if isinstance(kg, dict)
        )
        if not has_author_group:
            skipped += 1
            continue

        filtered = [
            kg for kg in existing_groups
            if isinstance(kg, dict) and kg.get("logicalName") != AUTHOR_KEYWORD_LOGICAL_NAME
        ]

        patches.append({
            "uuid":          uuid,
            "keywordGroups": filtered,   # empty list = remove all groups entirely
        })
        patched += 1
        tqdm.write(
            f"  🗑️  [{uuid}] — author keyword group removed "
            f"({'no remaining groups' if not filtered else f'{len(filtered)} group(s) kept'})"
        )

    output_path = os.path.join(output_dir, f"author_keyword_patch_{TODAY}.json")
    write_json(output_path, patches)

    return {
        "total":               len(records),
        "skipped_date_filter": skipped_date,
        "skipped_no_change":   skipped,
        "patched":             patched,
        "files":               [output_path],
    }


# ---------------------------------------------------------------------------
# Patch: file versions
# ---------------------------------------------------------------------------

def patch_file_versions(
    records: list,
    output_dir: str,
    modified_after: date = date.fromisoformat("1970-01-01"),
) -> dict:
    """
    For each FileElectronicVersion entry in electronicVersions that has no
    versionType assigned, set versionType to DEFAULT_VERSION_TYPE
    ("Accepted author manuscript"). Nothing else on the record is changed.
    Produces a standard PATCH-compatible JSON (uuid + full electronicVersions
    list, with the fix applied) for every record that had at least one file
    missing a version type.
    """
    patches = []
    skipped = 0
    skipped_date = 0
    patched = 0
    files_patched = 0

    for record in tqdm(records, desc="[file-versions] Processing", unit="rec"):
        mod_date = parse_modified_date(record.get("modifiedDate", ""))
        if mod_date is None or mod_date <= modified_after:
            skipped_date += 1
            continue

        uuid = record.get("uuid", "")
        electronic_versions = record.get("electronicVersions", [])

        if not electronic_versions:
            skipped += 1
            continue

        changed = False
        updated_versions = []
        for ev in electronic_versions:
            if (
                isinstance(ev, dict)
                and ev.get("typeDiscriminator") == "FileElectronicVersion"
                and not ev.get("versionType")
            ):
                ev = {**ev, "versionType": DEFAULT_VERSION_TYPE}
                changed = True
                files_patched += 1
                file_name = ev.get("file", {}).get("fileName", "") \
                    if isinstance(ev.get("file"), dict) else ""
                tqdm.write(
                    f"  📎 [{uuid}] — default versionType set on file "
                    f"'{file_name}'"
                )
            updated_versions.append(ev)

        if not changed:
            skipped += 1
            continue

        patches.append({
            "uuid":               uuid,
            "electronicVersions": updated_versions,
        })
        patched += 1

    output_path = os.path.join(output_dir, f"file_version_patch_{TODAY}.json")
    write_json(output_path, patches)

    return {
        "total":               len(records),
        "skipped_date_filter": skipped_date,
        "skipped_no_change":   skipped,
        "patched":             patched,
        "files_patched":       files_patched,
        "files":               [output_path],
    }


# ---------------------------------------------------------------------------
# Patch: duplicate file versions
# ---------------------------------------------------------------------------
#
# Ported from add_pdfs_to_pure.py's duplicate-resolution logic (same scoring
# and tie-breaking rules), adapted to operate on a plain electronicVersions
# list rather than a whole Pure record, so it can run as a standalone patch
# mode here.

def _clean_dspace_filename(filename: str) -> str:
    """
    Reverse HTML-entity encoding artifacts still literally present in a
    filename (e.g. "Me&#769;liacin.pdf" -> "Méliacin.pdf"): decode any HTML
    character references, then NFKC-normalize so a decoded combining mark
    merges into the preceding base letter and compatibility characters
    (e.g. the "fl" ligature) fold to plain letters. A no-op on filenames
    with no entity markup.
    """
    if not filename:
        return filename
    unescaped = html.unescape(filename)
    return unicodedata.normalize("NFKC", unescaped)


def _strip_diacritics(name: str) -> str:
    """Decompose accented characters and drop the combining marks, e.g. "é" -> "e"."""
    decomposed = unicodedata.normalize("NFKD", name)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


# A run of 2-6 digits bounded by underscores on both sides. This is the shape
# left behind when Pure's own storage-time filename normalization turns a
# literal, un-decoded HTML character reference like "&#769;" into "_769_"
# (the "&" and ";" each become "_", the digits survive as-is since they're
# word characters). Matched only within _is_clean_filename/_normalize_for_dedup,
# never used to alter a filename that's actually kept or displayed.
_ENTITY_DIGIT_RUN_RE = re.compile(r'_\d{2,6}_')


def _strip_entity_digit_runs(name: str) -> str:
    return _ENTITY_DIGIT_RUN_RE.sub("", name)


def _is_clean_filename(name: str) -> bool:
    """
    True if a filename shows no sign of the HTML-entity corruption: neither
    literal "&#NNN;" markup nor Pure's own "_NNN_" remnant of it. Used to
    prefer an un-corrupted duplicate over a corrupted one regardless of
    which copy happens to have more complete metadata.
    """
    if not name:
        return False
    unescaped = _clean_dspace_filename(name)
    if unescaped != name:
        return False
    if _strip_entity_digit_runs(unescaped) != unescaped:
        return False
    return True


def _pure_normalize_filename(name: str) -> str:
    """
    Normalize a filename the same way Pure does when storing uploaded files.
    Pure replaces characters that are not alphanumeric, hyphen, underscore,
    dot, or space with underscores. Used to group duplicates even when the
    exact fileName text differs slightly (e.g. punctuation).
    """
    return re.sub(r'[^\w.\- ]', '_', name)


def _normalize_for_dedup(name: str) -> str:
    """
    Full normalization used as the duplicate-grouping key: HTML-entity
    decoding, diacritic stripping, collapsing any "_NNN_" entity-digit
    remnant, Pure's own punctuation normalization, then lowercasing.

    This is deliberately aggressive so that a correctly-decoded filename
    (e.g. "Méliacin_(ARAN).pdf") and a corrupted copy of the very same file
    (e.g. "Me_769_liacin_(ARAN).pdf", which is what Pure's own storage-time
    normalization leaves behind once the literal "&#769;" markup is gone)
    reduce to the identical key and are therefore grouped as duplicates —
    which they are: the same underlying file, uploaded under two different
    spellings of its name. The "size" half of the grouping key (see
    _find_duplicate_file_groups) is the safety net against this being too
    aggressive: two genuinely different files are extremely unlikely to
    also share an exact byte size, even if their names coincidentally
    collapse to the same skeleton.
    """
    step = _clean_dspace_filename(name)
    step = _strip_diacritics(step)
    step = _strip_entity_digit_runs(step)
    step = _pure_normalize_filename(step)
    return step.lower()


def _version_type_uri(ev: dict) -> str | None:
    return (ev.get("versionType") or {}).get("uri")


def _license_uri(ev: dict) -> str | None:
    return (ev.get("licenseType") or {}).get("uri")


def _file_store_locations_key(file_block: dict) -> tuple:
    """Order-independent, hashable representation of file.fileStoreLocations."""
    locations = file_block.get("fileStoreLocations") or {}
    return tuple(sorted(locations.items()))


def _is_uploaded_by_real_user(ev: dict) -> bool:
    """
    Heuristic: entries created by a known system/import account (see
    SYSTEM_CREATORS) are treated as system/import uploads; any other
    creator is treated as a real Pure user.
    """
    creator = (ev.get("creator") or "").strip().lower()
    return creator not in SYSTEM_CREATORS


def _completeness_count(ev: dict) -> int:
    """
    Count how many of IDENTICAL_DUPLICATE_METADATA_FIELDS are meaningfully
    populated on a FileElectronicVersion. Higher = more complete metadata.
    """
    count = 0
    for field in IDENTICAL_DUPLICATE_METADATA_FIELDS:
        value = ev.get(field)
        if value is None:
            continue
        if isinstance(value, (dict, list, str)) and not value:
            continue
        count += 1
    return count


def _parse_ev_created(value: str):
    """
    Parse a FileElectronicVersion's 'created' timestamp (ISO 8601 with a
    UTC offset and variable-precision fractional seconds, e.g.
    "2026-09-14T17:22:08.79266+01:00") into a timezone-aware datetime, or
    None if it's missing/unparsable.
    """
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        pass
    # Fallback for Python versions where fromisoformat needs exactly 3 or 6
    # fractional-second digits: pad/truncate to exactly 6.
    match = re.match(r'^(.*?)(\.\d+)?([+-]\d{2}:\d{2}|Z)?$', value)
    if not match:
        return None
    base, frac, offset = match.groups()
    frac = "." + (frac[1:] + "000000")[:6] if frac else ""
    offset = "+00:00" if not offset or offset == "Z" else offset
    try:
        return datetime.fromisoformat(f"{base}{frac}{offset}")
    except ValueError:
        return None


def _normalize_scores(values: list) -> list:
    """Min-max normalize a list of numbers to [0, 1]. If every value is
    identical, that criterion has no discriminating power in this group,
    so everyone gets the same score (1.0) rather than an arbitrary 0."""
    lo, hi = min(values), max(values)
    if hi == lo:
        return [1.0] * len(values)
    return [(v - lo) / (hi - lo) for v in values]


def _choose_among_identical_duplicates(duplicates: list) -> dict:
    """
    Given 2+ FileElectronicVersion entries that are genuine, non-corrupted
    duplicates (identical fileName, size, versionType, licenseType, and
    fileStoreLocations — see _find_duplicate_file_groups), choose the
    single entry to keep.

    Three criteria are each min-max normalized to [0, 1] across the
    candidates in this specific group, then SUMMED with equal weight to
    produce a composite score; the highest-scoring entry wins:
      1. Uploaded by a real Pure user rather than a system/import account
         (_is_uploaded_by_real_user).
      2. Most complete metadata, counting only non-key, non-system fields
         (_completeness_count).
      3. Most recently created (ev.created, _parse_ev_created).
    A missing/unparsable 'created' value is treated as older than every
    parsed value in the group, so it never wins on recency.

    Ties (identical composite score) fall back to the earliest entry in
    the original electronicVersions order, for determinism.

    Note: the three criteria are equally weighted by construction (each
    normalized to the same [0, 1] range before summing) since no explicit
    relative weighting was specified. Adjust here if one criterion should
    matter more than the others.
    """
    real_user_vals = [1.0 if _is_uploaded_by_real_user(ev) else 0.0 for ev in duplicates]
    completeness_vals = [float(_completeness_count(ev)) for ev in duplicates]

    created_dts = [_parse_ev_created(ev.get("created", "")) for ev in duplicates]
    parsed = [dt for dt in created_dts if dt is not None]
    oldest_fallback = (min(parsed) if parsed else datetime.min.replace(tzinfo=timezone.utc)).timestamp() - 1
    created_vals = [dt.timestamp() if dt is not None else oldest_fallback for dt in created_dts]

    norm_real_user = _normalize_scores(real_user_vals)
    norm_completeness = _normalize_scores(completeness_vals)
    norm_created = _normalize_scores(created_vals)

    best_idx = None
    best_key = None
    for idx in range(len(duplicates)):
        score = norm_real_user[idx] + norm_completeness[idx] + norm_created[idx]
        key = (score, -idx)  # tie-break: earliest original entry wins
        if best_key is None or key > best_key:
            best_key = key
            best_idx = idx
    return duplicates[best_idx]


def _find_duplicate_file_groups(electronic_versions: list) -> dict:
    """
    Group FileElectronicVersions into candidate duplicate sets. Two entries
    are only considered duplicates of each other if ALL of the following
    match exactly:
      - fuzzy-normalized fileName (_normalize_for_dedup) — same file,
        allowing for the HTML-entity corruption vs. correctly-decoded
        spelling difference
      - file size
      - versionType.uri
      - licenseType.uri
      - file.fileStoreLocations (order-independent)

    Returns {key: [ev, ev, ...]} for groups with more than one entry.
    """
    groups = defaultdict(list)
    for ev in electronic_versions:
        if not isinstance(ev, dict) or ev.get("typeDiscriminator") != "FileElectronicVersion":
            continue
        file_block = ev.get("file") or {}
        file_name = file_block.get("fileName")
        if not file_name:
            continue
        key = (
            _normalize_for_dedup(file_name),
            file_block.get("size"),
            _version_type_uri(ev),
            _license_uri(ev),
            _file_store_locations_key(file_block),
        )
        groups[key].append(ev)

    return {key: evs for key, evs in groups.items() if len(evs) > 1}


def _resolve_duplicate_file_versions(electronic_versions: list) -> tuple[bool, list, list]:
    """
    Find duplicate FileElectronicVersion groups (see _find_duplicate_file_groups
    — size, versionType, licenseType, and fileStoreLocations must all match
    exactly) and resolve each group down to a single entry, handling two
    distinct cases differently:

    1. Group contains at least one corrupted-name entry (fileName still
       shows the HTML-entity artifact — _is_clean_filename is False):
       unchanged behaviour. Every corrupted-name entry is removed, but ONLY
       when the group also contains at least one clean-named entry to keep
       in its place. A group with no clean-named entry at all is left
       completely untouched. If there's more than one clean-named entry
       alongside the corrupted one(s), all of them are kept as-is — this
       case doesn't touch clean-vs-clean resolution.

    2. Group is entirely clean-named (no corruption artifact anywhere) but
       has more than one entry: these are genuine identical duplicates —
       same name, size, versionType, licenseType, and fileStoreLocations —
       with nothing left to distinguish them via the matching key. Exactly
       one is kept, chosen by _choose_among_identical_duplicates (weighted
       sum of: real-user creator, metadata completeness, recency).

    Returns (changed, deduped_versions, removed_evs):
      - changed: True if any duplicates were found and removed
      - deduped_versions: a new electronicVersions list with the resolved
        duplicates removed
      - removed_evs: the FileElectronicVersion dicts that were dropped
    """
    groups = _find_duplicate_file_groups(electronic_versions)
    if not groups:
        return False, electronic_versions, []

    to_remove_ids = set()
    removed = []
    for evs in groups.values():
        clean_evs = [ev for ev in evs if _is_clean_filename((ev.get("file") or {}).get("fileName", ""))]
        clean_ids = {id(ev) for ev in clean_evs}
        corrupted_evs = [ev for ev in evs if id(ev) not in clean_ids]

        if corrupted_evs:
            # Case 1: mixed or all-corrupted group — unchanged behaviour.
            if not clean_evs:
                continue  # no clean copy to keep — nothing actionable
            for ev in corrupted_evs:
                to_remove_ids.add(id(ev))
                removed.append(ev)
            continue

        # Case 2: every entry in this group is clean-named. If there's more
        # than one, they're genuine identical duplicates — pick one to keep.
        if len(clean_evs) > 1:
            keeper = _choose_among_identical_duplicates(clean_evs)
            for ev in clean_evs:
                if ev is not keeper:
                    to_remove_ids.add(id(ev))
                    removed.append(ev)

    if not removed:
        return False, electronic_versions, []

    deduped_versions = [ev for ev in electronic_versions if id(ev) not in to_remove_ids]
    return True, deduped_versions, removed


def patch_duplicate_files(
    records: list,
    output_dir: str,
    modified_after: date = date.fromisoformat("1970-01-01"),
) -> dict:
    """
    Remove duplicate FileElectronicVersion entries where an already-present
    copy of the same file exists — matched on fileName (fuzzy, allowing for
    HTML-entity corruption), size, versionType, licenseType, and
    fileStoreLocations all being otherwise identical (see
    _find_duplicate_file_groups). Handles two cases (see
    _resolve_duplicate_file_versions for the full rules):
      1. Corrupted-name duplicate(s) alongside a correctly-named copy —
         the corrupted ones are removed, the clean one(s) kept.
      2. Two or more genuinely identical clean-named duplicates with
         nothing left to tell them apart via the matching key — one is
         kept, chosen by a weighted sum of real-user creator, metadata
         completeness, and recency (_choose_among_identical_duplicates).
    Produces a PATCH-compatible JSON (uuid + full electronicVersions list
    with the resolved duplicates removed) for every record that had at
    least one actionable group.

    Typical cause: repeated failed/retried upload attempts on the same
    DSpace file (e.g. due to a filename-decoding bug that made earlier
    attempts fail, or simply re-running an upload job) create a new
    FileElectronicVersion each time instead of updating the existing one,
    leaving several duplicate copies on the record. Nothing else on the
    record is changed.
    """
    patches = []
    skipped_date = 0
    skipped_no_evs = 0
    skipped_no_duplicates = 0
    patched = 0
    files_removed = 0

    for record in tqdm(records, desc="[duplicate-files] Processing", unit="rec"):
        mod_date = parse_modified_date(record.get("modifiedDate", ""))
        if mod_date is None or mod_date <= modified_after:
            skipped_date += 1
            continue

        uuid = record.get("uuid", "")
        electronic_versions = record.get("electronicVersions", [])

        if not electronic_versions:
            skipped_no_evs += 1
            continue

        changed, deduped_versions, removed = _resolve_duplicate_file_versions(electronic_versions)
        if not changed:
            skipped_no_duplicates += 1
            continue

        for ev in removed:
            file_block = ev.get("file", {}) if isinstance(ev.get("file"), dict) else {}
            tqdm.write(
                f"  🗑️  [{uuid}] — removing duplicate file "
                f"'{file_block.get('fileName', '')}' (file pureId={file_block.get('pureId', '')})"
            )

        patches.append({
            "uuid":               uuid,
            "electronicVersions": deduped_versions,
        })
        patched += 1
        files_removed += len(removed)

    output_path = os.path.join(output_dir, f"duplicate_file_patch_{TODAY}.json")
    write_json(output_path, patches)

    return {
        "total":                 len(records),
        "skipped_date_filter":   skipped_date,
        "skipped_no_evs":        skipped_no_evs,
        "skipped_no_duplicates": skipped_no_duplicates,
        "patched":               patched,
        "files_removed":         files_removed,
        "files":                 [output_path],
    }


# ---------------------------------------------------------------------------
# Patch: publishers
# ---------------------------------------------------------------------------

def _normalize_for_comparison(s: str) -> str:
    """Lowercase, replace punctuation with spaces, collapse whitespace."""
    if not s:
        return ""
    result = "".join(" " if char in PUNC else char for char in s.lower())
    return " ".join(result.split())


def _load_dspace_rows(csv_path: str) -> list[dict]:
    with open(csv_path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _build_dspace_lookup(dspace_rows: list[dict]) -> dict:
    """
    Index DSpace rows by every available identifier so Pure records can be
    matched against them by DOI, repository DOI, handle, or DSpace UUID.
    Returns dict mapping normalised identifier string → dspace row.
    A single row may be reachable via multiple keys.
    """
    DOI_RE    = re.compile(r'^(?:https?://)?(?:doi\.org/|doi:)?(10\.\S+)$', re.IGNORECASE)
    HANDLE_RE = re.compile(r'^(?:https?://hdl\.handle\.net/)?(10379/\S+)$', re.IGNORECASE)

    def norm_doi(v):
        m = DOI_RE.match(v.strip().lower())
        return f"https://doi.org/{m.group(1)}" if m else None

    def norm_handle(v):
        m = HANDLE_RE.match(v.strip().lower())
        return f"http://hdl.handle.net/{m.group(1)}" if m else None

    index = {}

    for row in dspace_rows:
        # DSpace UUID
        ds_uuid = row.get("uuid", "").strip()
        if ds_uuid:
            index[ds_uuid.lower()] = row

        # Handle
        handle_raw = row.get("handle", "").strip()
        if handle_raw:
            nh = norm_handle(handle_raw)
            if nh:
                index[nh] = row

        # All URIs (semicolon-separated) — handles and DOIs
        uri_str = row.get("dc.identifier.uri", "")
        for u in [x.strip() for x in uri_str.split(";") if x.strip()]:
            nh = norm_handle(u)
            if nh:
                index[nh] = row
            nd = norm_doi(u)
            if nd:
                index[nd] = row

        # Publisher DOI
        pub_doi = row.get("dc.identifier.doi", "").strip()
        if pub_doi:
            nd = norm_doi(pub_doi)
            if nd:
                index[nd] = row

    return index


def _find_dspace_row(pure_record: dict, dspace_index: dict) -> dict | None:
    """
    Try to find the DSpace row that corresponds to this Pure record by
    checking DOIs, handles and DSpace UUID identifiers on the Pure record.
    """
    DOI_RE    = re.compile(r'^(?:https?://)?(?:doi\.org/|doi:)?(10\.\S+)$', re.IGNORECASE)
    HANDLE_RE = re.compile(r'^(?:https?://hdl\.handle\.net/)?(10379/\S+)$', re.IGNORECASE)

    def norm_doi(v):
        m = DOI_RE.match(v.strip().lower())
        return f"https://doi.org/{m.group(1)}" if m else None

    def norm_handle(v):
        m = HANDLE_RE.match(v.strip().lower())
        return f"http://hdl.handle.net/{m.group(1)}" if m else None

    candidates = []

    # Electronic versions → DOIs
    for ev in pure_record.get("electronicVersions", []):
        doi = ev.get("doi", "").strip()
        if doi:
            nd = norm_doi(doi)
            if nd:
                candidates.append(nd)
            nh = norm_handle(doi)
            if nh:
                candidates.append(nh)

    # Links → handles
    for link in pure_record.get("links", []):
        url = link.get("url", "").strip()
        if url:
            nh = norm_handle(url)
            if nh:
                candidates.append(nh)
            nd = norm_doi(url)
            if nd:
                candidates.append(nd)

    # Identifiers → DSpace UUID
    for ident in pure_record.get("identifiers", []):
        if ident.get("idSource") == "DSpace":
            val = ident.get("value", "").strip().lower()
            if val:
                candidates.append(val)

    for key in candidates:
        if key in dspace_index:
            return dspace_index[key]

    return None


def patch_publishers(
    records: list,
    publisher_mapping_path: str,
    dspace_csv_path: str,
    output_dir: str,
    modified_after: date = date.fromisoformat("1970-01-01"),
) -> dict:
    """
    For each Pure record whose typeDiscriminator is in PUBLISHER_TYPES and
    which has no publisher set, find the matching DSpace row (by DOI, handle,
    or DSpace UUID), look up dc.publisher in that row, resolve it against the
    publisher mapping, and emit a PATCH-compatible record (uuid + publisher).
    """
    # Load and index publisher mapping by normalised name
    with open(publisher_mapping_path, "r", encoding="utf-8") as f:
        publisher_mapping = json.load(f)
    pub_index = {}
    for pub in publisher_mapping:
        name = pub.get("name", "").strip()
        if name:
            key = _normalize_for_comparison(name)
            pub_index.setdefault(key, []).append(pub)

    # Load and index DSpace rows
    dspace_rows  = _load_dspace_rows(dspace_csv_path)
    dspace_index = _build_dspace_lookup(dspace_rows)

    patches = []
    skipped_date            = 0
    skipped_wrong_type      = 0
    skipped_has_publisher   = 0
    skipped_no_dspace_match = 0
    skipped_no_pub_name     = 0
    skipped_no_pub_match    = 0
    patched                 = 0

    for record in tqdm(records, desc="[publishers] Processing", unit="rec"):
        mod_date = parse_modified_date(record.get("modifiedDate", ""))
        if mod_date is None or mod_date <= modified_after:
            skipped_date += 1
            continue

        type_disc = record.get("typeDiscriminator", "")
        if type_disc not in PUBLISHER_TYPES:
            skipped_wrong_type += 1
            continue

        publisher = record.get("publisher")
        if isinstance(publisher, dict) and publisher.get("uuid"):
            skipped_has_publisher += 1
            continue

        uuid = record.get("uuid", "")

        dspace_row = _find_dspace_row(record, dspace_index)
        if dspace_row is None:
            skipped_no_dspace_match += 1
            tqdm.write(f"  ⚠️  [{uuid}] No matching DSpace row found")
            continue

        publisher_name = dspace_row.get("dc.publisher", "").strip()
        if not publisher_name:
            skipped_no_pub_name += 1
            tqdm.write(f"  ⚠️  [{uuid}] DSpace row has no dc.publisher")
            continue

        matches = pub_index.get(_normalize_for_comparison(publisher_name), [])
        if not matches:
            skipped_no_pub_match += 1
            tqdm.write(f"  ⚠️  [{uuid}] No publisher match for: '{publisher_name}'")
            continue

        matched_pub = matches[0]
        patches.append({
            "uuid": uuid,
            "publisher": {
                "uuid":       matched_pub["uuid"],
                "systemName": "Publisher",
            },
        })
        patched += 1
        tqdm.write(f"  ✅ [{uuid}] '{publisher_name}' → {matched_pub['uuid']}")

    output_path = os.path.join(output_dir, f"publisher_patch_{TODAY}.json")
    write_json(output_path, patches)

    return {
        "total":                     len(records),
        "skipped_date_filter":       skipped_date,
        "skipped_wrong_type":        skipped_wrong_type,
        "skipped_already_has_publisher": skipped_has_publisher,
        "skipped_no_dspace_match":   skipped_no_dspace_match,
        "skipped_no_publisher_name": skipped_no_pub_name,
        "skipped_no_publisher_match": skipped_no_pub_match,
        "patched":                   patched,
        "files":                     [output_path],
    }


# ---------------------------------------------------------------------------
# Patch: urls
# ---------------------------------------------------------------------------

def _is_doi_url(url: str) -> bool:
    """True if the value is a DOI (doi.org URL or bare 10.xxxx/... DOI)."""
    url = (url or "").strip()
    if not url:
        return False
    if re.search(r'doi\.org/', url, re.IGNORECASE):
        return True
    return bool(re.match(r'^10\.\d{4,9}/\S+$', url, re.IGNORECASE))


def _is_handle_url(url: str) -> bool:
    """True if the value is a hdl.handle.net Handle URL."""
    return bool(re.search(r'hdl\.handle\.net', (url or ""), re.IGNORECASE))


def _is_portal_url(link: dict, record: dict) -> bool:
    """True if the link is the Pure portal link for this record."""
    url = (link.get("url") or "").strip()
    portal_url = (record.get("portalUrl") or "").strip()
    if portal_url and url == portal_url:
        return True
    alias = (link.get("alias") or "").strip().lower()
    desc  = ((link.get("description") or {}).get("en_IE") or "").strip().lower()
    return "portal" in alias or "portal" in desc


def _clean_links(links: list, record: dict):
    """
    Clean a record's `links` list:
      - drop DOI links and Pure portal links
      - keep exactly one Handle link, with its description normalised to
        "Repository Handle"
      - de-duplicate the remaining (non-Handle) links by URL, preferring
        whichever copy has a description when duplicates are found
    Returns (cleaned_links, changed).
    """
    if not links:
        return links, False

    handle_link = None
    other_by_url = {}
    order = []

    for link in links:
        if not isinstance(link, dict):
            continue
        url = (link.get("url") or "").strip()
        if not url:
            continue
        if _is_doi_url(url):
            continue
        if _is_portal_url(link, record):
            continue

        if _is_handle_url(url):
            if handle_link is None:
                handle_link = link
            continue

        key = url.lower()
        existing = other_by_url.get(key)
        if existing is None:
            other_by_url[key] = link
            order.append(key)
        else:
            existing_has_desc = bool((existing.get("description") or {}).get("en_IE", "").strip())
            new_has_desc = bool((link.get("description") or {}).get("en_IE", "").strip())
            if new_has_desc and not existing_has_desc:
                other_by_url[key] = link

    cleaned = []
    if handle_link is not None:
        normalized_handle = dict(handle_link)
        normalized_handle["description"] = {"en_IE": "Repository Handle"}
        cleaned.append(normalized_handle)
    cleaned.extend(other_by_url[key] for key in order)

    return cleaned, cleaned != links


def patch_urls(
    records: list,
    output_dir: str,
    modified_after: date = date.fromisoformat("1970-01-01"),
) -> dict:
    """
    Clean up each record's `links` list: keep exactly one Handle link
    (description normalised to "Repository Handle"), drop all DOI links
    and Pure portal links, and de-duplicate what's left by URL (preferring
    the copy that has a description). Produces a PATCH-compatible JSON
    (uuid + cleaned links list) for every record whose links actually changed.
    """
    patches = []
    skipped = 0
    skipped_date = 0
    skipped_no_links = 0
    patched = 0

    for record in tqdm(records, desc="[urls] Processing", unit="rec"):
        mod_date = parse_modified_date(record.get("modifiedDate", ""))
        if mod_date is None or mod_date <= modified_after:
            skipped_date += 1
            continue

        uuid = record.get("uuid", "")
        links = record.get("links", [])

        if not links:
            skipped_no_links += 1
            continue

        cleaned_links, changed = _clean_links(links, record)
        if not changed:
            skipped += 1
            continue

        patches.append({"uuid": uuid, "links": cleaned_links})
        patched += 1
        tqdm.write(f"  🔗 [{uuid}] links: {len(links)} → {len(cleaned_links)}")

    output_path = os.path.join(output_dir, f"url_patch_{TODAY}.json")
    write_json(output_path, patches)

    return {
        "total":               len(records),
        "skipped_date_filter": skipped_date,
        "skipped_no_links":    skipped_no_links,
        "skipped_no_change":   skipped,
        "patched":             patched,
        "files":               [output_path],
    }

# ---------------------------------------------------------------------------
# Summary printer
# ---------------------------------------------------------------------------

def print_summary(mode: str, stats: dict) -> None:
    print(f"\n{'─' * 55}")
    print(f"  Summary: {mode}")
    print(f"{'─' * 55}")
    for key, val in stats.items():
        if key == "files":
            for f in val:
                print(f"   📄 Written  : {f}")
        else:
            label = key.replace("_", " ").capitalize()
            print(f"   {label:<38}: {val}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="patch_records.py",
        description=(
            "Pure Research Output Batch Patcher.\n"
            "One or more patch modes may be combined in a single run.\n"
            "See README.md for full documentation."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "input",
        help="Path to the input JSON file (list of Pure research output records).",
    )
    parser.add_argument(
        "--output_dir",
        help="Directory where patch files will be written.",
        default = "./patches"
    )

    modes = parser.add_argument_group("Patch modes (at least one required)")
    modes.add_argument(
        "--patch-nulls",
        action="store_true",
        help="Remove null items from lists. Outputs delete + create pair.",
    )
    modes.add_argument(
        "--patch-titles",
        action="store_true",
        help="Strip subtitle from title when title ends with subtitle.",
    )
    modes.add_argument(
        "--patch-workflow",
        action="store_true",
        help=(
            "Set workflow.step='validated' on all records passing the date filter. "
            "By default expects standard research output records. "
            "Use --workflow-from-log to switch to upload-log input format."
        ),
    )
    modes.add_argument(
        "--patch-external-orgs",
        action="store_true",
        help="Clear externalOrganizations at record and contributor level.",
    )
    modes.add_argument(
        "--patch-author-keywords",
        action="store_true",
        help="Remove the /dk/atira/pure/authors keyword group from records.",
    )

    modes.add_argument(
        "--patch-publishers",
        action="store_true",
        help=(
            "Inject publisher UUIDs into Pure records of eligible types that "
            "have no publisher set. Matches Pure records to DSpace rows by DOI, "
            "handle, or DSpace UUID, then resolves dc.publisher against the "
            "publisher mapping. Requires --publisher-mapping and --dspace-csv."
        ),
    )
    modes.add_argument(
        "--patch-file-versions",
        action="store_true",
        help=(
            "Set a default versionType ('Accepted author manuscript') on "
            "FileElectronicVersion entries that have no versionType assigned."
        ),
    )
    modes.add_argument(
        "--patch-urls",
        action="store_true",
        help=(
            "Clean links[]: keep one Handle link (description normalised "
            "to 'Repository Handle'), drop DOI links and Pure portal "
            "links, and de-duplicate remaining links by URL."
        ),
    )
    modes.add_argument(
        "--patch-duplicate-files",
        action="store_true",
        help=(
            "Remove duplicate FileElectronicVersion entries (same "
            "normalized fileName + size), keeping the most complete one. "
            "Useful after repeated failed upload attempts left several "
            "copies of the same file on a record."
        ),
    )

    opts = parser.add_argument_group("Options")
    opts.add_argument(
        "--workflow-from-log",
        action="store_true",
        dest="workflow_from_log",
        help=(
            "[--patch-workflow only] Treat the input as a Pure upload-log file "
            "(records with uuid, success, and type fields) instead of standard "
            "research output records. Only entries where success=true and "
            "type='research-outputs' will be patched. The date filter is not "
            "applied in this mode."
        ),
    )
    opts.add_argument(
        "--modified-after",
        default="1970-01-01",
        metavar="YYYY-MM-DD",
        help=(
            "Only process records modified strictly after this date "
            "(applies to all modes except --patch-nulls). "
            "Format: YYYY-MM-DD. Default: 1970-01-01 (all records)."
        ),
    )

    opts.add_argument(
        "--publisher-mapping",
        default=None,
        metavar="PATH",
        help=(
            "[--patch-publishers only] Path to the publisher mapping JSON file "
            "(array of objects with 'name' and 'uuid' keys)."
        ),
    )

    opts.add_argument(
        "--dspace-csv",
        default=None,
        metavar="PATH",
        help=(
            "[--patch-publishers only] Path to the DSpace source CSV file."
        ),
    )

    return parser


def main() -> None:
    parser = build_parser()
    args   = parser.parse_args()

    # --- Validate at least one mode ---
    if args.workflow_from_log and not args.patch_workflow:
        parser.error("--workflow-from-log requires --patch-workflow.")

    modes_selected = [
        args.patch_nulls,
        args.patch_titles,
        args.patch_workflow,
        args.patch_external_orgs,
        args.patch_author_keywords,
        args.patch_publishers,
        args.patch_file_versions,
        args.patch_urls,
        args.patch_duplicate_files,
    ]
    
    if not any(modes_selected):
        parser.error(
            "No patch mode selected. Choose at least one of: "
            "--patch-nulls, --patch-titles, --patch-workflow, "
            "--patch-external-orgs, --patch-author-keywords, --patch-publishers, "
            "--patch-file-versions, --patch-urls, --patch-duplicate-files"
        )

    if args.patch_publishers and not args.publisher_mapping:
        parser.error("--patch-publishers requires --publisher-mapping.")
    if args.patch_publishers and not args.dspace_csv:
        parser.error("--patch-publishers requires --dspace-csv.")
    if args.publisher_mapping and not args.patch_publishers:
        parser.error("--publisher-mapping requires --patch-publishers.")
    if args.dspace_csv and not args.patch_publishers:
        parser.error("--dspace-csv requires --patch-publishers.")
    if args.publisher_mapping and not os.path.isfile(args.publisher_mapping):
        print(f"❌ Publisher mapping file not found: {args.publisher_mapping}")
        return
    if args.dspace_csv and not os.path.isfile(args.dspace_csv):
        print(f"❌ DSpace CSV file not found: {args.dspace_csv}")
        return

    # --- Validate input ---
    if not os.path.isfile(args.input):
        print(f"❌ Input file not found: {args.input}")
        return

    # --- Validate --modified-after ---
    try:
        modified_after = date.fromisoformat(args.modified_after)
    except ValueError:
        print(f"❌ Invalid date for --modified-after: '{args.modified_after}'. Expected YYYY-MM-DD.")
        return

    # --- Ensure output dir ---
    try:
        ensure_dir(args.output_dir)
    except OSError as e:
        print(f"❌ Could not create output directory: {e}")
        return

    # --- Load records (once) ---
    print(f"⏳ Loading {args.input} …")
    try:
        records = load_records(args.input)
    except (json.JSONDecodeError, ValueError) as e:
        print(f"❌ Failed to load input: {e}")
        return
    print(f"✅ Loaded {len(records):,} records\n")

    # --- Run selected patches ---
    if args.patch_nulls:
        stats = patch_nulls(records, args.output_dir)
        print_summary("Null list items", stats)

    if args.patch_titles:
        stats = patch_titles(records, args.output_dir, modified_after)
        print_summary("Title / subtitle overlap", stats)

    if args.patch_workflow:
        stats = patch_workflow(records, args.output_dir, modified_after, from_upload_log=args.workflow_from_log)
        print_summary("Workflow step → validated", stats)

    if args.patch_external_orgs:
        stats = patch_external_orgs(records, args.output_dir, modified_after)
        print_summary("External organisations", stats)

    if args.patch_author_keywords:
        stats = patch_author_keywords(records, args.output_dir, modified_after)
        print_summary("Author keyword groups", stats)

    if args.patch_publishers:
        stats = patch_publishers(
            records, args.publisher_mapping, args.dspace_csv,
            args.output_dir, modified_after
        )
        print_summary("Publishers", stats)

    if args.patch_file_versions:
        stats = patch_file_versions(records, args.output_dir, modified_after)
        print_summary("File versions", stats)

    if args.patch_urls:
        stats = patch_urls(records, args.output_dir, modified_after)
        print_summary("URLs", stats)

    if args.patch_duplicate_files:
        stats = patch_duplicate_files(records, args.output_dir, modified_after)
        print_summary("Duplicate file versions", stats)

    print(f"\n✅ All done. Output directory: {args.output_dir}\n")


if __name__ == "__main__":
    main()