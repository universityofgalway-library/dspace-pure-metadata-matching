# patch_records.py

A unified command-line tool for cleaning and patching **Pure research output** JSON records. Combines eleven patch modes into a single script; one or more modes can be combined in a single run.

---

## Requirements

```
pip install tqdm
```

Python ≥ 3.10.

---

## Quick start

```bash
# Patch titles and remove author keywords in one pass
python patch_records.py records.json --output_dir ./patches \
    --patch-titles \
    --patch-author-keywords

# Run all applicable patches
python patch_records.py records.json --output_dir ./patches \
    --patch-nulls \
    --patch-titles \
    --patch-external-orgs \
    --patch-author-keywords
```

---

## Usage

```
python patch_records.py <input> [--output_dir DIR] [OPTIONS]
```

### Positional arguments

| Argument | Default | Description |
|---|---|---|
| `input` | *(required)* | Path to the input JSON file — a JSON **array** of Pure research output records. |

### Patch mode flags (at least one required)

| Flag | Description |
|---|---|
| `--patch-nulls` | Remove `null` items from lists. Produces a **delete + create** file pair. |
| `--patch-titles` | Strip the subtitle from the title when the title ends with the subtitle text. |
| `--patch-workflow` | Set `workflow.step = "validated"`. By default operates on standard research output records; use `--workflow-from-log` to switch to upload-log input format. |
| `--patch-external-orgs` | Clear `externalOrganizations` at the record level and within every contributor, internal and external. |
| `--patch-author-keywords` | Remove the `/dk/atira/pure/authors` keyword group from `keywordGroups`. |
| `--patch-publishers` | Inject publisher UUIDs into eligible Pure records that have no publisher set, sourced from DSpace `dc.publisher`. Requires `--publisher-mapping` and `--dspace-csv`. |
| `--patch-file-versions` | Set a default `versionType` ("Accepted author manuscript") on `FileElectronicVersion` entries in `electronicVersions` that have no `versionType` assigned. |
| `--patch-urls` | Clean `links[]`: drop DOI links and Pure portal links, remove only **exact** duplicate links (identical URL), and normalise every Handle link's description to "Repository Handle". Different Handles are all kept for manual review. |
| `--patch-duplicate-files` | Remove duplicate `FileElectronicVersion` entries left behind by repeated upload attempts on the same file. |
| `--patch-duplicate-dois` | Remove duplicate DOI electronic versions — the same DOI in any written form — keeping the metadata (access type, embargo, version, licence) of the removed copies on the remaining one. |
| `--patch-subjects` | Add DSpace `dc.subject` values as free keywords (`FreeKeywordsKeywordGroup`) to Pure records, merged with existing free keywords without duplication. Other keyword groups are left untouched. Requires `--dspace-csv`. |

### Options

| Flag | Default | Description |
|---|---|---|
| `--output_dir DIR` | `./patches` | Directory where all patch files will be written. Created if it does not exist. |
| `--modified-after YYYY-MM-DD` | `1970-01-01` | Skip records with a `modifiedDate` on or before this date. Applies to **all modes except `--patch-nulls`** and `--patch-workflow --workflow-from-log`. |
| `--workflow-from-log` | `False` | `[--patch-workflow only]` Treat the input as a Pure upload-log file (records with `uuid`, `success`, and `data` fields) instead of standard research output records. Only entries where `success = true` and `data = "research-outputs"` are patched. The `--modified-after` date filter is **not** applied in this mode. |
| `--publisher-mapping PATH` | *(none)* | `[--patch-publishers only]` Path to the publisher mapping JSON file (array of objects with `name` and `uuid` keys). |
| `--dspace-csv PATH` | *(none)* | `[--patch-publishers / --patch-subjects only]` Path to the DSpace source CSV file. A leading UTF-8 BOM (as in DSpace exports) is handled automatically. |

---

## General behaviour (all patch modes)

- **No `pureId` in patches.** `pureId` is a Pure system field. Every patch identifies its record by `uuid` only, and every `pureId` copied from the input record (in contributors, electronic versions and their files, links, keyword groups, …) is removed at all nesting levels. Nested objects stay linked to their Pure entities through their UUID references (e.g. `person.uuid` / `externalPerson.uuid`, `fileId`).
- **`uuid` first.** Each patch entry starts with `uuid`, followed by the patched fields.
- **No empty files.** If a mode finds no records to patch, no file is created; the summary shows `📭 No records to patch — no file written: <name>`. If a file with that name from an earlier run the same day still exists, it is **not** deleted — the summary warns about it so it isn't mistaken for this run's output.
- **Input encoding.** The DSpace CSV (`--dspace-csv`) is read as UTF-8 with or without a BOM.

---

## Patch modes — detailed behaviour

### `--patch-nulls`

Null items inside lists (e.g. `"contributors": [null, {...}]`) are invalid and must be handled differently from other patches. Pure does not accept a simple PATCH for these records. This mode therefore produces **two** output files:

| File | Purpose |
|---|---|
| `null_patch_delete_YYYY-MM-DD.json` | Metadata log of records that must be **deleted** from Pure before re-upload. |
| `null_patch_create_YYYY-MM-DD.json` | Cleaned versions of those records (system fields stripped, nulls removed, `uuid` removed) ready for **re-creation**. |

The delete log contains one entry per affected record with the following fields: `data`, `uuid`, `title`, `type`, `createdBy`, `createdDate`, `modifiedBy`, `modifiedDate`, `portalUrl`, `prettyUrlIdentifiers`, `previousUuids`.

> **Note:** `null` values in dictionaries/objects are left intact — the Pure schema permits them. Only `null` items inside arrays are removed.

System fields stripped from re-creation records: `createdBy`, `createdDate`, `modifiedBy`, `modifiedDate`, `prettyUrlIdentifiers`, `version`, `pureId`, `portalUrl`. In addition, `pureId` is removed recursively at **all nesting levels** throughout the record, and `uuid` is removed at the top level.

The `--modified-after` date filter does **not** apply to this mode.

---

### `--patch-titles`

Detects records where the `title.value` field ends with `subTitle.value` (comparison is case-insensitive and punctuation-insensitive) and strips the duplicated portion, including any preceding colon.

**Example:**

| Field | Before | After |
|---|---|---|
| `title.value` | `"Exploring AI: A New Era"` | `"Exploring AI"` |
| `subTitle.value` | `"A New Era"` | *(unchanged)* |

Records with no `title` or no `subTitle` are skipped. The `--modified-after` date filter applies.

Output file: `title_patch_YYYY-MM-DD.json`  
Patch shape: `{ "uuid": "…", "title": { "value": "…" } }`

---

### `--patch-workflow`

Sets `workflow.step = "validated"` on qualifying records. Operates in two modes depending on whether `--workflow-from-log` is supplied.

**Default mode — standard research output records:**  
Every record that passes the `--modified-after` date filter is included in the patch. Use this when your input file is a standard Pure research output export.

**Uploader log mode (`--workflow-from-log`):**  
Expects a JSON log produced by a Pure upload operation. Each entry must have a `uuid`, a `success` boolean, and a `data` string. Only entries where **`success = true`** *and* **`data = "research-outputs"`** are included in the patch — failed records and non-research-output types are silently skipped. (`type`, when present on a log entry, is the record's own research-output type URI, e.g. a `ContributionToJournal` term — not a substitute for `data`.) The `--modified-after` date filter is not applied in this mode.

Output file: `workflow_patch_YYYY-MM-DD.json`  
Patch shape: `{ "uuid": "…", "workflow": { "step": "validated" } }`

---

### `--patch-external-orgs`

Clears `externalOrganizations` to an empty list at two levels:

1. The record itself (`record.externalOrganizations`)
2. Each entry in `record.contributors[*].externalOrganizations` — **internal and external contributors alike**

Internal organisations (`organizations`) are not touched, at either level. Only records where at least one of the two levels is non-empty are included in the output. The `--modified-after` date filter applies.

- `contributors` is only included in the patch when at least one contributor actually changed, so contributors are never resent needlessly.
- A `null` entry in `contributors` is passed through unchanged instead of stopping the run (use `--patch-nulls` to remove it).

Output file: `external_org_patch_YYYY-MM-DD.json`  
Patch shape:
```json
{
  "uuid": "…",
  "externalOrganizations": [],
  "contributors": [ { "…": "…", "externalOrganizations": [] } ]
}
```
(`contributors` present only when a contributor changed.)

---

### `--patch-author-keywords`

Finds records that contain a `keywordGroups` entry with `logicalName = "/dk/atira/pure/authors"` and removes it. All other keyword groups in the same record are preserved.

- If removing the author group leaves no other groups, `keywordGroups` is set to `[]`.
- Records with no `keywordGroups` at all, or with no author keyword group present, are skipped.
- The `--modified-after` date filter applies.

Output file: `author_keyword_patch_YYYY-MM-DD.json`  
Patch shape:
```json
{
  "uuid": "…",
  "keywordGroups": [ /* remaining groups, or [] */ ]
}
```

---

### `--patch-publishers`

For each Pure record whose `typeDiscriminator` is one of `BookAnthology`, `ContributionToBookAnthology`, `OtherContribution`, `WorkingPaper`, or `NonTextual`, and which has no `publisher` set, this mode:

1. Matches the Pure record to a DSpace row using any available identifier — checked against the record's `electronicVersions` (DOIs and handle-shaped DOIs), `links` (handles and DOIs), and `identifiers` (DSpace UUID with `idSource = "DSpace"`).
2. Reads `dc.publisher` from the matched DSpace row.
3. Looks up the publisher name in the publisher mapping JSON (normalised, punctuation-insensitive match).
4. Emits a patch record with the resolved publisher UUID.

Records are skipped if:
- They already have a `publisher.uuid` set.
- No matching DSpace row can be found.
- The matched DSpace row has no `dc.publisher` value.
- The publisher name cannot be resolved against the mapping.
- They do not pass the `--modified-after` date filter.

Output file: `publisher_patch_YYYY-MM-DD.json`  
Patch shape:
```json
{
  "uuid": "…",
  "publisher": {
    "uuid": "…",
    "systemName": "Publisher"
  }
}
```

### `--patch-file-versions`

For each `FileElectronicVersion` entry in a record's `electronicVersions` that has no `versionType` assigned, sets `versionType` to:

```json
{
  "uri": "/dk/atira/pure/researchoutput/electronicversion/versiontype/authorsversion",
  "term": { "en_IE": "Accepted author manuscript" }
}
```

Records with no `electronicVersions`, or where every file version already has a `versionType`, are skipped. The `--modified-after` date filter applies.

Output file: `file_version_patch_YYYY-MM-DD.json`
Patch shape: `{ "uuid": "…", "electronicVersions": [ /* full list, with the fix applied */ ] }`

---

### `--patch-urls`

Cleans up each record's `links` array:

1. Drops any DOI link (`doi.org` URL or bare `10.xxxx/…` DOI).
2. Drops the Pure portal link (matched against the record's `portalUrl`, or an `alias`/`description` containing "portal").
3. Removes only **exact** duplicates — links with the identical URL (surrounding whitespace ignored), keeping whichever copy has a `description` set. Links whose URLs differ in any way are all kept for manual review, including:
   - two **different** Handles,
   - the same Handle as `http` and `https`, or with different letter case.
4. Normalises every Handle link's (`hdl.handle.net` URL) `description` to `{"en_IE": "Repository Handle"}`.

Link order (first occurrence) is kept.

Records whose `links` list is empty, or whose cleaned result is identical to the original, are skipped. The `--modified-after` date filter applies.

Output file: `url_patch_YYYY-MM-DD.json`
Patch shape: `{ "uuid": "…", "links": [ /* cleaned links */ ] }`

---

### `--patch-duplicate-files`

Removes duplicate `FileElectronicVersion` entries from a record's `electronicVersions` — typically left behind by repeated/retried upload attempts on the same underlying file (e.g. a filename-decoding bug that made earlier attempts fail, or simply re-running an upload job).

Two entries are only considered duplicates of each other if **all** of the following match exactly: normalized filename, file size, `versionType.uri`, `licenseType.uri`, and `file.fileStoreLocations`. Filename normalization is fuzzy enough to bridge an HTML-entity-corrupted name and its correctly-decoded counterpart (e.g. `Me_769_liacin.pdf` and `Méliacin.pdf` are recognised as the same file) — file size acts as the safety net against two genuinely different files coincidentally colliding on name alone.

Each duplicate group is resolved differently depending on its contents:

1. **Corrupted name alongside a clean one** — each corrupted-named entry is removed, keeping the clean-named entry(ies), but only when a clean sibling in the same group has at least as many accented characters as the corrupted name has digit-runs (each artifact replaces exactly one accented character). This guards against a filename that merely contains an ordinary bare number (a year, an ID) being wrongly treated as a corrupted duplicate of an unrelated file. A group with no clean-named entry at all, or where a corrupted candidate fails this check, is left untouched.
2. **Two or more genuinely identical clean-named duplicates** — nothing left to distinguish them via the matching key, so one is kept based on a weighted score: uploaded by a real Pure user (not `root`/`atira`/`sync_user`/`admin`/`system`) > most complete metadata (`accessType`, `visibleOnPortalDate`, `embargoPeriod`, `title`) > most recently created. Ties fall back to the earliest entry in the original order.

Records with no `electronicVersions`, or where no group is actionable, are skipped. Nothing outside `electronicVersions` is changed. The `--modified-after` date filter applies.

Output file: `duplicate_file_patch_YYYY-MM-DD.json`
Patch shape: `{ "uuid": "…", "electronicVersions": [ /* full list, with duplicates removed */ ] }`

---

### `--patch-duplicate-dois`

Removes duplicate `DoiElectronicVersion` entries from a record's `electronicVersions`.

**What counts as a duplicate** — only the DOI is compared, in normalised form (the same normalisation as `match_records.py`):
- case-insensitive;
- any written form of the same DOI: bare `10.…`, `doi.org` / `dx.doi.org` URLs (`http`/`https`, including the `https:/` typo), `doi:` / `DOI:` / `DOI ` / `:` prefixes;
- a trailing full stop is ignored.

So `https://doi.org/10.1/A`, `10.1/a`, `DOI: 10.1/a.` and `http://dx.doi.org/10.1/a` are one DOI. A value that isn't recognisable as a DOI (e.g. `ARTN e167`) only matches an identical value.

**Which copy is kept, and its metadata:**
- the copy with the most filled fields is kept (ties → the first); it takes the position of the first copy and keeps its own DOI text as stored in Pure;
- any field it lacks is filled in from the removed copies, so no metadata (access type, version, licence, …) is lost;
- `accessType` and `embargoPeriod` are always taken together from one copy, so an embargo is never combined with a different copy's access status;
- where copies disagree on a value (e.g. open vs closed), the kept copy's value stays and a warning names the DOI and fields; the summary counts these as `Records with conflicts`.

Other electronic version types, `null` entries and entries without a DOI are left untouched. Records without duplicates are skipped. The `--modified-after` date filter applies.

Output file: `duplicate_doi_patch_YYYY-MM-DD.json`
Patch shape: `{ "uuid": "…", "electronicVersions": [ /* full list, with duplicates merged */ ] }`

---

### `--patch-subjects`

Adds the subjects from DSpace `dc.subject` to each matching Pure record as free keywords. Uses the same keyword logic and the same keyword-group shape as `match_records.py`.

For each record that passes the `--modified-after` date filter, this mode:

1. Matches the Pure record to a DSpace row the same way as `--patch-publishers` — via `electronicVersions` DOIs, `links` (handles and DOIs), and `identifiers` (DSpace UUID with `idSource = "DSpace"`).
2. Splits `dc.subject` on `;` and trims whitespace (e.g. `"diabetes ; adults ; children"` → `diabetes`, `adults`, `children`). Empty values are ignored.
3. Collects every keyword already in the record's **general free-keywords group** — the group with `typeDiscriminator = "FreeKeywordsKeywordGroup"` **and** `logicalName = "keywordContainers"` — from both places Pure stores them: the group's `keywords` list and its `keywordContainers`, across all locale entries.
4. Merges the existing keywords with the DSpace subjects:
   - Duplicates are detected **case-insensitively**; when an existing keyword and a subject differ only in case, the existing Pure spelling is kept.
   - Repeated subjects within `dc.subject` itself are also collapsed.
   - The merged list is sorted alphabetically (case-insensitive).
5. Writes the merged list back as a single free-keywords group, in the same position in `keywordGroups` as the original (or appended at the end if the record had none). The shape is the same as a user-entered group on a valid Pure record: the merged list appears both in `keywords` and in one `ACCEPTED` / `USER_SUPPLIED` keyword container. (A group with only `keywords` is not picked up by Pure.)

```json
{
  "typeDiscriminator": "FreeKeywordsKeywordGroup",
  "logicalName": "keywordContainers",
  "name": { "en_IE": "Keywords" },
  "keywords": [
    { "locale": "en_IE", "freeKeywords": [ /* merged keywords */ ] }
  ],
  "keywordContainers": [
    {
      "state": "ACCEPTED",
      "origin": "USER_SUPPLIED",
      "freeKeywords": [
        { "locale": "en_IE", "freeKeywords": [ /* same merged keywords */ ] }
      ]
    }
  ]
}
```

**Existing keywords are never removed.** All other keyword groups — e.g. `ClassificationsKeywordGroup` (ASJC subject areas, Sustainable Development Goals — including their own `keywordContainers` with `structuredKeyword` entries) and Pure's own authors group (`/dk/atira/pure/authors`) — are passed through unchanged, apart from the removal of `pureId`s that applies to every patch. Keywords from every locale entry and container of the free-keywords group are kept, but they are consolidated into the single `en_IE` entry of one `ACCEPTED` / `USER_SUPPLIED` container.

Records are skipped if:
- No matching DSpace row can be found.
- The matched DSpace row has no `dc.subject` value.
- Every DSpace subject is already present in the free-keywords group (no change). Re-running the patch on already-patched records therefore produces no output for them.
- They do not pass the `--modified-after` date filter.

The summary also reports `Keywords added` — the total number of new keywords across all patched records.

Output file: `subjects_patch_YYYY-MM-DD.json`
Patch shape: `{ "uuid": "…", "keywordGroups": [ /* full list, with the merged free-keywords group */ ] }`

> **Combining modes that write the same field:** each patch file is built from the original input, so two modes that send the same field overwrite each other when both are applied. This affects `--patch-author-keywords` and `--patch-subjects` (both `keywordGroups`), and `--patch-file-versions`, `--patch-duplicate-files` and `--patch-duplicate-dois` (all `electronicVersions`). Apply one, re-export the records, then run the next.
>
> **Combining with `--patch-author-keywords`:** both modes emit the full `keywordGroups` list, and each patch file is built from the original input. Applying the subjects patch after the author-keywords patch would restore the authors group. Run and apply `--patch-author-keywords` first, re-export the records, then run `--patch-subjects`.

---

## Output files summary

| Patch mode | Output file(s) |
|---|---|
| `--patch-nulls` | `null_patch_delete_YYYY-MM-DD.json` + `null_patch_create_YYYY-MM-DD.json` |
| `--patch-titles` | `title_patch_YYYY-MM-DD.json` |
| `--patch-workflow` | `workflow_patch_YYYY-MM-DD.json` |
| `--patch-external-orgs` | `external_org_patch_YYYY-MM-DD.json` |
| `--patch-author-keywords` | `author_keyword_patch_YYYY-MM-DD.json` |
| `--patch-publishers` | `publisher_patch_YYYY-MM-DD.json` |
| `--patch-file-versions` | `file_version_patch_YYYY-MM-DD.json` |
| `--patch-urls` | `url_patch_YYYY-MM-DD.json` |
| `--patch-duplicate-files` | `duplicate_file_patch_YYYY-MM-DD.json` |
| `--patch-duplicate-dois` | `duplicate_doi_patch_YYYY-MM-DD.json` |
| `--patch-subjects` | `subjects_patch_YYYY-MM-DD.json` |

All files are written to the `--output_dir` directory (default `./patches/`). The date in each filename is the date the script is run. A file is only created when its mode has at least one record to patch.

---

## Examples

```bash
# 1. Clean null list items only
python patch_records.py data/records.json --output_dir patches/ --patch-nulls

# 2. Fix overlapping titles
python patch_records.py data/records.json --output_dir patches/ --patch-titles

# 3a. Advance all records to validated (standard research output input)
python patch_records.py data/records.json --output_dir patches/ --patch-workflow --modified-after 2024-01-01

# 3b. Advance records to validated using a Pure upload log
python patch_records.py upload_log.json --output_dir patches/ --patch-workflow --workflow-from-log

# 4. Clear external organisations, but only records modified after 2024-01-01
python patch_records.py data/records.json --output_dir patches/ \
    --patch-external-orgs \
    --modified-after 2024-01-01

# 5. Remove author keyword groups
python patch_records.py data/records.json --output_dir patches/ --patch-author-keywords

# 6. Inject publishers from DSpace into eligible Pure records
python patch_records.py data/records.json --output_dir patches/ \
    --patch-publishers \
    --publisher-mapping data/publishers.json \
    --dspace-csv data/dspace_export.csv \
    --modified-after 2024-01-01

# 7. Run all standard patches in one pass
python patch_records.py data/records.json --output_dir patches/ \
    --patch-nulls \
    --patch-titles \
    --patch-external-orgs \
    --patch-author-keywords \
    --patch-publishers \
    --publisher-mapping data/publishers.json \
    --dspace-csv data/dspace_export.csv \
    --modified-after 2023-06-01

# 8. Clean up duplicate FileElectronicVersions left by repeated uploads
python patch_records.py data/records.json --output_dir patches/ \
    --patch-duplicate-files \
    --modified-after 2024-01-01

# 9. Add DSpace dc.subject values as free keywords
python patch_records.py data/records.json --output_dir patches/ \
    --patch-subjects \
    --dspace-csv data/dspace_export.csv

# 10. Publishers and subjects in one pass, sharing the same DSpace CSV
python patch_records.py data/records.json --output_dir patches/ \
    --patch-publishers \
    --patch-subjects \
    --publisher-mapping data/publishers.json \
    --dspace-csv data/dspace_export.csv

# 11. Merge duplicate DOI electronic versions and remove exact duplicate links
python patch_records.py data/records.json --output_dir patches/ \
    --patch-duplicate-dois \
    --patch-urls
```

---

## Notes

- All patch modes **read the input file once** and process it in a single pass — combining modes is efficient.
- Progress bars (via `tqdm`) are shown for each active mode.
- Records that require no changes for a given mode are silently skipped and counted in the summary. A mode with nothing to patch writes no file.
- Patches never contain `pureId` — see [General behaviour](#general-behaviour-all-patch-modes).
- `--workflow-from-log` requires `--patch-workflow`; supplying it without `--patch-workflow` is an error.
- `--patch-publishers` requires both `--publisher-mapping` and `--dspace-csv`; `--publisher-mapping` without `--patch-publishers` is an error.
- `--patch-subjects` requires `--dspace-csv`.
- `--dspace-csv` requires `--patch-publishers` or `--patch-subjects`; supplying it with neither is an error.