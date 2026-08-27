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

Store keys in a `.env` file in the working directory:

```env
PURE_ROOT_API_KEY=your_production_api_key_here
PURE_ROOT_API_KEY_TEST=your_uat_api_key_here
```

`PURE_ROOT_API_KEY` is used by default (production **and** `--temp`). `PURE_ROOT_API_KEY_TEST` is used only when `--test` is set — there is no separate key for `--temp`; it reuses the production key. If the required key is not found, the script will print a warning and continue with an empty key (requests will likely be rejected by the API).

---

## Usage

```bash
python get_pure_data.py [OPTIONS]
```

### Example: fetch all entity types from the UAT staging environment

```bash
python get_pure_data.py --test
```

### Example: fetch only persons from production (default)

```bash
python get_pure_data.py --data persons
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

### Example: fetch from the TEMP environment

```bash
python get_pure_data.py --temp --data persons
```

### Example: fetch from a custom API endpoint

```bash
python get_pure_data.py --api-endpoint https://custom-pure-instance.example.com/ws/api/ --data journals
```

---

## Arguments

| Argument | Type | Default | Description |
|---|---|---|---|
| `--test` | flag | *(omit for production)* | Target the UAT staging environment. Takes priority over `--temp` if both are set. |
| `--temp` | flag | *(omit for production)* | Target the TEMP environment. Uses the same API key as production (`PURE_ROOT_API_KEY`) — there is no dedicated TEMP key. Ignored if `--test` is also set. |
| `--api-endpoint` | `str` | *(none)* | Custom API base URL. Overrides `--test`/`--temp`/production entirely when set — see [API Environments](#api-environments). |
| `--data` | `str` | `all` | Entity type to fetch. Use `all` to fetch every supported type, or specify one (see [Supported Entity Types](#supported-entity-types)). |
| `--output-dir` | `str` | `./pure_entities` | Directory where output JSON files are saved. Created automatically if it does not exist. |
| `--split-by-type` | flag | `False` | For `research-outputs` only: split results into separate files per subtype (e.g. `contributiontojournal`, `bookanthology`). |
| `--filename-prefix` | `str` | `pure_<env_label>_<data_type>` | Custom prefix for the output filename, where `<env_label>` is `prod`, `test`, or `temp` depending on which environment flag is set. When fetching a single type, the prefix applies to that file. ⚠️ When using `--data all`, an explicit `--filename-prefix` is applied unchanged to **every** entity type, so each type overwrites the previous type's output file — leave it unset with `--data all` to get one distinct file per type. |

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

Where `<filename_prefix>` defaults to `pure_<env_label>_<data_type>` (see [Arguments](#arguments)) unless overridden with `--filename-prefix`.

**Example (default prefix, production):**

```
pure_prod_persons_2026-04-28.json
```

**Example (default prefix, `--test`):**

```
pure_test_persons_2026-04-28.json
```

When `--split-by-type` is used with `research-outputs`, a separate file is created per subtype:

```
pure_prod_research-outputs_2026-04-28_contributiontojournal.json
pure_prod_research-outputs_2026-04-28_bookanthology.json
pure_prod_research-outputs_2026-04-28_contributiontoconference.json
...
```

Subtypes are derived from the second-to-last path segment of the `type.uri` field within each research output record (e.g. `contributiontojournal` from `.../contributiontojournal/article`).

- Records whose `type.uri` is **present but doesn't split into at least two path segments** are grouped under `unknown`.
- Records with a **missing or empty** `type.uri` are **not** written to any output file — they are silently dropped from the split output. If you need to account for every fetched record, compare the total reported by the fetch step (`✅ Total research-outputs fetched: N`) against the sum of items across the per-subtype files.

---

## API Environments

| Mode | Flag | URL |
|---|---|---|
| Production — default | *(none)* | `https://research.universityofgalway.ie/ws/api/` |
| UAT / Staging | `--test` | `https://galway-staging.elsevierpure.com/ws/api/` |
| TEMP | `--temp` | `https://galway-test.elsevierpure.com/ws/api/` |
| Custom | `--api-endpoint <url>` | User-supplied URL |

If more than one is set, precedence is: `--api-endpoint` > `--test` > `--temp` > production (default).

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
- Output files include today's date in the filename, so repeated runs will only overwrite previous exports made on the same day. Since the default prefix also embeds the environment (`prod`/`test`/`temp`), running against different environments on the same day produces distinctly named files and won't collide — unless a custom `--filename-prefix` is supplied, in which case no environment or date-based distinction is added beyond the date suffix.
- All JSON files are saved with UTF-8 encoding and 2-space indentation.