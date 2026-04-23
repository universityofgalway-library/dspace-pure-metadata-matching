# enrich_csv.py

Enriches a target DSpace CSV file with journal and publisher data. Three modes are available:

- **CSV mode** matches rows by `handle` and copies columns from another CSV;
- **JSON mode** matches rows by journal or publisher name against a local JSON file (exported from Pure API);
- **log mode** matches rows by journal or publisher name against an uploader JSON log and injects UUIDs only.

---

## Requirements

```bash
pip install pandas
```

---

## Usage

```bash
python enrich_csv.py <target> <source> --mode <csv|json|log> [--type <journals|publishers>]
```

### Arguments

| Argument | Required | Description |
|---|---|---|
| `target` | Yes | Path to the CSV file to enrich. |
| `source` | Yes | Path to the source CSV, JSON, or log file. |
| `--mode` | Yes | `csv`, `json`, or `log` — see below. |
| `--type` | For `json` and `log` modes | `journals` or `publishers` — specifies which record type to match against. |

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

Matches rows in the target CSV against a local Pure JSON export, then populates journal or publisher columns. Requires `--type`.

#### `--type journals`

Matches by journal title against a journals JSON file.

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
2. **Title-only fallback** — normalised title alone, stripped of punctuation (see [Title normalisation](#title-normalisation) below).

```bash
python enrich_csv.py target.csv journals.json --mode json --type journals
```

#### `--type publishers`

Matches by publisher name against a publishers JSON file.

**Columns populated in target:**

| Column | Source field in publisher JSON |
|---|---|
| `publisher_uuid` | `uuid` |

The publisher name used for matching is resolved from the CSV row in this order:
1. `dc.publisher` (if non-empty)
2. `publisher_name` (if non-empty)

**Expected publisher JSON format:**
```json
[
  {
    "uuid": "fe6cfb73-4742-4d9c-a471-fb6b3886e6f4",
    "name": "Springer",
    ...
  }
]
```

The JSON file must contain an array of publisher objects, or a dict with an `items` array.

```bash
python enrich_csv.py target.csv publishers.json --mode json --type publishers
```

---

### Log mode (`--mode log`)

Matches rows in the target CSV by name against an uploader log JSON file and injects a UUID only. Requires `--type`.

The log file must be an array of uploader log entries. Only entries where `success` is `true` and `type` matches the specified `--type` value are used. Matching is on normalised name (same `_normalise` function as JSON mode).

**Expected log entry format:**
```json
{
  "type": "journals",
  "name": "Journal of Example Studies",
  "uuid": "d159f747-a98c-4952-ae41-4dd17c075f53",
  "success": true
}
```

#### `--type journals`

Matches by journal title. The title column is detected automatically (same logic as JSON mode).

**Columns populated in target:**

| Column | Source field in log |
|---|---|
| `journal_uuid` | `uuid` |

```bash
python enrich_csv.py target.csv upload_log.json --mode log --type journals
```

#### `--type publishers`

Matches by publisher name, resolved from `dc.publisher` (preferred) or `publisher_name`.

**Columns populated in target:**

| Column | Source field in log |
|---|---|
| `publisher_uuid` | `uuid` |

```bash
python enrich_csv.py target.csv upload_log.json --mode log --type publishers
```

---

## Title normalisation

Used for all name comparisons in JSON and log modes. Original values in the CSV and JSON are never modified.

```python
_COMPARISON_STRIP = str.maketrans("", "", """—!–¿()-[]{};:'"''""‐\\,<>./?@#$%^&=+|£€*_~®™©0123456789""")

def _normalise(s: str) -> str:
    return s.strip().lower().translate(_COMPARISON_STRIP)
```

---

## Output

After each run the script prints a summary, for example:

```
============================================================
ENRICHMENT STATISTICS (LOG MODE — PUBLISHERS)
============================================================
Total rows in target file:  500
Rows with matching names:   390
Rows updated (publisher_uuid): 390
Rows not matched:           110

Match rate:  78.00%
============================================================
```