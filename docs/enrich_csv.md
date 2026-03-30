# enrich_csv.py

Enriches a target DSpace CSV file with journal and publisher data. Three modes are available:

- **CSV mode** matches rows by `handle` and copies columns from another CSV;
- **JSON mode** matches rows by journal title against a local journal JSON file (exported from Pure API);
- **log mode** matches rows by journal title against an uploader JSON log and injects journal UUIDs only.

---

## Requirements

```bash
pip install pandas
```

---

## Usage

```bash
python enrich_csv.py <target> <source> --mode <csv|json|log>
```

### Arguments

| Argument | Required | Description |
|---|---|---|
| `target` | Yes | Path to the CSV file to enrich. |
| `source` | Yes | Path to the source CSV, JSON, or log file. |
| `--mode` | Yes | `csv`, `json`, or `log` — see below. |

The output is written to `enriched_<target_filename>` in the same directory as the target file. The target file is never modified.

---

## Modes

### CSV mode (`--mode csv`)

Matches rows between the target and source CSV by their `handle` column, then copies journal and publisher columns from the source into the target.

**Columns copied from source:**

| Column | Description |
|---|---|
| `journal_title` | Journal name |
| `journal_issn` | ISSN(s) |
| `journal_uuid` | Journal UUID |
| `publisher_name` | Publisher name |
| `publisher_uuid` | Publisher UUID |

Columns missing from the source are skipped with a warning. Existing values in the target are only overwritten when the source has a non-null value for that column.

```bash
python enrich_csv.py target.csv source.csv --mode csv
```

---

### JSON mode (`--mode json`)

Matches rows in the target CSV by journal title against a local JSON file, then populates journal and publisher columns.

**Columns populated in target:**

| Column | Source field in journal JSON |
|---|---|
| `journal_uuid` | `uuid` |
| `publisher_uuid` | `publisher.uuid` |
| `journal_title` | `titles[0].title` |

The JSON file must contain an array of journal objects, or a dict with an `items` array.

The title column in the CSV is detected automatically (case-insensitive: prefers a column containing both `journal` and `title`, falls back to any column containing `title`).

**Matching strategy** (in order):
1. **Composite match** — normalised title + `journal_uuid`. Used when the target row already has a `journal_uuid` value; more precise as it disambiguates journals with identical titles.
2. **Title-only fallback** — normalised title alone, stripped of punctuation (see below).

**Title normalisation** (used for comparison only — originals are never modified):
```python
_COMPARISON_STRIP = str.maketrans("", "", """—!–¿()-[]{};:'"''""‐\\,<>./?@#$%^&=+|£€*_~®™©0123456789""")

def _normalise(s: str) -> str:
    return s.strip().lower().translate(_COMPARISON_STRIP)
```

```bash
python enrich_csv.py target.csv journals.json --mode json
```

---

### Log mode (`--mode log`)

Matches rows in the target CSV by journal title against an uploader log JSON file and injects `journal_uuid` only. No publisher UUID is available in this mode.

**Columns populated in target:**

| Column | Source field in log |
|---|---|
| `journal_uuid` | `uuid` |
| `journal_title` | Preserved from existing value; column added if absent |

The log file must be an array of uploader log entries. Only entries where `success` is `true` and `type` is `journals` are used. Matching is on normalised title (same `_normalise` function as JSON mode).

**Expected log entry format:**
```json
{
  "type": "journals",
  "name": "Journal of Example Studies",
  "uuid": "d159f747-a98c-4952-ae41-4dd17c075f53",
  "success": true
}
```

```bash
python enrich_csv.py target.csv upload_log.json --mode log
```

---

## Output

After each run the script prints a summary, for example:

```
============================================================
ENRICHMENT STATISTICS (LOG MODE)
============================================================
Total rows in target file:  500
Rows with matching titles:  390
Rows updated with new data: 390
Rows not matched:           110

Match rate:  78.00%
Update rate: 78.00%
============================================================
```