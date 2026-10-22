"""
patch_records.py — Pure Research Output Batch Patcher
======================================================
Merge, clean, and patch Pure research output JSON records.

Patch modes (one or more may be combined):
  --patch-nulls          Remove null items from lists across the record
  --patch-titles         Strip subtitle from title when title ends with subtitle
  --patch-workflow       Set workflow step to "validated" for successful records
  --patch-external-orgs  Clear externalOrganizations at record and contributor level;
                         the record's internal organizations are sent unchanged
                         alongside (Pure requires them)
  --patch-author-keywords  Remove the /dk/atira/pure/authors keyword group
  --patch-publishers     Inject publisher from DSpace dc.publisher into Pure records
                         that lack one. Requires --publisher-mapping and --dspace-csv.
  --patch-file-versions  Set a default versionType on file electronic versions
                         that don't have one assigned.
  --patch-urls            Clean links[]: remove only duplicate links (same URL),
                           keeping one copy; every other link is kept, including
                           every distinct Handle (desc "Repository Handle").
  --patch-duplicate-files Remove duplicate FileElectronicVersion entries (same
                           file name -- ignoring case, accents, punctuation and
                           "_NNN_" garbling -- and same known size). Keeps the
                           open-access copy, then the most complete metadata;
                           same rules as add_pdfs_to_pure.py.
  --patch-duplicate-dois  Remove duplicate DOI electronic versions (same DOI in any
                           form: case, URL/prefix, trailing full stop), keeping
                           their metadata (access, version, licence) on the
                           remaining one.
  --patch-subjects        Add DSpace dc.subject values as free keywords
                           (FreeKeywordsKeywordGroup) to Pure records, merged
                           with existing keywords without duplication.
                           Requires --dspace-csv.

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
from datetime import date, datetime
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

# SYSTEM_CREATORS, FILE_EV_METADATA_FIELDS and OPEN_ACCESS_URI (used by
# --patch-duplicate-files) are defined in the duplicate-file block below.


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def load_records(path: str) -> list:
    with open(path, "r", encoding="utf-8") as f:
        records = json.load(f)
    if not isinstance(records, list):
        raise ValueError("Expected a JSON array at the top level.")
    return records


# Output files actually written during this run (see write_json / print_summary).
_WRITTEN_FILES = set()


def write_json(path: str, data) -> bool:
    """
    Write data as JSON. If there is nothing to write (empty list/dict), no
    file is created. Returns True if the file was written.
    """
    if not data:
        return False
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    _WRITTEN_FILES.add(path)
    return True


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


def _finalise_update_patches(patches: list, records: list) -> list:
    """
    Final shape of an update patch: "uuid" first, then the patched fields
    with every pureId removed at any nesting level -- pureId is a system
    field and is not supplied in patches (the record is identified by uuid).
    `records` is accepted for a uniform call signature and is not used.
    """
    finalised = []
    for patch in patches:
        entry = {"uuid": patch.get("uuid")}
        entry.update(_strip_pure_id({k: v for k, v in patch.items() if k not in ("uuid", "pureId")}))
        finalised.append(entry)
    return finalised


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
    patches = _finalise_update_patches(patches, records)
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
      expected to have 'uuid', 'success' (bool), and 'data' (record type,
      e.g. "research-outputs"). Only entries where success=True and
      data="research-outputs" are patched.
    """
    result = []
    skipped_date = 0
    skipped_log = 0
    skipped_already_validated = 0

    for record in tqdm(records, desc="[workflow] Processing", unit="rec"):
        if not isinstance(record, dict):
            continue

        if from_upload_log:
            # Log format: filter by success flag and record type. The upload
            # log's constant discriminator for a research-output entry is
            # the "data" field (e.g. {"data": "research-outputs", ...} —
            # see the log entries this script itself writes, and real
            # created/deleted-records logs), NOT "type" — "type" on these
            # entries, when present, is the record's own research-output
            # type URI (e.g. ContributionToJournal), which will essentially
            # never equal the literal string "research-outputs". Checking
            # "type" here always fails to match, silently producing an
            # empty patch for every real upload-log file.
            if record.get("data") != "research-outputs":
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
    result = _finalise_update_patches(result, records)
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
        # Anything that isn't a contributor object (e.g. a null list item --
        # see --patch-nulls) is passed through unchanged rather than crashing.
        if not isinstance(contrib, dict):
            cleaned.append(contrib)
            continue
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
    """
    Clear externalOrganizations at record level and on every contributor
    (internal and external). Each patch also carries the record's existing
    internal "organizations", unchanged: Pure rejects an update that sends
    externalOrganizations without them ("Organisations is required").
    """
    patches = []
    skipped = 0
    skipped_date = 0
    record_cleared = 0
    contributor_cleared = 0
    no_internal_orgs_used_managing = 0
    skipped_no_organizations = 0

    for record in tqdm(records, desc="[ext-orgs] Processing", unit="rec"):
        uuid = record.get("uuid", "")

        mod_date = parse_modified_date(record.get("modifiedDate", ""))
        if mod_date is None or mod_date <= modified_after:
            skipped_date += 1
            continue

        # Pure keeps a record's internal and external organisations together:
        # sending "externalOrganizations" without "organizations" empties the
        # record's organisations, which Pure rejects ("Organisations is
        # required", value=[]). So the record's existing internal
        # organisations are always sent alongside, unchanged. "organizations"
        # can't be empty: if the record has none, its managing organisation is
        # used; with neither, the record can't be patched safely and is skipped.
        internal_orgs = [
            org for org in (record.get("organizations") or [])
            if isinstance(org, dict) and org.get("uuid")
        ]
        used_managing_org = False
        if not internal_orgs:
            managing_uuid = (record.get("managingOrganization") or {}).get("uuid")
            if managing_uuid:
                internal_orgs = [{"systemName": "Organization", "uuid": managing_uuid}]
                used_managing_org = True
            elif record.get("externalOrganizations") or _clean_contributor_ext_orgs(record.get("contributors", []))[1]:
                tqdm.write(f"  ⚠️ [{uuid}] no internal organisations and no managing organisation — skipped "
                           f"(Pure requires organisations)")
                skipped_no_organizations += 1
                continue

        patch = {"uuid": uuid, "organizations": internal_orgs, "externalOrganizations": []}
        changed = bool(record.get("externalOrganizations"))
        if changed:
            record_cleared += 1

        # Internal and external contributors alike lose their external
        # organisations. The contributors list is only sent when a contributor
        # actually changed, so contributors are never overwritten needlessly.
        contributors = record.get("contributors", [])
        cleaned_contribs, contribs_changed = _clean_contributor_ext_orgs(contributors)
        if contribs_changed:
            patch["contributors"] = cleaned_contribs
            changed = True
            contributor_cleared += 1

        if changed:
            patches.append(patch)
            if used_managing_org:
                no_internal_orgs_used_managing += 1
                tqdm.write(f"  ℹ️ [{uuid}] record has no internal organisations — sending its managing organisation as organizations")
            tqdm.write(
                f"  🧹 [{uuid}] — "
                f"record ext orgs: {'cleared' if record.get('externalOrganizations') else 'already empty'}, "
                f"contributors patched: {contribs_changed}"
            )
        else:
            skipped += 1

    output_path = os.path.join(output_dir, f"external_org_patch_{TODAY}.json")
    patches = _finalise_update_patches(patches, records)
    write_json(output_path, patches)

    return {
        "total":                len(records),
        "skipped_date_filter":  skipped_date,
        "skipped_no_change":    skipped,
        "patched":              len(patches),
        "record_ext_orgs_cleared":      record_cleared,
        "contributor_ext_orgs_cleared": contributor_cleared,
        "organizations_from_managing_org": no_internal_orgs_used_managing,
        "skipped_no_organizations":     skipped_no_organizations,
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
    patches = _finalise_update_patches(patches, records)
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
    patches = _finalise_update_patches(patches, records)
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
# Duplicate FileElectronicVersion detection and resolution
# ---------------------------------------------------------------------------
#
# This block is IDENTICAL in add_pdfs_to_pure.py and patch_records.py, so the
# upload script and the --patch-duplicate-files patch use one and the same
# rule for (a) whether two files are the same file and (b) which copy of a
# duplicate stays. Change it in both scripts together.
#
# Two FileElectronicVersions are duplicates when
#   - both sizes are known and identical, and
#   - their file names are the same file name (same_file_name), checked twice:
#       1. exact match after loose normalisation (file_name_key): HTML-entity
#          decoding, Unicode NFKC, accents removed, Pure's own punctuation
#          replacement, lower case. "Paper.PDF" = "paper.pdf",
#          "Résumé.pdf" = "Resume.pdf", "a,b.pdf" = "a_b.pdf".
#       2. as a garbled name (is_garbled_copy_of): Pure stores an un-decoded
#          "&#769;" as "_769_", so "Me_769_liacin.pdf" is a garbled copy of
#          "Méliacin.pdf". Every name containing a number between
#          underscores is checked both ways, so "Smith_2019_final.pdf" twice
#          is still an exact match.
# Other metadata (licence, version type, access type, embargo, storage
# location, creator) does not matter for deciding that two files are
# duplicates; it only decides which copy stays (choose_file_version_to_keep).

FILE_EV_METADATA_FIELDS = (
    "accessType", "licenseType", "versionType", "visibleOnPortalDate",
    "embargoPeriod", "title",
)

# Creator values treated as system/import accounts rather than real Pure users.
SYSTEM_CREATORS = {"root", "atira", "sync_user", "admin", "system", ""}

OPEN_ACCESS_URI = "/dk/atira/pure/core/openaccesspermission/open"

# A run of 2-6 digits between underscores: what Pure's storage-time filename
# normalisation leaves of a literal, un-decoded HTML character reference
# ("&#769;" -> "_769_"; "&" and ";" become "_", the digits survive).
ENTITY_DIGIT_RUN_RE = re.compile(r'_\d{2,6}_')


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


def is_open_access_ev(ev: dict) -> bool:
    return ((ev.get("accessType") or {}).get("uri") or "") == OPEN_ACCESS_URI


def metadata_completeness_score(ev: dict) -> int:
    """How many of FILE_EV_METADATA_FIELDS are meaningfully populated."""
    score = 0
    for field in FILE_EV_METADATA_FIELDS:
        value = ev.get(field)
        if value is None:
            continue
        if isinstance(value, (dict, list, str)) and not value:
            continue
        score += 1
    return score


def is_uploaded_by_real_user(ev: dict) -> bool:
    """Entries created by a known system/import account (SYSTEM_CREATORS) are
    system/import uploads; any other creator is a real Pure user."""
    creator = (ev.get("creator") or "").strip().lower()
    return creator not in SYSTEM_CREATORS


def _ev_created_sort_value(ev: dict) -> float:
    """Timestamp of ev['created'] (ISO 8601), or +inf if missing/unparsable."""
    value = ev.get("created") or ""
    if not value:
        return float("inf")
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        pass
    match = re.match(r'^(.*?)(\.\d+)?([+-]\d{2}:\d{2})?$', text)
    if not match:
        return float("inf")
    base, frac, offset = match.groups()
    frac = "." + (frac[1:] + "000000")[:6] if frac else ""
    try:
        parsed = datetime.fromisoformat(f"{base}{frac}{offset or '+00:00'}")
        return parsed.timestamp()
    except (ValueError, OverflowError, OSError):
        return float("inf")


def choose_file_version_to_keep(duplicates: list) -> dict:
    """
    Choose the single FileElectronicVersion to keep from a group of
    duplicates (see find_duplicate_file_groups). Priority, each step only
    breaking ties left by the previous one:
      1. Open access (accessType "open") over any other access type.
      2. Most complete metadata (metadata_completeness_score).
      3. A clean file name over a garbled one ("_NNN_" / "&#NNN;" remnants).
      4. Uploaded by a real Pure user rather than a system/import account.
      5. Oldest (earliest 'created'; missing dates count as newest).
      6. First in the record's electronicVersions order.
    """
    garbled_ids = set()
    for ev in duplicates:
        name = (ev.get("file") or {}).get("fileName", "")
        if is_garbled_file_name(name) or any(
            is_garbled_copy_of(name, (other.get("file") or {}).get("fileName", ""))
            for other in duplicates if other is not ev
        ):
            garbled_ids.add(id(ev))

    best_ev = None
    best_key = None
    for idx, ev in enumerate(duplicates):
        key = (
            1 if is_open_access_ev(ev) else 0,
            metadata_completeness_score(ev),
            0 if id(ev) in garbled_ids else 1,
            1 if is_uploaded_by_real_user(ev) else 0,
            -_ev_created_sort_value(ev),
            -idx,
        )
        if best_key is None or key > best_key:
            best_key = key
            best_ev = ev
    return best_ev


def find_duplicate_file_groups(electronic_versions: list) -> list:
    """
    Group FileElectronicVersions that are the same file: both sizes known and
    identical, and same_file_name. Grouping is transitive (A~B, B~C -> one
    group). Returns a list of groups (lists of ev dicts, in record order)
    with more than one entry. Entries without a file name or a known size
    are never grouped.
    """
    files = [
        ev for ev in (electronic_versions or [])
        if isinstance(ev, dict)
        and ev.get("typeDiscriminator") == "FileElectronicVersion"
        and (ev.get("file") or {}).get("fileName")
        and known_file_size(ev) is not None
    ]
    parent = list(range(len(files)))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(files)):
        name_i = files[i]["file"]["fileName"]
        size_i = known_file_size(files[i])
        for j in range(i + 1, len(files)):
            if known_file_size(files[j]) != size_i:
                continue
            if same_file_name(name_i, files[j]["file"]["fileName"]):
                parent[root(j)] = root(i)

    groups = defaultdict(list)
    for i, ev in enumerate(files):
        groups[root(i)].append(ev)
    return [evs for _, evs in sorted(groups.items()) if len(evs) > 1]


def resolve_file_duplicates(electronic_versions: list) -> tuple:
    """
    Resolve every duplicate group to its keeper (choose_file_version_to_keep).
    Does not mutate the input. Returns (changed, deduped_versions,
    removed_evs, keeper_of), where keeper_of maps id(ev) -> kept ev for every
    entry of every duplicate group (the keeper maps to itself).
    """
    electronic_versions = electronic_versions or []
    groups = find_duplicate_file_groups(electronic_versions)
    if not groups:
        return False, electronic_versions, [], {}

    to_remove_ids = set()
    removed = []
    keeper_of = {}
    for evs in groups:
        keeper = choose_file_version_to_keep(evs)
        for ev in evs:
            keeper_of[id(ev)] = keeper
            if ev is not keeper:
                to_remove_ids.add(id(ev))
                removed.append(ev)

    deduped_versions = [ev for ev in electronic_versions if id(ev) not in to_remove_ids]
    return True, deduped_versions, removed, keeper_of


def _resolve_duplicate_file_versions(electronic_versions: list) -> tuple[bool, list, list]:
    """Resolve the duplicate files of one electronicVersions list (see
    resolve_file_duplicates). Returns (changed, deduped_versions, removed_evs)."""
    changed, deduped_versions, removed, _keeper_of = resolve_file_duplicates(electronic_versions)
    return changed, deduped_versions, removed


def patch_duplicate_files(
    records: list,
    output_dir: str,
    modified_after: date = date.fromisoformat("1970-01-01"),
) -> dict:
    """
    Remove duplicate FileElectronicVersion entries. Uses the same rules as
    add_pdfs_to_pure.py (the duplicate-file block above is identical in both
    scripts):
      - Duplicates: both sizes known and identical, and the same file name
        (same_file_name) -- an exact match ignoring case, accents and
        punctuation, or a "_NNN_" garbled copy of an accented name. Names
        with a number between underscores are checked both ways. Licence,
        version type, access type, embargo, storage location and creator
        do not matter for this decision.
      - Which copy stays (choose_file_version_to_keep): open access first,
        then the most complete metadata, then a clean file name over a
        garbled one, then a real Pure user over a system account, then the
        oldest, then the first in the record.
    Files without a known size are never removed.
    Produces a PATCH-compatible JSON (uuid + full electronicVersions list
    with the duplicates removed) for every record that had at least one
    duplicate group.

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
    patches = _finalise_update_patches(patches, records)
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
    # utf-8-sig strips a leading BOM if present (DSpace exports include one);
    # without it the first header is read as '\ufeff"uuid"' and DSpace UUID
    # matching silently never succeeds. Plain UTF-8 files read identically.
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
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
    patches = _finalise_update_patches(patches, records)
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
    Clean a record's `links` list -- the ONLY links removed are duplicates:
      - links with the identical URL (surrounding whitespace ignored) are
        collapsed into ONE copy, preferring whichever copy has a description;
        the kept copy takes the position of the first occurrence;
      - every other link is kept as it is: links whose URLs differ in any way
        (including two different Handles, kept for manual review), DOI links,
        portal links, and entries without a URL;
      - every Handle link's description is normalised to "Repository Handle".
    A non-empty list therefore never becomes empty.
    Returns (cleaned_links, changed).
    """
    if not links:
        return links, False

    # Position in `cleaned` of the copy kept for each URL.
    position_by_url = {}
    cleaned = []

    for link in links:
        url = (link.get("url") or "").strip() if isinstance(link, dict) else ""
        if not url:
            cleaned.append(link)          # nothing to compare -- keep as it is
            continue
        if url not in position_by_url:
            position_by_url[url] = len(cleaned)
            cleaned.append(link)
            continue
        kept = cleaned[position_by_url[url]]
        kept_has_desc = bool(((kept.get("description") or {}).get("en_IE") or "").strip())
        new_has_desc = bool(((link.get("description") or {}).get("en_IE") or "").strip())
        if new_has_desc and not kept_has_desc:
            cleaned[position_by_url[url]] = link

    for i, link in enumerate(cleaned):
        if isinstance(link, dict) and _is_handle_url((link.get("url") or "").strip()):
            link = dict(link)
            link["description"] = {"en_IE": "Repository Handle"}
            cleaned[i] = link

    return cleaned, cleaned != links


def patch_urls(
    records: list,
    output_dir: str,
    modified_after: date = date.fromisoformat("1970-01-01"),
) -> dict:
    """
    Clean up each record's `links` list: remove only duplicate links (identical
    URL), keeping one copy (the one with a description, if any), and normalise
    every Handle link's description to "Repository Handle". Nothing else is
    removed -- different Handles, DOI links and portal links are all kept.
    Produces a PATCH-compatible JSON (uuid + cleaned links list) for every
    record whose links actually changed.
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

        if links and not cleaned_links:
            # Safety net: never send a patch that would delete every link.
            tqdm.write(f"  ⚠️ [{uuid}] cleaning would leave no links — record skipped")
            skipped += 1
            continue
        patches.append({"uuid": uuid, "links": cleaned_links})
        patched += 1
        tqdm.write(f"  🔗 [{uuid}] links: {len(links)} → {len(cleaned_links)}")

    output_path = os.path.join(output_dir, f"url_patch_{TODAY}.json")
    patches = _finalise_update_patches(patches, records)
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
# Patch: duplicate DOIs
# ---------------------------------------------------------------------------

# DOI recognition, identical to match_records.py: bare "10.xxx/..", doi.org or
# dx.doi.org URLs (http/https, and the "https:/" typo), and "doi:", "DOI:",
# "DOI " or ":" prefixes.
_DOI_REGEX = re.compile(r'^(?:https?:/{1,2})?(?:(?:dx\.)?doi\.org/|doi\s*:?\s*|:)?(10\.\S+)$', re.IGNORECASE)


def _doi_comparison_key(value: str) -> str:
    """
    Key used to decide whether two DOI electronic versions are duplicates.
    Same normalisation as match_records.normalize_doi: case-insensitive,
    any URL/prefix form of the same DOI gives the same key, and trailing full
    stops are ignored ("10.1/A", "https://doi.org/10.1/a" and
    "DOI: 10.1/a." are duplicates). A value that isn't recognisable as a
    DOI only matches an identical value (surrounding whitespace ignored).
    Used for comparison only -- the DOI text stored in Pure is not changed.
    """
    match = _DOI_REGEX.match(value.strip().lower())
    if not match:
        return value.strip()
    return f"https://doi.org/{match.group(1).rstrip('.')}"


# Fields never copied from a removed duplicate onto the kept one: the DOI
# itself (identical by definition), the type, and Pure's internal id.
_DOI_MERGE_EXCLUDED_FIELDS = {"doi", "typeDiscriminator", "pureId"}


def _is_filled(value) -> bool:
    return value not in (None, "", [], {})


def _merge_duplicate_doi_versions(duplicates: list):
    """
    Merge electronic versions that share the same DOI into one.

    The kept entry is the one with the most filled fields (ties -> first).
    Any field it lacks is then filled from the other copies, in their
    original order, so no metadata (access type, version, licence, ...) is
    lost. accessType and embargoPeriod are taken together from one copy, so
    an embargo period is never combined with another copy's access type.
    Where copies disagree on a field, the kept entry's value stays and the
    conflict is reported.

    Returns (merged_entry, conflicting_field_names).
    """
    def richness(ev):
        return sum(1 for k, v in ev.items() if k not in _DOI_MERGE_EXCLUDED_FIELDS and _is_filled(v))

    kept_index = max(range(len(duplicates)), key=lambda i: (richness(duplicates[i]), -i))
    merged = dict(duplicates[kept_index])
    others = [ev for i, ev in enumerate(duplicates) if i != kept_index]
    conflicts = []

    if not _is_filled(merged.get("accessType")):
        for ev in others:
            if _is_filled(ev.get("accessType")):
                merged["accessType"] = ev["accessType"]
                if _is_filled(ev.get("embargoPeriod")):
                    merged["embargoPeriod"] = ev["embargoPeriod"]
                else:
                    merged.pop("embargoPeriod", None)
                break

    for ev in others:
        for key, value in ev.items():
            if key in _DOI_MERGE_EXCLUDED_FIELDS or key in ("accessType", "embargoPeriod") or not _is_filled(value):
                continue
            if not _is_filled(merged.get(key)):
                merged[key] = value
            elif merged[key] != value and key not in conflicts:
                conflicts.append(key)
        for key in ("accessType", "embargoPeriod"):
            if _is_filled(ev.get(key)) and _is_filled(merged.get(key)) and ev[key] != merged[key] and key not in conflicts:
                conflicts.append(key)

    return merged, conflicts


def _dedupe_doi_electronic_versions(electronic_versions: list):
    """
    Collapse DoiElectronicVersion entries with the same DOI (compared with
    _doi_comparison_key, so different spellings of one DOI count as the same)
    into one, merged as described in _merge_duplicate_doi_versions. The merged
    entry takes the position of the first copy and keeps its own DOI text;
    every other electronic version is left untouched.

    Returns (new_list, removed_count, [(doi, conflicting_fields), ...]).
    """
    groups = defaultdict(list)
    for idx, ev in enumerate(electronic_versions):
        if (
            isinstance(ev, dict)
            and ev.get("typeDiscriminator") == "DoiElectronicVersion"
            and isinstance(ev.get("doi"), str)
            and ev["doi"].strip()
        ):
            groups[_doi_comparison_key(ev["doi"])].append(idx)

    duplicate_groups = {doi: idxs for doi, idxs in groups.items() if len(idxs) > 1}
    if not duplicate_groups:
        return electronic_versions, 0, []

    replacement = {}
    drop = set()
    conflict_report = []
    for doi, idxs in duplicate_groups.items():
        merged, conflicts = _merge_duplicate_doi_versions([electronic_versions[i] for i in idxs])
        replacement[idxs[0]] = merged
        drop.update(idxs[1:])
        if conflicts:
            conflict_report.append((doi, conflicts))

    new_list = [
        replacement.get(idx, ev)
        for idx, ev in enumerate(electronic_versions)
        if idx not in drop
    ]
    return new_list, len(drop), conflict_report


def patch_duplicate_dois(
    records: list,
    output_dir: str,
    modified_after: date = date.fromisoformat("1970-01-01"),
) -> dict:
    """
    Remove duplicate DOI electronic versions: DoiElectronicVersion entries
    with the same DOI, whatever form it is written in (case, doi.org /
    dx.doi.org / "doi:" prefix, trailing full stop -- see
    _doi_comparison_key). Only the DOI is compared. The
    metadata of the removed copies (access type, embargo, version, licence,
    ...) is kept on the remaining entry -- see _merge_duplicate_doi_versions.
    Produces a standard PATCH-compatible JSON (uuid + full electronicVersions
    list) for every record that had at least one duplicate.
    """
    patches = []
    skipped = 0
    skipped_date = 0
    dois_removed = 0
    records_with_conflicts = 0

    for record in tqdm(records, desc="[duplicate-dois] Processing", unit="rec"):
        mod_date = parse_modified_date(record.get("modifiedDate", ""))
        if mod_date is None or mod_date <= modified_after:
            skipped_date += 1
            continue

        uuid = record.get("uuid", "")
        electronic_versions = record.get("electronicVersions") or []

        new_versions, removed, conflicts = _dedupe_doi_electronic_versions(electronic_versions)
        if not removed:
            skipped += 1
            continue

        patches.append({"uuid": uuid, "electronicVersions": new_versions})
        dois_removed += removed
        tqdm.write(f"  🧹 [{uuid}] removed {removed} duplicate DOI electronic version(s)")
        if conflicts:
            records_with_conflicts += 1
            for doi, fields in conflicts:
                tqdm.write(f"     ⚠️ copies of {doi} disagree on {fields} — kept the most complete copy's values")

    output_path = os.path.join(output_dir, f"duplicate_doi_patch_{TODAY}.json")
    patches = _finalise_update_patches(patches, records)
    write_json(output_path, patches)

    return {
        "total":                   len(records),
        "skipped_date_filter":     skipped_date,
        "skipped_no_change":       skipped,
        "patched":                 len(patches),
        "duplicate_dois_removed":  dois_removed,
        "records_with_conflicts":  records_with_conflicts,
        "files":                   [output_path],
    }


# ---------------------------------------------------------------------------
# Patch: subjects (dc.subject -> free keywords)
# ---------------------------------------------------------------------------
#
# parse_subjects and merge_keywords are taken unchanged from match_records.py.
# build_free_keywords_group writes the keywords in the shape Pure itself uses
# for a user-entered free-keywords group: both the "keywords" list and the
# "keywordContainers" list (state ACCEPTED, origin USER_SUPPLIED), as seen on
# valid records exported from Pure. The earlier template with only "keywords"
# was not picked up by Pure.

FREE_KEYWORDS_TYPE_DISCRIMINATOR = "FreeKeywordsKeywordGroup"
FREE_KEYWORDS_LOGICAL_NAME       = "keywordContainers"


def parse_subjects(subject_str):
    """Parse dc.subject field: semicolon-separated keywords."""
    if not subject_str:
        return []
    return [s.strip() for s in subject_str.split(";") if s.strip()]


def merge_keywords(existing_keywords, new_keywords):
    """
    Merge two lists of keyword strings into one, alphabetically sorted list
    with no duplicates. Comparison for duplicates is case-insensitive, but
    the original capitalisation of the first occurrence encountered is kept
    (existing keywords take precedence over new ones with the same value).
    """
    seen = {}
    for kw in existing_keywords + new_keywords:
        key = kw.lower()
        if key not in seen:
            seen[key] = kw
    return sorted(seen.values(), key=lambda k: k.lower())


def build_free_keywords_group(keywords):
    """
    Build a FreeKeywordsKeywordGroup dict (dc.subject -> Pure keywordGroups),
    mirroring a valid Pure record: the same en_IE keyword list appears in
    "keywords" and in a single ACCEPTED / USER_SUPPLIED keyword container.
    """
    return {
        "typeDiscriminator": "FreeKeywordsKeywordGroup",
        "logicalName": "keywordContainers",
        "name": {
            "en_IE": "Keywords"
        },
        "keywords": [
            {
                "locale": "en_IE",
                "freeKeywords": list(keywords)
            }
        ],
        "keywordContainers": [
            {
                "state": "ACCEPTED",
                "origin": "USER_SUPPLIED",
                "freeKeywords": [
                    {
                        "locale": "en_IE",
                        "freeKeywords": list(keywords)
                    }
                ]
            }
        ]
    }


def _existing_free_keywords(group: dict) -> list:
    """
    Every keyword string in a free-keywords group, from both places Pure
    stores them: "keywords" (locale entries) and "keywordContainers"
    (containers -> locale entries). Duplicates are removed later by
    merge_keywords.
    """
    locale_entries = list(group.get("keywords") or [])
    for container in group.get("keywordContainers") or []:
        if isinstance(container, dict):
            locale_entries.extend(container.get("freeKeywords") or [])
    found = []
    for locale_entry in locale_entries:
        if not isinstance(locale_entry, dict):
            continue
        found.extend(
            kw for kw in (locale_entry.get("freeKeywords") or [])
            if isinstance(kw, str) and kw.strip()
        )
    return found


def _is_general_free_keywords_group(kg: dict) -> bool:
    """
    True only for the general free-keywords group (typeDiscriminator AND
    logicalName both required, as in match_records.py). Pure's own "authors"
    free-keywords group (logicalName /dk/atira/pure/authors) and any
    classification/discipline keyword groups do not match and are passed
    through untouched.
    """
    return (
        kg.get("typeDiscriminator") == FREE_KEYWORDS_TYPE_DISCRIMINATOR
        and kg.get("logicalName") == FREE_KEYWORDS_LOGICAL_NAME
    )


def patch_subjects(
    records: list,
    dspace_csv_path: str,
    output_dir: str,
    modified_after: date = date.fromisoformat("1970-01-01"),
) -> dict:
    """
    For each Pure record, find the matching DSpace row (by DOI, handle, or
    DSpace UUID — same matching as --patch-publishers), parse its dc.subject
    values and add them to the record's general free-keywords group.

    Existing keywords are never removed:
      - every keyword already in the general FreeKeywordsKeywordGroup
        (all locale entries, in both "keywords" and "keywordContainers") is
        kept and merged with the DSpace subjects;
      - all other keyword groups are passed through unchanged.
    Duplicates are removed case-insensitively; an existing keyword's
    capitalisation wins over a DSpace subject with the same value.

    Records where every DSpace subject is already present are skipped.
    Produces a standard PATCH-compatible JSON (uuid + full keywordGroups).
    """
    dspace_rows  = _load_dspace_rows(dspace_csv_path)
    dspace_index = _build_dspace_lookup(dspace_rows)

    patches = []
    skipped_date            = 0
    skipped_no_dspace_match = 0
    skipped_no_subjects     = 0
    skipped_no_change       = 0
    patched                 = 0
    keywords_added          = 0

    for record in tqdm(records, desc="[subjects] Processing", unit="rec"):
        mod_date = parse_modified_date(record.get("modifiedDate", ""))
        if mod_date is None or mod_date <= modified_after:
            skipped_date += 1
            continue

        uuid = record.get("uuid", "")

        dspace_row = _find_dspace_row(record, dspace_index)
        if dspace_row is None:
            skipped_no_dspace_match += 1
            tqdm.write(f"  ⚠️  [{uuid}] No matching DSpace row found")
            continue

        dspace_subjects = parse_subjects(dspace_row.get("dc.subject", ""))
        if not dspace_subjects:
            skipped_no_subjects += 1
            continue

        existing_groups = record.get("keywordGroups") or []

        # Split existing groups into the general free-keywords group(s) and
        # everything else, remembering where the free-keywords group sat so
        # the rebuilt group goes back into the same position.
        existing_free_keywords = []
        other_keyword_groups   = []
        free_group_position    = None
        for kg in existing_groups:
            if not isinstance(kg, dict):
                continue
            if _is_general_free_keywords_group(kg):
                if free_group_position is None:
                    free_group_position = len(other_keyword_groups)
                existing_free_keywords.extend(_existing_free_keywords(kg))
            else:
                other_keyword_groups.append(kg)

        existing_lower = {kw.lower() for kw in existing_free_keywords}
        new_subjects = merge_keywords([], [
            s for s in dspace_subjects if s.lower() not in existing_lower
        ])
        if not new_subjects:
            skipped_no_change += 1
            continue

        merged_keywords = merge_keywords(existing_free_keywords, dspace_subjects)
        free_group = build_free_keywords_group(merged_keywords)

        if free_group_position is None:
            updated_groups = other_keyword_groups + [free_group]
        else:
            updated_groups = (
                other_keyword_groups[:free_group_position]
                + [free_group]
                + other_keyword_groups[free_group_position:]
            )

        # pureId fields (from the record's other keyword groups) are left out
        # of the patch, at every nesting level.
        patches.append({
            "uuid":          uuid,
            "keywordGroups": _strip_pure_id(updated_groups),
        })
        patched += 1
        keywords_added += len(new_subjects)
        tqdm.write(
            f"  🏷️  [{uuid}] — added {len(new_subjects)} keyword(s), "
            f"{len(merged_keywords)} total after merge: {'; '.join(new_subjects)}"
        )

    output_path = os.path.join(output_dir, f"subjects_patch_{TODAY}.json")
    patches = _finalise_update_patches(patches, records)
    write_json(output_path, patches)

    return {
        "total":                   len(records),
        "skipped_date_filter":     skipped_date,
        "skipped_no_dspace_match": skipped_no_dspace_match,
        "skipped_no_subjects":     skipped_no_subjects,
        "skipped_no_change":       skipped_no_change,
        "patched":                 patched,
        "keywords_added":          keywords_added,
        "files":                   [output_path],
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
                if f in _WRITTEN_FILES:
                    print(f"   📄 Written  : {f}")
                else:
                    print(f"   📭 No records to patch — no file written: {os.path.basename(f)}")
                    if os.path.exists(f):
                        print(f"      ⚠️ A file with this name from an earlier run still exists: {f}")
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
        "--output-dir",
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
        help=(
            "Clear externalOrganizations at record and contributor level. The "
            "record's internal organizations are sent unchanged alongside (Pure "
            "rejects the update otherwise); if it has none, its managing "
            "organisation is used."
        ),
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
            "Clean links[]: remove only duplicate links (identical URL), "
            "keeping one copy, and normalise Handle descriptions to "
            "'Repository Handle'. Every other link is kept, including "
            "different Handles, DOI links and portal links."
        ),
    )
    modes.add_argument(
        "--patch-duplicate-files",
        action="store_true",
        help=(
            "Remove duplicate FileElectronicVersion entries (same file "
            "name ignoring case/accents/punctuation or a garbled '_NNN_' "
            "copy of it, and the same known size). The open-access copy "
            "stays, then the one with the most complete metadata. "
            "Useful after repeated failed upload attempts left several "
            "copies of the same file on a record."
        ),
    )
    modes.add_argument(
        "--patch-duplicate-dois",
        action="store_true",
        help=(
            "Remove duplicate DOI electronic versions -- the same DOI in any "
            "form (case, doi.org/dx.doi.org/'doi:' prefix, trailing full "
            "stop) -- keeping the metadata of removed copies (access type, "
            "embargo, version, licence) on the remaining one."
        ),
    )
    modes.add_argument(
        "--patch-subjects",
        action="store_true",
        help=(
            "Add DSpace dc.subject values as free keywords "
            "(FreeKeywordsKeywordGroup, logicalName 'keywordContainers') to "
            "Pure records, merged with existing free keywords without "
            "duplication. Other keyword groups are left untouched. Matches "
            "Pure records to DSpace rows by DOI, handle, or DSpace UUID. "
            "Requires --dspace-csv."
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
            "[--patch-publishers / --patch-subjects only] Path to the "
            "DSpace source CSV file."
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
        args.patch_subjects,
        args.patch_duplicate_dois,
    ]
    
    if not any(modes_selected):
        parser.error(
            "No patch mode selected. Choose at least one of: "
            "--patch-nulls, --patch-titles, --patch-workflow, "
            "--patch-external-orgs, --patch-author-keywords, --patch-publishers, "
            "--patch-file-versions, --patch-urls, --patch-duplicate-files, "
            "--patch-subjects, --patch-duplicate-dois"
        )

    if args.patch_publishers and not args.publisher_mapping:
        parser.error("--patch-publishers requires --publisher-mapping.")
    if args.patch_publishers and not args.dspace_csv:
        parser.error("--patch-publishers requires --dspace-csv.")
    if args.publisher_mapping and not args.patch_publishers:
        parser.error("--publisher-mapping requires --patch-publishers.")
    if args.patch_subjects and not args.dspace_csv:
        parser.error("--patch-subjects requires --dspace-csv.")
    if args.dspace_csv and not (args.patch_publishers or args.patch_subjects):
        parser.error("--dspace-csv requires --patch-publishers or --patch-subjects.")
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

    if args.patch_duplicate_dois:
        stats = patch_duplicate_dois(records, args.output_dir, modified_after)
        print_summary("Duplicate DOIs", stats)

    if args.patch_subjects:
        stats = patch_subjects(records, args.dspace_csv, args.output_dir, modified_after)
        print_summary("Subjects → free keywords", stats)

    print(f"\n✅ All done. Output directory: {args.output_dir}\n")


if __name__ == "__main__":
    main()