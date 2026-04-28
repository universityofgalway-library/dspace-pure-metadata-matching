# patch_records.py

A unified command-line tool for cleaning and patching **Pure research output** JSON records. Combines six patch modes into a single script; one or more modes can be combined in a single run.

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
python patch_records.py records.json ./patches \
    --patch-titles \
    --patch-author-keywords

# Run all applicable patches
python patch_records.py records.json ./patches \
    --patch-nulls \
    --patch-titles \
    --patch-external-orgs \
    --patch-author-keywords
```

---

## Usage

```
python patch_records.py <input> <output_dir> [OPTIONS]
```

### Positional arguments

| Argument | Default | Description |
|---|---|---|
| `input` | *(required)* | Path to the input JSON file — a JSON **array** of Pure research output records. |
| `output_dir` | `./patches` | Directory where all patch files will be written. Created if it does not exist. |

### Patch mode flags (at least one required)

| Flag | Description |
|---|---|
| `--patch-nulls` | Remove `null` items from lists. Produces a **delete + create** file pair. |
| `--patch-titles` | Strip the subtitle from the title when the title ends with the subtitle text. |
| `--patch-workflow` | Set `workflow.step = "validated"`. By default operates on standard research output records; use `--workflow-from-log` to switch to upload-log input format. |
| `--patch-external-orgs` | Clear `externalOrganizations` at the record level and within every contributor. |
| `--patch-author-keywords` | Remove the `/dk/atira/pure/authors` keyword group from `keywordGroups`. |
| `--patch-publishers` | Inject publisher UUIDs into eligible Pure records that have no publisher set, sourced from DSpace `dc.publisher`. Requires `--publisher-mapping` and `--dspace-csv`. |

### Options

| Flag | Default | Description |
|---|---|---|
| `--modified-after YYYY-MM-DD` | `1970-01-01` | Skip records with a `modifiedDate` on or before this date. Applies to **all modes except `--patch-nulls`** and `--patch-workflow --workflow-from-log`. |
| `--workflow-from-log` | `False` | `[--patch-workflow only]` Treat the input as a Pure upload-log file (records with `uuid`, `success`, and `type` fields) instead of standard research output records. Only entries where `success = true` and `type = "research-outputs"` are patched. The `--modified-after` date filter is **not** applied in this mode. |
| `--publisher-mapping PATH` | *(none)* | `[--patch-publishers only]` Path to the publisher mapping JSON file (array of objects with `name` and `uuid` keys). |
| `--dspace-csv PATH` | *(none)* | `[--patch-publishers only]` Path to the DSpace source CSV file. |

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
Expects a JSON log produced by a Pure upload operation. Each entry must have a `uuid`, a `success` boolean, and a `type` string. Only entries where **`success = true`** *and* **`type = "research-outputs"`** are included in the patch — failed records and non-research-output types are silently skipped. The `--modified-after` date filter is not applied in this mode.

Output file: `workflow_patch_YYYY-MM-DD.json`  
Patch shape: `{ "uuid": "…", "workflow": { "step": "validated" } }`

---

### `--patch-external-orgs`

Clears `externalOrganizations` to an empty list at two levels:

1. The record itself (`record.externalOrganizations`)
2. Each entry in `record.contributors[*].externalOrganizations`

Only records where at least one of the two levels is non-empty are included in the output. The `--modified-after` date filter applies.

Output file: `external_org_patch_YYYY-MM-DD.json`  
Patch shape:
```json
{
  "uuid": "…",
  "externalOrganizations": [],
  "contributors": [ { "…": "…", "externalOrganizations": [] } ]
}
```

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

All files are written to `<output_dir>/`. The date in each filename is the date the script is run.

---

## Examples

```bash
# 1. Clean null list items only
python patch_records.py data/records.json patches/ --patch-nulls

# 2. Fix overlapping titles
python patch_records.py data/records.json patches/ --patch-titles

# 3a. Advance all records to validated (standard research output input)
python patch_records.py data/records.json patches/ --patch-workflow --modified-after 2024-01-01

# 3b. Advance records to validated using a Pure upload log
python patch_records.py upload_log.json patches/ --patch-workflow --workflow-from-log

# 4. Clear external organisations, but only records modified after 2024-01-01
python patch_records.py data/records.json patches/ \
    --patch-external-orgs \
    --modified-after 2024-01-01

# 5. Remove author keyword groups
python patch_records.py data/records.json patches/ --patch-author-keywords

# 6. Inject publishers from DSpace into eligible Pure records
python patch_records.py data/records.json patches/ \
    --patch-publishers \
    --publisher-mapping data/publishers.json \
    --dspace-csv data/dspace_export.csv \
    --modified-after 2024-01-01

# 7. Run all standard patches in one pass
python patch_records.py data/records.json patches/ \
    --patch-nulls \
    --patch-titles \
    --patch-external-orgs \
    --patch-author-keywords \
    --patch-publishers \
    --publisher-mapping data/publishers.json \
    --dspace-csv data/dspace_export.csv \
    --modified-after 2023-06-01
```

---

## Notes

- All patch modes **read the input file once** and process it in a single pass — combining modes is efficient.
- Progress bars (via `tqdm`) are shown for each active mode.
- Records that require no changes for a given mode are silently skipped and counted in the summary.
- `--workflow-from-log` requires `--patch-workflow`; supplying it without `--patch-workflow` is an error.
- `--publisher-mapping` and `--dspace-csv` must be supplied together with `--patch-publishers`; using either without the other is an error.