# prepare_uploads.py

A command-line tool for preparing entity records to upload to Pure. Four entity types are supported as individual sub-commands, and any combination can be run together in a single invocation.

## Requirements

- Python 3.10 or later (uses the `X | Y` union type syntax)
- No third-party packages — only the standard library

## Usage

```
python prepare_uploads.py <command> [options]
```

Append `--help` to any command for its full option list:

```bash
python prepare_uploads.py authors --help
python prepare_uploads.py funders --help
python prepare_uploads.py journals --help
python prepare_uploads.py publishers --help
python prepare_uploads.py run --help
python prepare_uploads.py all --help
```

---

## CSV delimiter detection

All commands that accept a CSV file (`funders`, `publishers`, `journals`, `run`, `all`) automatically detect whether the file uses comma or tab as its delimiter by sniffing the first non-empty line. No configuration is needed.


## Commands

### `authors` — Extract external persons

Reads a JSON array of author objects and extracts those where **both** `internal` and `external` flags are `false`, formatting them as `ExternalPerson` records.

```
python prepare_uploads.py authors <input> [-o OUTPUT] [--sample]
```

| Argument | Description |
|---|---|
| `input` | Path to the source authors JSON file |
| `-o / --output` | Output path (default: `<input_dir>/authors_to_upload_<today>.json`) |
| `--sample` | Print the first output record to stdout after writing |
| `--log-dir` | Directory for log files (default: `<output_dir>/logs)` |
| `--sample` | Print the first output record to stdout after writing |

**Input format** — a JSON array where each object may contain:

```json
[
  { "firstName": "Jane", "lastName": "Smith", "internal": false, "external": false }
]
```

**Output format:**

```json
[
  {
    "name": { "firstName": "Jane", "lastName": "Smith" },
    "type": {
      "uri": "/dk/atira/pure/externalperson/externalpersontypes/externalperson/externalperson",
      "term": { "en_IE": "External person" }
    },
    "workflow": { "step": "forApproval" },
    "systemName": "ExternalPerson"
  }
]
```

**Example:**

```bash
python prepare_uploads.py authors ./data/merged_authors_2026-03-25.json --sample
```

---

### `funders` — Find missing funders

Reads funder names from a CSV column, compares them case-insensitively against an organisations JSON, and writes missing ones as `ExternalOrganization` (type: `researchFundingBody`) records. Names longer than 10 words are skipped automatically with a warning. Matching against existing organisations is case- and punctuation-insensitive, but punctuation is preserved in the output.

```
python prepare_uploads.py funders <csv> <organisations> [-o OUTPUT] [--column COLUMN]
```

| Argument | Description |
|---|---|
| `csv` | Path to the input CSV file (delimiter auto-detected) |
| `organisations` | Path to a Pure organisations JSON file for name matching |
| `-o / --output` | Output path (default: `<organisations_dir>/funders_to_upload_<today>.json`) |
| `--column` | CSV column containing funder values (default: `dc.contributor.funder`) |
| `--log-dir` | Directory for log files (default: `<output_dir>/logs)` |
| `--sample` | Print the first output record to stdout after writing |

Multiple funders in a single cell must be separated by semicolons (`;`).

**Output format:**

```json
[
  {
    "name": { "en_IE": "Science Foundation Ireland" },
    "type": {
      "uri": "/dk/atira/pure/ueoexternalorganisation/ueoexternalorganisationtypes/ueoexternalorganisation/researchFundingBody"
    },
    "visibility": { "key": "FREE" },
    "workflow": { "step": "forApproval" },
    "systemName": "ExternalOrganization"
  }
]
```

**Example:**

```bash
python prepare_uploads.py funders ./exports/items.csv ./data/organisations.json \
  --column "dc.contributor.funder"
```

---

### `journals` — Create or update journals

Reads a journal CSV (delimiter auto-detected), matches each row against an existing journals JSON (by UUID first, then by title), and produces two output files: one for journals that must be created and one for journals that need updating.

```
python prepare_uploads.py journals <csv> <existing> [--output-create FILE] [--output-update FILE]
```

| Argument | Description |
|---|---|
| `csv` | Path to the input CSV file (delimiter auto-detected) |
| `existing` | Path to the existing journals JSON file |
| `--output-create` | Output for new journals (default: `./unmatched_records/journals_to_create_<timestamp>.json`) |
| `--output-update` | Output for journals needing updates (default: `./unmatched_records/journals_to_update_<timestamp>.json`) |
| `--log-dir` | Directory for log files (default: `<output_dir>/logs)` |
| `--sample` | Print the first output record to stdout after writing |

**Expected CSV columns:**

| Column | Description |
|---|---|
| `journal_title` | Journal title (required) |
| `journal_issn` | Semicolon-separated ISSNs |
| `journal_uuid` | UUID of an existing journal (optional) |
| `publisher_uuid` | UUID of the publisher to link (optional) |

**Matching logic:**

1. If `journal_uuid` is present → look up by UUID; generate an update record if the publisher or ISSNs differ.
2. If no UUID → look up by title (case-insensitive). If found, check for updates; if not found, generate a create record.

**Example:**

```bash
python prepare_uploads.py journals ./exports/journals.csv ./data/existing_journals.json \
  --output-create ./out/to_create.json \
  --output-update ./out/to_update.json
```

---

### `publishers` — Find missing publishers

Reads publisher names from a CSV column, compares them case-insensitively against an organisations JSON, and writes missing ones as `Publisher` records. Names longer than 10 words are skipped automatically with a warning. This command uses the same matching logic as `funders`.

```
python prepare_uploads.py publishers <csv> <organisations> [-o OUTPUT] [--column COLUMN]
```

| Argument | Description |
|---|---|
| `csv` | Path to the input CSV file (delimiter auto-detected) |
| `organisations` | Path to a Pure organisations JSON file for name matching |
| `-o / --output` | Output path (default: `<organisations_dir>/publishers_to_upload_<today>.json`) |
| `--column` | CSV column containing publisher values (default: `dc.publisher`) |
| `--log-dir` | Directory for log files (default: `<output_dir>/logs)` |
| `--sample` | Print the first output record to stdout after writing |

Multiple publishers in a single cell must be separated by semicolons (`;`).

**Output format:**

```json
[
  {
    "name": "Elsevier",
    "type": {
      "uri": "/dk/atira/pure/publisher/publishertypes/publisher/publisher",
      "term": { "en_IE": "Publisher" }
    },
    "workflow": { "step": "forApproval" },
    "systemName": "Publisher"
  }
]
```

**Example:**

```bash
python prepare_uploads.py publishers ./exports/items.csv ./data/organisations.json \
  --column "dc.publisher"
```

---

### `run` — Run multiple commands together

Run any subset of the four commands in a single invocation. Only supply the inputs that the chosen commands actually need. The `--commands` flag is required.

```
python prepare_uploads.py run --commands COMMAND [COMMAND ...] [options]
```

**Available commands for `--commands`:** `authors` `funders` `publishers` `journals`

#### Shared inputs

| Flag | Used by | Description |
|---|---|---|
| `--csv` | funders, publishers, journals | CSV file (delimiter auto-detected) |
| `--organisations` | funders, publishers | Organisations JSON for name matching |

#### Per-command inputs

| Flag | Command | Description |
|---|---|---|
| `--authors-input` | authors | Input authors JSON |
| `--authors-output` | authors | Output path for authors |
| `--sample` | all | Print the first output record for each command |
| `--funders-column` | funders | CSV column (default: `dc.contributor.funder`) |
| `--funders-output` | funders | Output path for funders |
| `--publishers-column` | publishers | CSV column (default: `dc.publisher`) |
| `--publishers-output` | publishers | Output path for publishers |
| `--journals-existing` | journals | Existing journals JSON |
| `--journals-output-create` | journals | Output path for journals to create |
| `--journals-output-update` | journals | Output path for journals to update |

**Example — funders and publishers from the same CSV:**

```bash
python prepare_uploads.py run \
  --commands funders publishers \
  --csv ./exports/items.csv \
  --organisations ./data/organisations.json
```

**Example — all four commands:**

```bash
python prepare_uploads.py run \
  --commands authors funders publishers journals \
  --authors-input ./data/authors.json \
  --csv ./exports/items.csv \
  --organisations ./data/organisations.json \
  --journals-existing ./data/journals.json
```

---

### `all` — Run all four commands at once

Shortcut for `run --commands authors funders publishers journals`. Accepts the same optional flags as `run`.

```
python prepare_uploads.py all --authors-input FILE --csv FILE --organisations FILE --journals-existing FILE [options]
```

**Example:**

```bash
python prepare_uploads.py all \
  --authors-input ./data/authors.json \
  --csv ./exports/items.csv \
  --organisations ./data/organisations.json \
  --journals-existing ./data/journals.json
```

---

## Output notes

- All output files are written as indented, UTF-8 encoded JSON.
- Parent directories are created automatically if they do not exist.
- Default output paths include today's date or a timestamp so repeated runs never overwrite previous results.
- All records are placed in workflow step `forApproval`, meaning they require manual review in the target system before going live.
- The organisations JSON passed to `funders` and `publishers` can be either an internal or external Pure organisations export; both name formats (plain string list and locale dict) are handled automatically.