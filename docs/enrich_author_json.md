# enrich_author_json.py

Injects UUIDs from a creation log into an authors JSON file. For each author
who is neither internal nor external, it looks for an exact name match in an uploader
log and, on a hit, sets `external: true` and appends the UUID to `externalUUIDs`.

---

## Requirements

Python 3.9+ — no third-party dependencies.

---

## Usage

```bash
python enrich_authors.py --authors-file <path> --log-file <path> [--entity-type <type>]
```

### Arguments

| Argument | Required | Default | Description |
|---|---|---|---|
| `--authors-file` | Yes | — | Path to the authors JSON file to enrich. |
| `--log-file` | Yes | — | Path to the creation log JSON file. |
| `--entity-type` | No | `external-persons` | The `type` field value to filter log entries by. Use `external-organizations` for organizations. |

The script writes the enriched data to `updated_<original-filename>` in the
same directory as the source file. The source file is never modified.

---

## Input formats

### Authors JSON

An array of author objects. Relevant fields:

```json
[
  {
    "firstName": "R.",
    "lastName": "Aabenhus",
    "internal": false,
    "external": false,
    "externalUUIDs": []
  }
]
```

| Field | Type | Role |
|---|---|---|
| `firstName` | string | Used to build the match key. |
| `lastName` | string | Used to build the match key. |
| `internal` | boolean | If `true`, the author is skipped. |
| `external` | boolean | If `true`, the author is skipped. |
| `externalUUIDs` | array | The matched UUID is appended here. |

### Log JSON

An array of uploader log entries for newly created external persons. Relevant fields:

```json
[
  {
    "type": "external-persons",
    "name": "Aabenhus, R.",
    "uuid": "d159f747-a98c-4952-ae41-4dd17c075f53",
    "success": true
  }
]
```

| Field | Type | Role |
|---|---|---|
| `type` | string | Must equal `--entity-type` or the entry is ignored. |
| `name` | string | Match key — must be in `"lastName, firstName"` format. |
| `uuid` | string | Injected into the author on a match. |
| `success` | boolean | Must be `true` or the entry is ignored. |

---

## Matching logic

An author is a candidate if **both** `internal` and `external` are `false`.

For each candidate, the script builds a key from the author's JSON fields:

```
"lastName, firstName"  →  lowercased
```

This is compared against the lowercased `name` field of every eligible log
entry. The match is **exact** (case-insensitive only) — no fuzzy logic,
no variant handling. If the strings differ by even one character the entry
is not matched.

---

## Output

On a match the author object is updated in place:

- `external` is set to `true`.
- The UUID is appended to `externalUUIDs` (skipped if already present).

Authors with no match and authors that were skipped are left unchanged.

---

## Example

```bash
python enrich_authors.py \
  --authors-file data/persons.json \
  --log-file data/creation_log.json
```

Console output:

```
Loading authors file : data/persons.json
Loading log file     : data/creation_log.json
Log entries loaded   : 2 (type='external-persons', success=True)

  ✓  R. Aabenhus                               →  d159f747-a98c-4952-ae41-4dd17c075f53
  ✗  J. Smith  (no match found)

============================================================
SUMMARY
============================================================
  Total authors         : 3
  Matched & updated     : 1
  No match found        : 1
  Skipped (already i/e) : 1

  Output written to     : data/updated_persons.json
```
