# patch_records.py

A unified command-line tool for cleaning and patching **Pure research output** JSON records. Combines five patch modes into a single script; one or more modes can be combined in a single run.

---

## Requirements

```
pip install tqdm
```

Python ≥ 3.9.

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

| Argument | Description |
|---|---|
| `input` | Path to the input JSON file — a JSON **array** of Pure research output records. |
| `output_dir` | Directory where all patch files will be written. Created if it does not exist. |

### Patch mode flags (at least one required)

| Flag | Description |
|---|---|
| `--patch-nulls` | Remove `null` items from lists. Produces a **delete + create** file pair. |
| `--patch-titles` | Strip the subtitle from the title when the title ends with the subtitle text. |
| `--patch-workflow` | Set `workflow.step = "validated"` for records where `success = true`. |
| `--patch-external-orgs` | Clear `externalOrganizations` at the record level and within every contributor. |
| `--patch-author-keywords` | Remove the `/dk/atira/pure/authors` keyword group from `keywordGroups`. |

### Options

| Flag | Default | Description |
|---|---|---|
| `--modified-after YYYY-MM-DD` | `1970-01-01` | Skip records with a `modifiedDate` on or before this date. Applies to **all modes except `--patch-nulls`**. |

---

## Patch modes — detailed behaviour

### `--patch-nulls`

Null items inside lists (e.g. `"contributors": [null, {...}]`) are invalid and must be handled differently from other patches. Pure does not accept a simple PATCH for these records. This mode therefore produces **two** output files:

| File | Purpose |
|---|---|
| `null_patch_delete_YYYY-MM-DD.json` | Metadata log of records that must be **deleted** from Pure before re-upload. |
| `null_patch_create_YYYY-MM-DD.json` | Cleaned versions of those records (system fields stripped, nulls removed, `uuid` removed) ready for **re-creation**. |

> **Note:** `null` values in dictionaries/objects are left intact — the Pure schema permits them. Only `null` items inside arrays are removed.

System fields stripped from re-creation records: `createdBy`, `createdDate`, `modifiedBy`, `modifiedDate`, `prettyUrlIdentifiers`, `version`, `pureId`, `portalUrl`.

---

### `--patch-titles`

Detects records where the `title.value` field ends with `subTitle.value` (comparison is case-insensitive and punctuation-insensitive) and strips the duplicated portion, including any preceding colon.

**Example:**

| Field | Before | After |
|---|---|---|
| `title.value` | `"Exploring AI: A New Era"` | `"Exploring AI"` |
| `subTitle.value` | `"A New Era"` | *(unchanged)* |

Output file: `title_patch_YYYY-MM-DD.json`  
Patch shape: `{ "uuid": "…", "title": { "value": "…" } }`

---

### `--patch-workflow`
 
Sets `workflow.step = "validated"` (=`"description": {"en_IE": "Export to repository"}`) on qualifying records. Operates in two modes depending on whether `--workflow-from-log` is supplied.
 
**Default mode — standard research output records:**  
Every record that passes the `--modified-after` date filter is included in the patch. Use this when your input file is a standard Pure research output export.
 
**Uploader log mode (`--workflow-from-log`):**  
Expects the JSON log produced by `pure_uploader.py`. Each entry must have a `uuid`, a `success` boolean, and a `type` string. Only entries where **`success = true`** *and* **`type = "research-outputs"`** are included in the patch — failed records and non-research-output types are silently skipped. The `--modified-after` date filter is not applied in this mode.
 
Output file: `workflow_patch_YYYY-MM-DD.json`  
Patch shape: `{ "uuid": "…", "workflow": { "step": "validated" } }`

---

### `--patch-external-orgs`

Clears `externalOrganizations` to an empty list at two levels:

1. The record itself (`record.externalOrganizations`)
2. Each entry in `record.contributors[*].externalOrganizations`

Records can optionally be filtered by `--modified-after YYYY-MM-DD` so that only recently-modified records are included. This flag applies to all modes except `--patch-nulls`.
Only records where at least one of the two levels is non-empty are included in the output.

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
- Records with no `keywordGroups` at all are skipped.

Output file: `author_keyword_patch_YYYY-MM-DD.json`  
Patch shape:
```json
{
  "uuid": "…",
  "keywordGroups": [ /* remaining groups, or [] */ ]
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

# 6. Run all standard patches in one pass
python patch_records.py data/records.json patches/ \
    --patch-nulls \
    --patch-titles \
    --patch-external-orgs \
    --patch-author-keywords \
    --modified-after 2023-06-01
```

---

## Notes

- All patch modes **read the input file once** and process it in a single pass — combining modes is efficient.
- Progress bars (via `tqdm`) are shown for each active mode.
- Records that require no changes for a given mode are silently skipped and counted in the summary.
