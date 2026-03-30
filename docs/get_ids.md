# get_ids.py

A command-line tool that matches records between a DSpace CSV export and a Pure JSON export by Handle URL, producing a joined CSV of identifiers from both systems with duplicate detection.

The script reads a DSpace CSV (keyed by `handle`) and a Pure JSON export (keyed by links of type `Handle`), joins them, and writes a CSV containing the DSpace UUID, Pure UUID, Pure ID, and handle for each matched pair. Rows involving duplicate handles or DSpace UUIDs are flagged in an extra column.

It is intended to be sent over to Elsevier to establish a connection between individual Pure and DSpace records for the Pure > DSpace connector to work.
---

## Requirements

- Python 3.10+
- No third-party dependencies (standard library only)

---

## Usage

```bash
python get_ids.py --csv CSV_FILE --json JSON_FILE [OPTIONS]
```

Both `--csv` and `--json` are required.

### Example: basic match

```bash
python get_ids.py \
  --csv dspace_export.csv \
  --json pure_research_outputs.json
```

### Example: filter by modifier and date, custom output path

```bash
python get_ids.py \
  --csv dspace_export.csv \
  --json pure_research_outputs.json \
  --output ./results/matched_2026-03-30.csv \
  --modified-by "john@example.com" \
  --modified-after "2025-01-01"
```

### Example: filter by date only

```bash
python get_ids.py \
  --csv dspace_export.csv \
  --json pure_research_outputs.json \
  --modified-after "2025-06-01"
```

---

## Arguments

| Argument | Required | Default | Description |
|---|---|---|---|
| `--csv` | ✅ | — | Path to the DSpace CSV input file |
| `--json` | ✅ | — | Path to the Pure JSON input file |
| `--output` | | `matched_records.csv` | Path for the output CSV file |
| `--modified-by` | | `None` | Only include Pure records whose `modifiedBy` field exactly matches this value (e.g. `john@example.com`) |
| `--modified-after` | | `None` | Only include Pure records modified strictly after this date. Format: `YYYY-MM-DD` |

Both `--modified-by` and `--modified-after` can be combined. If neither is set, all Pure records are considered.

---

## Input Format

### DSpace CSV

A standard DSpace metadata export. The file must contain at minimum:

- `handle` — a bare handle (e.g. `10379/6474`) or full Handle URL. Records without a handle value are ignored.
- `uuid` — the DSpace item UUID.

The file is read with UTF-8 encoding and BOM stripping (`utf-8-sig`), so CSV exports from Excel or DSpace's own export tools are handled correctly.

### Pure JSON

A JSON array of research output records, as produced by the [Pure API Data Fetcher](/docs/get_pure_data.md). Each record must contain:

- `links` — an array of link objects. Links with `"alias": "Handle"` are used for matching.
- `uuid` — the Pure record UUID.
- `pureId` — the numeric Pure identifier.

Optionally, for filtering:

- `modifiedBy` — the user who last modified the record (string).
- `modifiedDate` — ISO-8601 datetime string (e.g. `2025-12-14T10:30:00Z`).

---

## Matching Logic

1. Every Pure record is checked for links with `alias == "Handle"`.
2. Each Handle URL is looked up in the DSpace records dictionary (keyed by full Handle URL).
3. On a match, a row is produced containing the DSpace UUID, Handle URL, Pure UUID, and Pure ID.
4. Rows where any of these four fields is empty are skipped and counted separately.
5. If a single Pure record has multiple Handle links, only the first matching Handle is used to avoid duplicate output rows.

---

## Output

A CSV file written to the path specified by `--output`:

```
dspace_uuid,handle,pure_uuid,pure_id,duplicate_flag
a1b2c3d4-...,http://hdl.handle.net/10379/6474,e5f6a7b8-...,12345,
...
```

### Output columns

| Column | Description |
|---|---|
| `dspace_uuid` | UUID of the matched DSpace item |
| `handle` | Full Handle URL used to make the match |
| `pure_uuid` | UUID of the matched Pure record |
| `pure_id` | Numeric Pure identifier |
| `duplicate_flag` | Pipe-separated list of duplicate reasons, or empty if none |

### Duplicate flag values

| Value | Meaning |
|---|---|
| `duplicate_handle` | The same Handle URL appears in more than one output row |
| `duplicate_dspace_uuid` | The same DSpace UUID appears in more than one output row |
| `duplicate_handle\|duplicate_dspace_uuid` | Both conditions apply to this row |

Duplicate rows are included in the output and flagged rather than removed, so conflicts can be investigated manually.

---

## Console Output

The script prints a step-by-step summary to stdout:

```
Loading CSV from: dspace_export.csv
  → 4312 records with Handles loaded.
Loading JSON from: pure_research_outputs.json
  → 8760 records loaded.
  → 312 records after filtering.
Matching records by Handle…
  → 287 complete matches found.
  → 4 matched records skipped due to one or more empty fields.
  → WARNING: 2 duplicate keys detected (same handle or dspace_uuid maps to multiple pure_uuids).
     • http://hdl.handle.net/10379/6474  →  [uuid-aaa, uuid-bbb]
Output written to: matched_records.csv
  → Rows with duplicates are flagged in the 'duplicate_flag' column.
```

---

## Notes

- Bare handles in the DSpace CSV (e.g. `10379/6474`) are automatically prefixed with `http://hdl.handle.net/` before matching. Records that already contain a full URL are used as-is.
- The `--modified-after` filter is strictly greater than — records modified exactly on the given date are excluded.
- Pure records with a missing or unparseable `modifiedDate` are excluded when `--modified-after` is set.
- Python 3.10 or later is required due to the use of the `X | Y` union type hint syntax (`str | None`).