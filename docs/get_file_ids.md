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
# Basic match — all records, default output filename
python get_file_ids.py --csv dspace_export.csv --json pure_outputs.json

# Custom output path
python get_file_ids.py --csv dspace_export.csv --json pure_outputs.json \
  --output ./results/matched_records.csv

# Filter by modifier and date
python get_file_ids.py --csv dspace_export.csv --json pure_outputs.json \
  --modified-by "john@example.com" \
  --modified-after "2025-01-01"

# Only include records where every DSpace PDF matched a Pure file
python get_file_ids.py --csv dspace_export.csv --json pure_outputs.json \
  --pdf-filter full_match
```

---

## Arguments

| Argument | Required | Default | Description |
|---|---|---|---|
| `--csv` | Yes | — | Path to the DSpace CSV input file |
| `--json` | Yes | — | Path to the Pure JSON input file |
| `--output` | No | `matched_records.csv` | Base path for the output CSV. The `--pdf-filter` value and today's date are inserted automatically — see [Output Files](#output-files) |
| `--modified-by` | No | `None` | Only include Pure records whose `modifiedBy` field exactly matches this value |
| `--modified-after` | No | `None` | Only include Pure records modified strictly after this date. Format: `YYYY-MM-DD` |
| `--pdf-filter` | No | `all` | Filter output by PDF match type — see [PDF Filter](#pdf-filter) |

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
| `pdf_handle_paths` | Semicolon-separated paths to DSpace PDFs, e.g. `/10379/4728/1/file.pdf`. Used for PDF matching against Pure file records. |

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
| `electronicVersions` | Used to extract `FileElectronicVersion` file metadata — see [PDF Matching](#pdf-matching). |
| `modifiedBy` | String. Used by the `--modified-by` filter. |
| `modifiedDate` | ISO-8601 datetime string. Used by the `--modified-after` filter and by duplicate resolution. Records with a missing or unparseable `modifiedDate` are excluded when `--modified-after` is active, and treated as least-recently-modified during duplicate resolution. |

---

## Matching Logic

1. The DSpace CSV is indexed by two keys on load: full Handle URL and DSpace item UUID.
2. For each Pure record, a DSpace match is attempted in priority order:
   - **DSpace UUID first** — the Pure record's `identifiers` array is searched for an entry with `idSource == "DSpace"`. If found, the UUID is looked up in the DSpace UUID index.
   - **Handle fallback** — if no UUID match is found, each link in the Pure record's `links` array with `alias == "Handle"` is looked up in the DSpace Handle index. The first matching Handle is used.
3. If neither lookup finds a match, the Pure record is skipped.
4. On a match, identifiers and file information are collected from both records (see [PDF Matching](#pdf-matching)) and a row is produced. The `handle` value in the output row is always taken from the DSpace CSV record's own `handle` column, regardless of which key triggered the match.
5. Rows where any of the four core fields (`dspace_uuid`, `pure_uuid`, `pure_id`, `handle`) is empty are skipped and counted separately.
6. Each Pure record produces at most one output row. Once a match is found (by either key), no further lookups are attempted for that record.

---

## PDF Matching

All DSpace PDF paths and all Pure `FileElectronicVersion` entries are always written to the output row regardless of whether their filenames matched — no file information is silently dropped.

When both sides have files, the script attempts to correlate them by filename:

- The base filename is extracted from each DSpace path (URL-decoded, trailing slashes stripped).
- Both the DSpace filename and the Pure `fileName` are normalised using Pure's filename normalisation rules: characters that are not alphanumeric, hyphens, underscores, dots, or spaces are replaced with underscores.
- A DSpace path is considered matched if its normalised filename equals a normalised Pure `fileName`.

The result of this correlation determines the `file_match_type` column — see [PDF Filter](#pdf-filter).

When multiple files are present, values in the file columns are semicolon-separated.

---

## PDF Filter

The `--pdf-filter` flag controls which matched records appear in the output. The same label is written to the `file_match_type` column of every row.

| Value | `file_match_type` | Included records |
|---|---|---|
| `all` (default) | varies | Every handle-matched record |
| `full_match` | `full_match` | Both sides have files and every DSpace PDF path matched a Pure filename |
| `partial_match` | `partial_match` | Both sides have files, at least one DSpace PDF matched, and at least one did not |
| `file_name_mismatch` | `file_name_mismatch` | Both sides have files but no DSpace filename matched any Pure filename |
| `dspace_only_pdf` | `dspace_only_pdf` | DSpace has files but the Pure record has no `FileElectronicVersion` entries |
| `pure_only_pdf` | `pure_only_pdf` | Pure has `FileElectronicVersion` entries but DSpace has no `pdf_handle_paths` |
| `no_pdf` | `no_pdf` | Neither DSpace nor Pure have any files |

---

## Output Files

Two output files are produced, both named automatically from the `--output` base path using the `--pdf-filter` value as a prefix and today's date as a suffix:

| File | Naming pattern | Contents |
|---|---|---|
| Main output | `<filter>_<name>_<YYYY-MM-DD>.csv` | One row per matched record, after duplicate resolution |
| Duplicates | `<filter>_duplicate_<name>_<YYYY-MM-DD>.csv` | All rows involved in duplicate collisions (only written if duplicates exist) |

For example, with `--output matched_records.csv` and `--pdf-filter all` run on 2026-06-09:

```
all_matched_records_2026-06-09.csv
all_duplicate_matched_records_2026-06-09.csv
```

With a directory prefix such as `--output results/matched_records.csv`, the filter prefix and date suffix are applied to the filename only:

```
results/all_matched_records_2026-06-09.csv
results/all_duplicate_matched_records_2026-06-09.csv
```

### Main output columns

| Column | Description |
|---|---|
| `dspace_uuid` | UUID of the matched DSpace item |
| `pure_uuid` | UUID of the matched Pure record |
| `pure_id` | Numeric Pure identifier (`pureId`) |
| `title` | Pure record title (`title.value`) |
| `dspace_file_id` | Semicolon-separated DSpace PDF paths from `pdf_handle_paths` |
| `pure_file_id` | Semicolon-separated `fileId` values for all Pure `FileElectronicVersion` entries |
| `pure_file_pure_id` | Semicolon-separated `pureId` values for all Pure file objects |
| `pure_file_name` | Semicolon-separated `fileName` values for all Pure files |
| `file_match_type` | PDF match classification for this record — see [PDF Filter](#pdf-filter) |
| `handle` | Full Handle URL used to make the match |

### Duplicates output columns

Identical to the main output, with one additional leading column:

| Column | Description |
|---|---|
| `duplicate_key` | The handle or `dspace_uuid` that triggered the collision group |

---

## Duplicate Resolution

After matching, the script detects records where the same `handle` or `dspace_uuid` maps to more than one `pure_uuid`. Rather than emitting all candidates, it selects the best one per collision group and excludes the rest from the main output.

**Selection criteria (descending priority):**

1. Most DSpace filenames matched to a Pure filename.
2. Most Pure `FileElectronicVersion` entries in total.
3. Most recently modified (`modifiedDate` from the Pure record). Records missing a `modifiedDate` rank lowest.

All candidates (winner and losers) are written to the duplicates output file for review. A warning is printed to stdout for each collision group, showing which Pure record was kept, its score, and which were dropped.

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
     • http://hdl.handle.net/10379/6474
       kept:    uuid-aaa  (score (2, 2, datetime.datetime(...)))
       dropped: [uuid-bbb]
Output written to: all_matched_records_2026-06-09.csv
Duplicates written to: all_duplicate_matched_records_2026-06-09.csv
```