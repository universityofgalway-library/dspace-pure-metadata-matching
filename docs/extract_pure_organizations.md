# extract_pure_organizations.py

A script that merges internal and external organization records exported from the Elsevier Pure API into a single, unified mapping file. It normalises name variants across languages and produces a flat JSON structure suitable for lookups and downstream processing. It is intended to support author matching, metadata enrichment, and other workflows that need to resolve organization names to Pure identifiers.

---

## Requirements

- Python 3.7+
- No third-party dependencies (standard library only)

---

## Configuration

Unlike the other scripts in this project, this script does not use command-line arguments. Input and output paths are set as constants near the top of the file:

```python
INTERNAL_ORG_JSON = "./pure_entities/2026-03-02/pure_test_organizations_2026-03-02.json"
EXTERNAL_ORG_JSON = "./pure_entities/2026-03-02/pure_test_external-organizations_2026-03-02.json"
OUTPUT_JSON        = f"./pure_entities/organizations_mapping_{TODAY}.json"
```

Update these paths before running if your files are in a different location.

---

## Usage

```bash
python extract_pure_organizations.py
```

No arguments are required. The output directory is created automatically if it does not exist.

---

## Input Format

Both input files are JSON arrays produced by the Pure API (e.g. via the [Pure API Data Fetcher](pure_api_fetcher_docs.md)). Each element is an organization object.

### Name extraction

The script handles two formats for the `name` field:

- **Dictionary** (multilingual): all language-keyed values are extracted.
- **String**: used directly.

For **internal organizations**, the `nameVariants` array is also processed. Each variant's `value` field is handled in the same way as `name` (dictionary or string).

All extracted names are deduplicated while preserving their original order.

### Visibility

The `visibility` field is expected to be a dictionary with a `key` property (e.g. `"FREE"`, `"RESTRICTED"`). If the field is absent or not a dictionary, visibility is recorded as an empty string.

---

## Output

A single JSON file is written to the path defined by `OUTPUT_JSON`, named with today's date:

```
organizations_mapping_2026-03-30.json
```

The file contains a JSON array where each element represents one organization:

```json
[
  {
    "pureId": 12345,
    "uuid": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
    "name": [
      "School of Engineering",
      "Scoil na hInnealtóireachta"
    ],
    "internal": true,
    "external": false,
    "visibility": "FREE"
  },
  ...
]
```

### Output fields

| Field | Type | Description |
|---|---|---|
| `pureId` | `int` or `null` | Numeric Pure identifier |
| `uuid` | `str` or `null` | UUID of the organization in Pure |
| `name` | `list[str]` | All name variants, deduplicated, in extraction order |
| `internal` | `bool` | `true` for records from the internal organizations file |
| `external` | `bool` | `true` for records from the external organizations file |
| `visibility` | `str` | Visibility key from Pure (e.g. `FREE`, `RESTRICTED`) |

Note: `internal` and `external` are always opposite booleans. Both fields are included for convenience when filtering.

---

## Console Output

The script prints progress and statistics to stdout:

```
============================================================
Organization Mapping Generator
============================================================

📂 Loading internal organizations from: ./pure_entities/...
✅ Loaded 412 internal organizations

📂 Loading external organizations from: ./pure_entities/...
✅ Loaded 8304 external organizations

🔄 Processing organizations...
✅ Processed 8716 total organizations
   - Internal: 412
   - External: 8304

💾 Writing mapping to: ./pure_entities/organizations_mapping_2026-03-30.json
✅ Done! Mapping saved successfully.

============================================================
Statistics:
============================================================

Visibility breakdown:
  FREE: 7901
  RESTRICTED: 815

============================================================
Sample entries (first 3):
============================================================
...
```

---

## Error Handling

- If an input file is not found, a warning is printed and that source is skipped (an empty list is used in its place). The script continues with whichever source loaded successfully.
- If an input file contains invalid JSON, an error is printed and that source is treated as empty.
- If an individual organization record raises an exception during processing, a warning is printed with the record's UUID and the record is skipped. All other records are still processed.

---

## Notes

- Both `internal` and `external` sources are processed even if one fails to load, so a partial output is always written rather than aborting entirely.
- The output file path includes today's date, so repeated runs will not overwrite previous outputs unless they were produced on the same day.
- All JSON output uses UTF-8 encoding and 2-space indentation.
- To update the input paths (e.g. after a new Pure export), edit the `INTERNAL_ORG_JSON` and `EXTERNAL_ORG_JSON` constants at the top of the script.