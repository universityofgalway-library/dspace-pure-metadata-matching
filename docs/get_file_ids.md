# get_file_ids.py

Matches records between a DSpace CSV export and a Pure JSON export — by DSpace UUID first, then by Handle. For each matched pair, it pairs the DSpace PDF files with the Pure `FileElectronicVersion` entries by filename, builds DSpace-style file IDs, and writes the identifiers from both systems in two formats:

- **per record** — one row per matched record, file columns semicolon-separated;
- **per file** — one row per file, each DSpace file on the same row as the Pure file it matched.

It also reports duplicates (several Pure records for one DSpace item) and unmatched records on both sides.

The per-file output replaces the separate `reformat_csv_for_jens.py` script, which is no longer needed.

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

# Custom output path (Windows paths work too: --output .\results\matched_records.csv)
python get_file_ids.py --csv dspace_export.csv --json pure_outputs.json \
  --output ./results/matched_records.csv

# Filter by modifier and date
python get_file_ids.py --csv dspace_export.csv --json pure_outputs.json \
  --modified-by "john@example.com" \
  --modified-after "2025-01-01"

# Only include records where every file matched on both sides
python get_file_ids.py --csv dspace_export.csv --json pure_outputs.json \
  --pdf-filter full_match

# Only include specific Handles
python get_file_ids.py --csv dspace_export.csv --json pure_outputs.json \
  --handles "10379/7309,10379/1501,10379/5890"

# Only include Handles found in a Pure XML import file
python get_file_ids.py --csv dspace_export.csv --json pure_outputs.json \
  --xml-filter sample_temp_pure_import.xml
```

---

## Arguments

| Argument | Required | Default | Description |
|---|---|---|---|
| `--csv` | Yes | — | Path to the DSpace CSV input file |
| `--json` | Yes | — | Path to the Pure JSON input file |
| `--output` | No | `./pure_temp/temp_test_matched_records.csv` | Base path for the output CSVs. The `--pdf-filter` value and today's date are added to the file name automatically — see [Output Files](#output-files) |
| `--modified-by` | No | `None` | Only include Pure records whose `modifiedBy` field exactly matches this value |
| `--modified-after` | No | `None` | Only include Pure records modified after the start of this date (00:00 UTC). Format: `YYYY-MM-DD` |
| `--pdf-filter` | No | `all` | Filter output by PDF match type — see [PDF Filter](#pdf-filter) |
| `--handles` | No | `None` | Only include records whose Handle is in this comma-separated list — see [Handle Filter](#handle-filter) |
| `--xml-filter` | No | `None` | Only include records whose Handle matches a record id in this Pure XML import file — see [Handle Filter](#handle-filter) |

`--modified-by` and `--modified-after` can be combined with each other. `--handles` and `--xml-filter` can also be combined with each other (see [Handle Filter](#handle-filter)). If none of these are set, all Pure records are considered.

---

## Input Format

### DSpace CSV

A standard DSpace metadata export. Read with UTF-8 encoding and BOM stripping (`utf-8-sig`), so CSV exports from DSpace's own export tools and Excel are handled correctly.

Required columns:

| Column | Description |
|---|---|
| `handle` | Bare handle (e.g. `10379/6474`) or Handle URL. Records without a handle value are ignored. If two rows have the same handle, the later one is used for Handle matching and a warning is printed. |
| `uuid` | DSpace item UUID. Required for output — rows where this is empty are skipped. |

Optional columns used when present:

| Column | Description |
|---|---|
| `pdf_handle_paths` | Semicolon-separated paths to DSpace PDFs, e.g. `/10379/4728/1/file.pdf`. Drives file pairing against Pure file records, and supplies the sequence number and filename used to build `dspace_file_id` — see [DSpace File IDs](#dspace-file-ids). |
| `pdf_links` | Semicolon-separated DSpace bitstream content URLs, e.g. `https://.../server/api/core/bitstreams/{uuid}/content`, aligned by position with `pdf_handle_paths`. Supplies the bitstream UUID used to build `dspace_file_id`. If missing, shorter than `pdf_handle_paths`, or a UUID can't be found in an entry, the corresponding `dspace_file_id` falls back to the raw `pdf_handle_paths` value instead of being dropped. |
| `dc.title`, `dc.type`, `collection_names` | Copied into the [unmatched DSpace report](#unmatched-reports). |

### Pure JSON

A JSON array of research output records as produced by `get_pure_data.py` (read as UTF-8, with or without a BOM).

Required fields:

| Field | Description |
|---|---|
| `uuid` | Pure record UUID. Required for output — rows where this is empty are skipped. |
| `pureId` | Numeric Pure identifier. Required for output — rows where this is empty are skipped. |

Fields used for matching (a record needs at least one of them to be matched):

| Field | Description |
|---|---|
| `identifiers` | Entries with `idSource == "DSpace"` (any letter case) hold the record's DSpace item UUID — in a `"typeDiscriminator": "PrimaryId"` entry (written by `match_records.py`) or an `"typeDiscriminator": "Id"` entry (Pure's import); other identifier types with `idSource "DSpace"` are read too. A record has **one** DSpace UUID: `PrimaryId` first, then `Id` (see [Matching Logic](#matching-logic)). UUIDs are compared case-insensitively. |
| `links` | Every link whose `url` is a Handle — whatever its `alias` — provides a Handle for matching. See [Matching Logic](#matching-logic) for the accepted forms. |

Optional fields:

| Field | Description |
|---|---|
| `title` | Used to populate the `title` output column. Read from `title.value`. |
| `electronicVersions` | Used to extract `FileElectronicVersion` file metadata — see [File Pairing](#file-pairing). |
| `modifiedBy` | String. Used by the `--modified-by` filter. |
| `modifiedDate` | ISO-8601 datetime string, e.g. `2026-04-22T13:27:18.721+01:00`. Used by the `--modified-after` filter and by duplicate resolution. A date with an offset, with `Z`, or without an offset (taken as UTC) is accepted, with any number of fractional-second digits. Records with a missing or unparseable `modifiedDate` are excluded when `--modified-after` is active, and treated as least-recently-modified during duplicate resolution. |

---

## Matching Logic

1. The DSpace CSV is indexed by two keys on load: Handle (canonical form) and DSpace item UUID (lower case).
2. For each Pure record, a DSpace match is attempted in priority order:
   - **DSpace UUID first** — the record's DSpace UUID is looked up in the DSpace UUID index, ignoring letter case. It is read from the `identifiers` entries with `idSource "DSpace"`: the `PrimaryId` entry's value if there is one, otherwise the `Id` entry's (then any other identifier type). The same UUID in both a `PrimaryId` and an `Id` entry counts once.
   - **Handle fallback** — only if that UUID isn't in the CSV (or the record has none): the links in the Pure record's `links` array whose URL is a Handle are looked up in the DSpace Handle index, and the first one found is used.
3. Handles are compared in canonical form (`http://hdl.handle.net/<handle>`, lower case). Accepted forms — on both sides and in `--handles` — are a bare handle (`10379/6474`) or a URL with `hdl.handle.net`, `handle.net` or `www.handle.net` host, with or without `http://` / `https://`, in any letter case, with or without trailing slashes. These are the same forms `match_records.py` and `add_pdfs_to_pure.py` accept.
4. If neither lookup finds a match, the Pure record isn't in the output. If it carries a DSpace UUID or a Handle, it's listed in the [unmatched Pure report](#unmatched-reports); if it carries neither, it isn't a DSpace record and is only counted.
5. **A Pure record belongs to one DSpace item** and produces at most one row. A record that carries **several distinct DSpace UUIDs** (e.g. a `PrimaryId` and an `Id` with different values), or — when matched by Handle — Handles of **several DSpace items** in the CSV, is a data error: only the identifier chosen above is used, a warning is printed, and the record is listed in the [multiple DSpace IDs report](#multiple-dspace-ids). The other DSpace items it points at are not listed as unlinked in the unmatched DSpace report.
   Each **file** has its own identifier on both sides — the DSpace bitstream UUID (from `pdf_links`, see [DSpace File IDs](#dspace-file-ids)) and Pure's `fileId` / file `pureId` — these are never taken for the record's DSpace UUID.
6. If a `--handles` and/or `--xml-filter` filter is active and the matched Handle isn't in it, the row is excluded and counted separately — see [Handle Filter](#handle-filter).
7. On a match, identifiers and file information are collected from both records (see [File Pairing](#file-pairing) and [DSpace File IDs](#dspace-file-ids)). The `handle` value in the output is the canonical Handle URL of the matched DSpace item.
8. Rows where any of the four core fields (`dspace_uuid`, `pure_uuid`, `pure_id`, `handle`) is empty are skipped and counted separately.

---

## File Pairing

DSpace PDFs and Pure `FileElectronicVersion` entries are paired **one to one** by filename — a Pure file is never paired with two DSpace files. All files from both sides always appear in the output, whether they matched or not.

- The base filename is extracted from each DSpace path (URL-decoded, trailing slashes stripped), then cleaned: HTML character references still literally present (e.g. an accented character rendered as `&#769;`) are decoded, and the result is Unicode-normalized (NFKC).
- **Direct match:** a DSpace file and a Pure file are paired when their names are equal after Pure's filename normalisation (characters that are not alphanumeric, hyphens, underscores, dots or spaces become underscores).
- **Fallback match:** only for the files still unpaired after every direct match has been made, a looser comparison is tried: both names are further reduced by stripping diacritics and collapsing any `_NNN_` remnant. That is the shape Pure's own storage-time normalization leaves behind when a file was uploaded before entity-decoding existed, e.g. `Me_769_liacin.pdf`. This recovers files uploaded under their old, mangled name. Because direct matches are made first, a fallback match can never take a file that has a direct match.
- If several Pure files have the same normalised name (e.g. a duplicate upload), the first unpaired one is used and the other is reported as an unmatched Pure file.

Each file gets a status (the `file_match` column of the [per-file output](#per-file-output-columns)):

| `file_match` | Meaning |
|---|---|
| `matched` | DSpace file and Pure file paired by the direct comparison |
| `matched_fallback` | Paired by the fallback comparison — worth a glance |
| `dspace_only` | DSpace file with no matching Pure file |
| `pure_only` | Pure file with no matching DSpace file |
| `no_files` | The record has no files on either side (one row, so the record still appears) |

The counts of DSpace files, Pure files and matched pairs determine the record's `file_match_type` — see [PDF Filter](#pdf-filter).

---

## DSpace File IDs

The `dspace_file_id` output column holds a full DSpace-style file identifier for each PDF, in the format:

```
{handle}:{seq}/{file uuid}:{filename}
```

For example:

```
10379/1034:1/3c246f30-8d3a-4077-a013-6ca4ef262a3e:paper_0078.pdf
```

This mirrors how `create_pure_xml.py` builds DSpace `<file id="...">` values, and is constructed as follows:

- **`{handle}`** — the bare handle of the matched DSpace item (no `http://hdl.handle.net/` prefix).
- **`{seq}`** — the sequence number parsed from the corresponding `pdf_handle_paths` entry (e.g. the `1` in `/10379/1034/1/paper_0078.pdf`). Defaults to `1` if the path has no sequence segment.
- **`{filename}`** — the filename parsed from the same `pdf_handle_paths` entry, URL-decoded.
- **`{file uuid}`** — the DSpace bitstream UUID, extracted from the corresponding `pdf_links` entry (matched by position). The UUID is found with a regex search over the URL, so it works regardless of host or path structure (e.g. `.../server/api/core/bitstreams/{uuid}/content`).

Each entry in `pdf_handle_paths` is processed independently; the `pdf_links` entry at the same position supplies its UUID. If `pdf_links` is missing, shorter than `pdf_handle_paths`, or no UUID can be found in the aligned entry, that file's `dspace_file_id` falls back to the raw `pdf_handle_paths` value (e.g. `/10379/2000/1/nolink.pdf`) rather than being dropped or fabricated.

---

## PDF Filter

The `--pdf-filter` flag controls which matched records appear in the output (both the per-record and the per-file file). The same label is written to the `file_match_type` column of every row.

| Value | `file_match_type` | Included records |
|---|---|---|
| `all` (default) | varies | Every matched record |
| `full_match` | `full_match` | Both sides have files, and **every DSpace file and every Pure file** is in a matched pair |
| `partial_match` | `partial_match` | At least one pair matched, but at least one file on **either side** is unmatched — including extra Pure files when every DSpace file matched |
| `file_name_mismatch` | `file_name_mismatch` | Both sides have files but no file pair matched |
| `dspace_only_pdf` | `dspace_only_pdf` | DSpace has files but the Pure record has no `FileElectronicVersion` entries |
| `pure_only_pdf` | `pure_only_pdf` | Pure has `FileElectronicVersion` entries but DSpace has no `pdf_handle_paths` |
| `no_pdf` | `no_pdf` | Neither DSpace nor Pure have any files |

> **Change from earlier versions:** `full_match` used to require only that every DSpace file matched, so a record with an extra (e.g. old) Pure file counted as `full_match`. Such records are now `partial_match`.

---

## Handle Filter

`--handles` and `--xml-filter` restrict output to a specific set of Handles. Both are optional and off by default; when used together, the two sets of Handles are **unioned** (a record is included if its Handle is in either set).

### `--handles`

A comma-separated list of handles, e.g.:

```
--handles "10379/7309"
--handles "10379/7309,10379/1501,10379/5890"
```

Each entry may be a bare handle (`10379/7309`) or a Handle URL in any of the accepted forms (see [Matching Logic](#matching-logic)); all are normalised the same way as the CSV's `handle` column before comparison.

### `--xml-filter`

Path to a Pure XML import file (e.g. a `temp_pure_import` XML produced by `create_pure_xml.py`). Handles are extracted from the `id` attribute of each **top-level record element** — the direct children of the document root, such as:

```xml
<book id="10379/7309" subType="edited_book">
<contributionToJournal id="10379/17295" subType="article">
<workingPaper id="10379/1034" subType="working_paper">
```

Nested elements with an `id` attribute — `<person id="...">`, `<organisation id="...">`, `<file id="...">`, etc. — use unrelated ID schemes (UUIDs, or the DSpace `{handle}:{seq}/{uuid}:{filename}` format) and are not considered.

If the file can't be read or isn't valid XML, the script prints an error and exits with a non-zero status.

### Applying the filter

The filter is applied to each row's **matched** Handle (that of the DSpace item the Pure record was matched to — see [Matching Logic](#matching-logic)), not to the raw Handles present in the Pure JSON's `links` array. Rows excluded by the filter are counted and reported separately from those skipped for other reasons. The unmatched reports are limited to the filter's Handles too.

---

## Output Files

All files are written next to the `--output` base path, with today's date added to the file name. The per-record, per-file and duplicates files also get the `--pdf-filter` value as a prefix. Folder paths with `/` or (on Windows) `\` both work. Files are UTF-8 **with a BOM**, so Excel shows accented characters correctly.

| File | Naming pattern | Contents |
|---|---|---|
| Per record | `<filter>_<name>_<YYYY-MM-DD>.csv` | One row per matched record, after duplicate resolution |
| Per file | `<filter>_per_file_<name>_<YYYY-MM-DD>.csv` | One row per file of the same records — DSpace and Pure files paired |
| Duplicates | `<filter>_duplicate_<name>_<YYYY-MM-DD>.csv` | All rows involved in duplicate collisions (only written if duplicates exist) |
| Unmatched DSpace | `unmatched_dspace_<name>_<YYYY-MM-DD>.csv` | DSpace items no Pure record links to — see [Unmatched Reports](#unmatched-reports) |
| Unmatched Pure | `unmatched_pure_<name>_<YYYY-MM-DD>.csv` | Pure records whose DSpace UUID / Handle isn't in the CSV |
| Multiple DSpace IDs | `multiple_dspace_ids_<name>_<YYYY-MM-DD>.csv` | Pure records with several DSpace UUIDs, or Handles of several DSpace items (only written if any) — see [Multiple DSpace IDs](#multiple-dspace-ids) |

For example, with `--output results/matched_records.csv` and `--pdf-filter all` run on 2026-06-09:

```
results/all_matched_records_2026-06-09.csv
results/all_per_file_matched_records_2026-06-09.csv
results/all_duplicate_matched_records_2026-06-09.csv
results/unmatched_dspace_matched_records_2026-06-09.csv
results/unmatched_pure_matched_records_2026-06-09.csv
results/multiple_dspace_ids_matched_records_2026-06-09.csv
```

The output folder is created if it doesn't exist.

### Per-record output columns

| Column | Description |
|---|---|
| `dspace_uuid` | UUID of the matched DSpace item |
| `pure_uuid` | UUID of the matched Pure record |
| `pure_id` | Numeric Pure identifier (`pureId`) |
| `title` | Pure record title (`title.value`) |
| `dspace_file_id` | Semicolon-separated DSpace file IDs in `{handle}:{seq}/{file uuid}:{filename}` format — see [DSpace File IDs](#dspace-file-ids) |
| `pure_file_id` | Semicolon-separated `fileId` values for all Pure `FileElectronicVersion` entries |
| `pure_file_pure_id` | Semicolon-separated `pureId` values for all Pure file objects |
| `pure_file_name` | Semicolon-separated `fileName` values for all Pure files |
| `file_match_type` | PDF match classification for this record — see [PDF Filter](#pdf-filter) |
| `handle` | Handle URL of the matched DSpace item |

**Order of the file values:** matched files come first and **line up by position** — the n-th DSpace file matched the n-th Pure file. Then come the unmatched DSpace files (in `dspace_file_id`) and the unmatched Pure files (in the three Pure columns). So the lists line up only for the first *matched-count* entries. For per-file work, use the per-file output, which states every pairing explicitly. A filename that itself contains `;` (rare, e.g. `…Noisy Data; an Additive…`) makes the semicolon-separated lists ambiguous to split, but the per-file output isn't affected.

### Per-file output columns

The same columns as the per-record output, with **one file per row** and one extra column:

| Column | Description |
|---|---|
| `dspace_file_id` | One DSpace file ID, or empty if this row is an unmatched Pure file |
| `pure_file_id`, `pure_file_pure_id`, `pure_file_name` | One Pure file, or empty if this row is an unmatched DSpace file |
| `file_match` | Status of this file pair — see [File Pairing](#file-pairing) |

Record-level columns (`dspace_uuid`, `pure_uuid`, `pure_id`, `title`, `file_match_type`, `handle`) are repeated on every row of the record. Rows are in the same order as the per-record lists: matched pairs, then unmatched DSpace files, then unmatched Pure files. A record without any files has one row with `file_match = no_files`.

### Duplicates output columns

Identical to the per-record output, with one additional leading column:

| Column | Description |
|---|---|
| `duplicate_key` | The `dspace_uuid` or handle that triggered the collision group |

---

## Unmatched Reports

### Unmatched DSpace items

Every DSpace CSV row (with a handle) that **no Pure record links to** — neither by DSpace UUID nor by Handle — whatever the filters on the output. These are items that are missing from Pure, or whose Pure record lacks both the DSpace UUID and the Handle. The CSV contains all collections and types, so filter on `collection_names` and `dc.type` as needed.

| Column | Description |
|---|---|
| `dspace_uuid` | DSpace item UUID |
| `handle` | Handle URL |
| `title` | `dc.title` |
| `dc.type` | Item type |
| `collection_names` | Collection(s) |
| `pdf_count` | Number of entries in `pdf_handle_paths` |

This report is **not written** when `--modified-by` or `--modified-after` is used, since only part of Pure is read then. With `--handles` / `--xml-filter`, it's limited to those Handles.

### Unmatched Pure records

Pure records that carry a DSpace UUID or a Handle, none of which is in the CSV — for example, an item deleted from or not included in the DSpace export, or a stale link. Pure records with neither aren't DSpace records; they're only counted in the console output.

| Column | Description |
|---|---|
| `pure_uuid`, `pure_id`, `title` | The Pure record |
| `dspace_uuids` | Its DSpace UUIDs (semicolon-separated) |
| `handles` | Its Handles (semicolon-separated) |
| `reason` | `DSpace UUID(s) not in the CSV`, `Handle(s) not in the CSV`, or both |

With `--handles` / `--xml-filter`, only records with a Handle in the filter are listed.

### Multiple DSpace IDs

Pure records that carry **more than one distinct DSpace UUID**, or that were matched by Handle and carry **Handles of more than one DSpace item** in the CSV. A record should have exactly one; these need fixing in Pure. The output uses only one of them (`used_dspace_uuid` / `used_handle`).

| Column | Description |
|---|---|
| `pure_uuid`, `pure_id`, `title` | The Pure record |
| `dspace_uuids` | All its DSpace UUIDs (semicolon-separated; `PrimaryId` first, then `Id`) |
| `handles` | All its Handles (semicolon-separated) |
| `used_dspace_uuid`, `used_handle` | The DSpace item the output row uses (empty if neither is in the CSV) |
| `problem` | `several DSpace UUIDs`, `Handles of several DSpace items in the CSV`, or both |

---

## Duplicate Resolution

After matching, the script detects rows where the same `dspace_uuid` or `handle` maps to more than one Pure record. Rather than emitting all candidates, it selects the best one per collision group and excludes the rest from the per-record and per-file outputs.

**Selection criteria (descending priority):**

1. Most matched DSpace/Pure file pairs.
2. Most Pure `FileElectronicVersion` entries in total.
3. Most recently modified (`modifiedDate` from the Pure record). Records missing a `modifiedDate` rank lowest.

All candidates (winner and losers) are written to the duplicates output file for review. A warning is printed for each collision group, showing which Pure record was kept, its score, and which were dropped; a group found under both the DSpace UUID and the Handle of the same item is reported once. Rows are identified by Pure record **and** DSpace item.

---

## Console Output

```
Loading CSV from: dspace_export.csv
  → 15496 records with Handles loaded.
Loading JSON from: pure_outputs.json
  → 123529 records loaded.
Matching records by DSpace UUID, then Handle…
  → 12480 complete matches found.
  → WARNING: 3 Pure record(s) carry several DSpace UUIDs (or Handles of several DSpace items) — only one is used (PrimaryId first, then Id).
  → 110950 Pure record(s) have no DSpace UUID or Handle (not DSpace records).
  → WARNING: 1 duplicate keys detected (same dspace_uuid or handle maps to multiple Pure records).
     • 63ea6fca-4fcc-4d29-9a06-35a62b773832
       kept:    uuid-aaa  (score (1, 1, datetime.datetime(...)))
       dropped: [uuid-bbb]
Output written to: all_matched_records_2026-06-09.csv
Per-file output written to: all_per_file_matched_records_2026-06-09.csv (12950 rows)
Duplicates written to: all_duplicate_matched_records_2026-06-09.csv
DSpace items no Pure record links to: 3016 → unmatched_dspace_matched_records_2026-06-09.csv
Pure records whose DSpace UUID / Handle is not in the CSV: 12 → unmatched_pure_matched_records_2026-06-09.csv
Pure records with several DSpace UUIDs / Handles: 3 → multiple_dspace_ids_matched_records_2026-06-09.csv
```

The numbers above are illustrative. Lines for the `--modified-by`/`--modified-after` filter (`X records after filtering`), the Handle filter (`X handle(s) …`, `X matched records excluded by the Handle filter`), skipped rows, repeated CSV Handles and Pure records with several DSpace UUIDs / Handles only appear when they apply.
