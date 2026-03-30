# Pure API Data Fetcher

A command-line tool for extracting data from the Elsevier Pure research information system via its REST API. Supports paginated bulk fetching of all major entity types, with optional splitting of research outputs by subtype.

## Requirements

- Python 3.7+
- `requests`
- `python-dotenv`
- `tqdm`

Install dependencies:

```bash
pip install requests python-dotenv tqdm
```

---

## Authentication

The script requires a Pure API key with **root access** to retrieve all metadata fields (including restricted fields such as keywords). A regular user API key will work but may return incomplete records.

Store the key in a `.env` file in the working directory:

```env
PURE_ROOT_API_KEY=your_api_key_here
```

If the key is not found, the script will print a warning and continue with an empty key (requests will likely be rejected by the API).

---

## Usage

```bash
python get_pure_data.py [OPTIONS]
```

### Example: fetch all entity types from the staging environment

```bash
python get_pure_data.py --test True
```

### Example: fetch only persons from production

```bash
python get_pure_data.py --test False --data persons
```

### Example: fetch research outputs split by subtype, with a custom output directory

```bash
python get_pure_data.py \
  --data research-outputs \
  --split-by-type \
  --output-dir ./exports/research
```

### Example: fetch a single type with a custom filename prefix

```bash
python get_pure_data.py \
  --data journals \
  --filename-prefix galway_journals \
  --output-dir ./exports
```

---

## Arguments

| Argument | Type | Default | Description |
|---|---|---|---|
| `--test` | `bool` | `True` | Target environment. `True` uses the UAT staging URL; `False` uses the production URL. |
| `--data` | `str` | `all` | Entity type to fetch. Use `all` to fetch every supported type, or specify one (see [Supported Entity Types](#supported-entity-types)). |
| `--output-dir` | `str` | `./pure_entities` | Directory where output JSON files are saved. Created automatically if it does not exist. |
| `--split-by-type` | flag | `False` | For `research-outputs` only: split results into separate files per subtype (e.g. `contributiontojournal`, `conferencepaper`). |
| `--filename-prefix` | `str` | `pure_test_<data_type>` | Custom prefix for the output filename. Only applies when fetching a single data type. |

---

## Supported Entity Types

The following values are valid for `--data`:

- `research-outputs`
- `persons`
- `external-persons`
- `journals`
- `events`
- `awards`
- `organizations`
- `external-organizations`
- `publishers`

Passing `all` fetches every type in the order listed above.

---

## Output

Each entity type is saved as a JSON file in the output directory. The filename follows this pattern:

```
<filename_prefix>_<YYYY-MM-DD>.json
```

**Example:**

```
pure_test_persons_2026-03-30.json
```

When `--split-by-type` is used with `research-outputs`, a separate file is created per subtype:

```
pure_test_research-outputs_2026-03-30_contributiontojournal.json
pure_test_research-outputs_2026-03-30_conferencepaper.json
pure_test_research-outputs_2026-03-30_book.json
...
```

Subtypes are derived from the `type.uri` field within each research output record.

---

## API Environments

| Mode | URL |
|---|---|
| UAT / Staging (`--test True`) | `https://galway-staging.elsevierpure.com/ws/api/` |
| Production (`--test False`) | `https://research.universityofgalway.ie/ws/api/` |

---

## Pagination

The script fetches records in pages of 1,000 items. Before the main fetch loop, it makes a single lightweight request to retrieve the total record count, which is used to display a progress bar. Pagination continues until all records have been retrieved or an error is encountered.

---

## Error Handling

- If the total count cannot be determined, the progress bar is disabled and the fetch continues without it.
- A non-200 HTTP response at any page stops fetching for that entity type and logs the status code and response body.
- Network errors (`requests.exceptions.RequestException`) at any page are caught and logged, and the fetch for that type is abandoned.
- Errors for individual types do not abort the run when fetching `all`; the script proceeds to the next type and reports a summary at the end.

---

## Summary Output

After all types have been processed, the script prints a summary:

```
============================================================
📊 Summary:
   ✅ Successful: 8
   ❌ Failed: 1
   📁 Output directory: ./pure_entities
============================================================
```

---

## Notes

- Root API key access is required to retrieve all metadata fields. With a regular user key, fields such as keywords may be missing from the response.
- The `--filename-prefix` argument is only applied when fetching a single data type. When `--data all` is used, each type receives its own default prefix (`pure_test_<data_type>`).
- Output files include today's date in the filename, so repeated runs will only overwrite previous exports made on the same day.
- All JSON files are saved with UTF-8 encoding and 2-space indentation.
