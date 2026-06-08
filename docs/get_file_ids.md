# get_file_ids.py

Matches records between a DSpace CSV export and a Pure JSON export by Handle URL. For each matched pair, it correlates DSpace PDF file paths against Pure `FileElectronicVersion` entries by filename and produces a joined CSV of identifiers from both systems.

---

## Requirements

- Python 3.10+
- No third-party dependencies (standard library only)

---

## Usage

```bash
python get_file_ids.py --csv <file> --json <file> [OPTIONS]
```

Both `--csv` and `--json` are required.

### Examples

```bash
# Basic match
python get_file_ids.py --csv dspace_export.csv --json pure_outputs.json

# Custom output path
python get_file_ids.py --csv dspace_export.csv --json pure_outputs.json \
  --output ./results/matched_2026-04-28.csv

# Filter by modifier and date
python get_file_ids.py --csv dspace_export.csv --json pure_outputs.json \
  --modified-by "john@example.com" \
  --modified-after "2025-01-01"

# Only include records where every DSpace PDF has a matched Pure file
python get_file_ids.py --csv dspace_export.csv --json pure_outputs.json \
  --pdf-filter with
```

---

## Arguments

| Argument | Required | Default | Description |
|---|---|---|---|
| `--csv` | Yes | — | Path to the DSpace CSV input file |
| `--json` | Yes | — | Path to the Pure JSON input file |
| `--output` | No | `matched_records.csv` | Path for the output CSV file |
| `--modified-by` | No | `None` | Only include Pure records whose `modifiedBy` field exactly matches this value |
| `--modified-after` | No | `None` | Only include Pure records modified strictly after this date. Format: `YYYY-MM-DD` |
| `--pdf-filter` | No | `all` | Filter output by PDF match state — see [PDF Filter](#pdf-filter) |

Both `--modified-by` and `--modified-after` can be combined. If neither is set, all Pure records are considered.

---

## Input Format

### DSpace CSV

A standard DSpace metadata export. Read with UTF-8 encoding and BOM stripping (`utf-8-sig`), so CSV exports from DSpace's own export tools and Excel are handled correctly.

Required columns:

| Column | Description |
|---|---|
| `handle` | Bare handle (e.g. `10379/6474`) or full Handle URL. Records without a handle value are ignored. |
| `uuid` | DSpace item UUID. Required for output — rows where this is empty are skipped. |

Optional columns used when present:

| Column | Description |
|---|---|
| `pdf_handle_paths` | Semicolon-separated handle-based paths to DSpace PDFs, e.g. `/10379/4728/1/file.pdf`. Used for PDF matching against Pure file records. |

### Pure JSON

A JSON array of research output records as produced by `get_pure_data.py`. Each record is matched against the DSpace CSV via its `links` array — specifically links with `"alias": "Handle"`.

Required fields:

| Field | Description |
|---|---|
| `links` | Array of link objects. Links with `alias == "Handle"` provide the Handle URL used for matching. |
| `uuid` | Pure record UUID. Required for output — rows where this is empty are skipped. |
| `pureId` | Numeric Pure identifier. Required for output — rows where this is empty are skipped. |

Optional fields:

| Field | Description |
|---|---|
| `title` | Used to populate the `title` output column. Read from `title.value`. |
| `electronicVersions` | Used to extract `FileElectronicVersion` file metadata (see [PDF Matching](#pdf-matching)). |
| `modifiedBy` | String. Used by the `--modified-by` filter. |
| `modifiedDate` | ISO-8601 datetime string. Used by the `--modified-after` filter. Records with a missing or unparseable `modifiedDate` are excluded when this filter is active. |

---

## Matching Logic

1. Each Pure record is checked for links with `alias == "Handle"`.
2. Each Handle URL is looked up in the DSpace records dictionary (keyed by full Handle URL). Bare handles in the DSpace CSV (e.g. `10379/6474`) are automatically prefixed with `http://hdl.handle.net/` before lookup.
3. On a match, identifiers and file information are collected from both records (see [PDF Matching](#pdf-matching)) and a row is produced.
4. Rows where any of the four core fields (`dspace_uuid`, `pure_uuid`, `pure_id`, `handle`) is empty are skipped and counted separately.
5. If a Pure record has multiple Handle links, the first one that matches a DSpace record is used and subsequent handles for that record are ignored.

---

## PDF Matching

When a DSpace row has a `pdf_handle_paths` value and the matched Pure record has `FileElectronicVersion` entries, the script correlates them by filename:

- The base filename is extracted from each DSpace path (URL-decoded, trailing slashes stripped).
- Both the DSpace filename and the Pure `fileName` are normalised using Pure's filename normalisation rules (non-alphanumeric characters other than hyphens, underscores, dots, and spaces are replaced with underscores).
- A DSpace path is considered matched if its normalised filename equals a normalised Pure `fileName`.

Three cases are handled:

| Situation | Behaviour |
|---|---|
| DSpace has PDF paths AND Pure has `FileElectronicVersion` entries | Per-file matching is performed; only correlated pairs are written to the output |
| DSpace has no PDF paths | Pure file fields are carried through as-is (may be empty) |
| DSpace has PDF paths but Pure has no `FileElectronicVersion` entries | All file output fields are empty |

When multiple PDFs are found, values in the file columns are semicolon-separated.

---

## PDF Filter

The `--pdf-filter` flag controls which matched records appear in the output based on how well DSpace PDF paths correlate with Pure file records:

| Value | Included records |
|---|---|
| `all` (default) | Every handle-matched record |
| `with` | Only records where every DSpace PDF path has a matching Pure file (records with no DSpace PDFs are excluded) |
| `without` | Only records with no `pdf_handle_paths` in the DSpace row |
| `partial` | Only records where at least one DSpace PDF matched a Pure file AND at least one did not |

---

## Output

A CSV file written to the path specified by `--output`:

```
dspace_uuid,pure_uuid,pure_id,title,dspace_file_id,pure_file_id,pure_file_pure_id,pure_file_name,handle
a1b2c3d4-...,e5f6a7b8-...,12345,My Paper Title,/10379/4728/1/paper.pdf,MDAxOTAxYjI5,98765,paper.pdf,http://hdl.handle.net/10379/4728
```

### Output columns

| Column | Description |
|---|---|
| `dspace_uuid` | UUID of the matched DSpace item |
| `pure_uuid` | UUID of the matched Pure record |
| `pure_id` | Numeric Pure identifier (`pureId`) |
| `title` | Pure record title (`title.value`) |
| `dspace_file_id` | Semicolon-separated handle-based paths for matched DSpace PDFs |
| `pure_file_id` | Semicolon-separated `fileId` values for matched Pure files |
| `pure_file_pure_id` | Semicolon-separated `pureId` values for matched Pure file objects |
| `pure_file_name` | Semicolon-separated `fileName` values for matched Pure files |
| `handle` | Full Handle URL used to make the match |

---

## Duplicate Detection

After matching, the script checks for rows where the same `handle` or `dspace_uuid` maps to more than one `pure_uuid`. Duplicate rows are included in the output — they are not removed — and a warning is printed to stdout listing each conflicting key and the Pure UUIDs involved. These should be investigated manually.

---

## Console Output

```
Loading CSV from: dspace_export.csv
  → 4312 records with Handles loaded.
Loading JSON from: pure_outputs.json
  → 8760 records loaded.
  → 312 records after filtering.
Matching records by Handle…
  → 287 complete matches found.
  → 4 matched records skipped due to missing core fields.
  → WARNING: 2 duplicate keys detected (same handle or dspace_uuid maps to multiple pure_uuids).
     • http://hdl.handle.net/10379/6474  →  [uuid-aaa, uuid-bbb]
  → No duplicates found.
Output written to: matched_records.csv
```
