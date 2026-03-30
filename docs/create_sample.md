# create_sample.py

A command-line tool that samples records from a DSpace metadata CSV file by document type, with optional author validation against a JSON author registry.

---

## Overview

This script reads an enriched DSpace metadata CSV, filters records by document type, optionally validates authors against a known-authors JSON file, and writes a stratified sample to a new CSV. It is designed to support QA workflows by producing representative, reproducible subsets of large metadata exports.

---

## Requirements

- Python 3.7+
- `pandas`

Install dependencies:

```bash
pip install pandas
```

---

## Usage

```bash
python create_sample.py [OPTIONS]
```

All arguments are optional and fall back to sensible defaults.

### Example: basic run with defaults

```bash
python create_sample.py
```

### Example: custom input/output with author filtering disabled

```bash
python create_sample.py \
  --input-file ./data/my_metadata.csv \
  --output-file ./output/sample.csv \
  --no-filter-authors \
  --sample-size 10
```

### Example: require specific fields to be non-empty

```bash
python create_sample.py \
  --required-fields dc.title dc.date.issued dc.description.abstract \
  --sample-size 3
```

---

## Arguments

| Argument | Type | Default | Description |
|---|---|---|---|
| `--input-file` | `str` | `./dspace_data/all_data_test/enriched_dspace_test_metadata_2026-02-13.csv` | Path to the input CSV file |
| `--output-file` | `str` | `./dspace_data/test_samples/dspace_test_sample_<TODAY>.csv` | Base path for the output CSV (a date suffix is appended automatically) |
| `--authors-json` | `str` | `./author_matching/2026-02-26/updated_merged_all_authors_2026-02-26.json` | Path to the authors JSON registry |
| `--random-state` | `int` | `42` | Random seed for reproducible sampling |
| `--filter-authors` | flag | `True` | Filter records to only those with authors found in the JSON registry (default behaviour) |
| `--no-filter-authors` | flag | — | Disable author filtering; sample from all records regardless of author match |
| `--required-fields` | `str...` | `[]` | Space-separated list of CSV column names that must be non-empty. Records with all required fields populated are prioritised in sampling. |
| `--sample-size` | `int` | `5` | Number of records to sample per document type |

---

## Input Format

### CSV file

The input CSV must contain at least these two columns:

- `dc.type` — document type string (see [Supported Document Types](#supported-document-types))
- `dc.contributor.author` — semicolon-separated list of authors in `Lastname, Firstname` format

**Example author field value:**

```
Smith, John; Doe, Jane; Müller, Hans
```

### Authors JSON

A JSON array of author objects. Each object may contain:

```json
{
  "firstName": "Jane",
  "lastName": "Doe",
  "alternativeFirstName": ["J."],
  "alternativeLastName": [],
  "internal": ["uuid-1234"],
  "external": ["orcid-5678"]
}
```

An author is considered **valid** if they have at least one entry in `internal` or `external`. Authors with neither are indexed in the lookup but will not pass validation.

---

## Output

The output is a CSV file written to the path specified by `--output-file`, with a date suffix appended before the extension:

```
sample.csv  →  sample_2026-03-30.csv
```

The output contains the sampled rows with all original columns preserved.

---

## Supported Document Types

The script samples from the following `dc.type` values:

- `journal article`
- `review article`
- `review`
- `conference paper`
- `conference output`
- `conference poster`
- `book part`
- `book`
- `report`
- `conference proceedings`
- `working paper`
- `video`
- `interactive resource`
- `newspaper article`
- `book review`
- `data management plan`
- `other`

Document types not in this list are ignored. Types present in the list but absent from the data are skipped silently (`doctoral thesis`, `master thesis`).

---

## Sampling Logic

For each document type, up to `--sample-size` records are selected as follows:

1. **Author filtering** (if enabled): only records where *every* listed author matches a valid entry in the authors JSON are retained.
2. **Required fields prioritisation** (if `--required-fields` is set):
   - Records where all required fields are non-empty are selected first.
   - If there are not enough such records to meet the sample size, the remainder is filled from records that are missing one or more required fields.
3. **Random sampling** using the specified `--random-state` for reproducibility.

If a document type has fewer records than `--sample-size`, all available records are included.

---

## Author Matching

Author names from the CSV (`Lastname, Firstname`) are normalised and compared against all name variants in the JSON registry, including `alternativeFirstName` and `alternativeLastName`. Both `(first, last)` and `(last, first)` orderings are checked to handle inconsistent input formatting.

A publication passes author validation only if **every** author in the `dc.contributor.author` field can be matched to a valid registry entry.

---

## Notes

- The output file path always has today's date appended, so repeated runs do not overwrite previous outputs.
- The random state default of `42` ensures runs with the same inputs produce identical samples.
- Commented-out document types in the source (`doctoral thesis`, `master thesis`, `data management plan`) are intentionally excluded from sampling.
