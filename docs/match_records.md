# Research Output Record Matching Script

Matches and enriches research output records between DSpace (institutional repository) and Pure (research information system).

---

## Overview

The script performs three main tasks:

1. **Match records** across systems using DOIs, handles, and title similarity
2. **Update existing Pure records** with DSpace metadata (precedence-based or override)
3. **Create new Pure records** for unmatched DSpace items

---

## Usage

```bash
python match_records.py
```

### Dependencies

```bash
pip install requests python-dotenv tqdm rapidfuzz python-dateutil --break-system-packages
```

### API Keys

```bash
echo "PURE_ROOT_API_KEY=your-production-key-here" >> .env
echo "PURE_ROOT_API_KEY_TEST=your-uat-key-here" >> .env
```

The variable used depends on `USE_TEST_ENV` (see Configuration below).

---

## Configuration

Edit these variables at the top of the script:

```python
OVERRIDE_MODE = False         # True: replace all fields; False: fill blanks only
COLLECT_EXTERNAL_ORGS = False # True: attach external org data to contributors/records
USE_TEST_ENV = False          # True: use UAT environment and test org config

DSPACE_CSV                = "./dspace_data/export.csv"
PURE_JSON                 = "./pure_data/research-outputs.json"
PERSON_MAPPING_JSON       = "./mappings/persons.json"
ORGANIZATION_MAPPING_JSON = "./mappings/organizations.json"
PUBLISHER_MAPPING_JSON    = "./pure_entities/pure_publishers.json"
OUTPUT_DIR                = f"./record_matching/prod_all_output_{TODAY}"
```

`USE_TEST_ENV` also controls which org config file is loaded:
- `True` → `./scripts/test_orgs_config.json`
- `False` → `./scripts/prod_orgs_config.json`

The org config file is required and must be a JSON object with the following keys:

```json
{
  "LIBRARY_REPOSITORY": "<uuid>",
  "CENTRAL_UNIVERSITY": ["<uuid>", ...],
  "EXTERNAL_ORGS_TO_IGNORE": ["<uuid>", ...]
}
```

---

## Input Files

| File | Format | Description |
|------|--------|-------------|
| `DSPACE_CSV` | CSV | DSpace metadata export — one record per row |
| `PURE_JSON` | JSON array | Pure research output records |
| `PERSON_MAPPING_JSON` | JSON array | Author name → Pure person UUID mappings |
| `ORGANIZATION_MAPPING_JSON` | JSON array | Org name → Pure org UUID mappings |
| `PUBLISHER_MAPPING_JSON` | JSON array | Publisher name → Pure publisher UUID mappings |

### Required DSpace CSV Columns

| Column | Description |
|--------|-------------|
| `collection_names` | Must equal `publications` (case-insensitive, exact match). Records not in a Publications collection are skipped. |
| `uuid` | DSpace item UUID — written to Pure as `PrimaryId` identifier |
| `dc.title` | Main title |
| `dc.title.subtitle` / `dc.title.alternative` | Subtitle (optional) |
| `dc.contributor.author` | Semicolon-separated author names |
| `dc.contributor.editor` | Semicolon-separated editor names |
| `dc.contributor.translator` | Semicolon-separated translator names |
| `dc.contributor.illustrator` | Semicolon-separated illustrator names |
| `dc.contributor.funder` | Semicolon-separated funder names |
| `dc.date.issued` | Publication date |
| `dc.date.embargo` | Embargo end date |
| `dc.identifier.doi` | Publisher DOI |
| `dc.identifier.uri` | Handle and/or repository DOI (semicolon-separated) |
| `dc.description.abstract` | Abstract text |
| `dc.description.sponsorship` | Funding acknowledgement text |
| `dc.language.iso` | ISO 639-3 language code (e.g. `eng`, `gle`) |
| `dc.publisher` | Publisher name — matched against `PUBLISHER_MAPPING_JSON` for applicable record types |
| `dc.rights` | Rights/licence label (e.g. `CC BY-NC-ND`) |
| `dc.type` | Resource type (e.g. `journal article`, `book`) |
| `journal_uuid` | Pure journal UUID (required for journal contributions) |

---

## Matching Strategy

Records are matched in priority order:

1. **Publisher DOI** — from `dc.identifier.doi`
2. **Repository DOI** — from `dc.identifier.uri`, pattern `10.13025/*`
3. **Handle** — from `dc.identifier.uri`, pattern `10379/*`
4. **Title** — two sub-strategies applied in order:
   - **Exact** — normalised title string match against index
   - **Fuzzy** — token-based candidate retrieval + fuzzy scoring, 90% threshold

When multiple Pure records match, the best is selected by: visibility (FREE/CAMPUS) → number of internal contributors → field completeness → whether last modified by a real user.

---

## Field Mapping & Update Rules

### Override Mode (`OVERRIDE_MODE = True`)

- Replaces all mapped fields with DSpace values
- Removes existing contributors/funders and uses only DSpace data
- Use with caution — overwrites curator work

### Precedence Mode (`OVERRIDE_MODE = False`)

- Uses precedence rules to update data in Pure
- Adds new contributors/funders without removing existing ones

| DSpace Field | Pure Field | Rule |
|---|---|---|
| `uuid` | `identifiers` (PrimaryId, idSource: DSpace) | Always set; demotes existing PrimaryId to Id |
| `dc.contributor.*` | `contributors` | Add new; preserve existing (precedence) |
| `dc.contributor.funder` | `fundingDetails` | Add new funders |
| `dc.date.issued` | `publicationStatuses[0].publicationDate` | Fill if blank |
| `dc.identifier.doi` | `electronicVersions` (publisher version) | Add if missing |
| `dc.identifier.uri` (DOI `10.13025/*`) | `electronicVersions` (repository version) | Add if missing; always overwrites `licenseType`, `versionType`, embargo |
| `dc.identifier.uri` (handle) | `links` | Set as repository handle link |
| `dc.description.abstract` | `abstract` | Fill if blank |
| `dc.description.sponsorship` | `fundingText` | Fill if blank |
| `dc.title` + `dc.title.subtitle` | `title` + `subTitle` | Fill if blank; subtitle stripped from title if embedded (see below) |
| `dc.language.iso` | `language` | Fill if blank |
| `dc.rights` | Repository version `licenseType` | Always overwrite |
| `dc.date.embargo` | Repository version `embargoPeriod` | Always overwrite |
| `dc.publisher` | `publisher` | Fill if blank (BookAnthology, ContributionToBookAnthology, OtherContribution, WorkingPaper, NonTextual types only) |
| `journal_uuid` | `journalAssociation.journal.uuid` | Fill if blank; if missing on journal/periodical types, record is downgraded to `OtherContribution` |
| _(always)_ | `workflow.step` | Always set to `validated` on every output record |

### Subtitle Stripping

If a DSpace title already contains the subtitle embedded after a colon (e.g. `"Main Title: The Subtitle"`), the script detects this and strips the subtitle portion from the `title` field before writing, using either `dc.title.subtitle` or Pure's existing `subTitle` as the reference. This prevents duplication like `"Main Title: The Subtitle"` + `subTitle: "The Subtitle"`.

### Authors Keyword Group Removal

When contributors are successfully resolved for a matched Pure record, any existing Pure keyword group with `logicalName == "/dk/atira/pure/authors"` is removed. This cleans up the unstructured author string that Pure stores before persons are properly linked.

### Unmatched Funders Fallback

If a funder cannot be matched to an organization UUID and there is no `dc.description.sponsorship` text, the unmatched funder names are written as plain text into `fundingText` so the information is not lost entirely.

---

## Identifier Handling

The DSpace `uuid` column is written into Pure's `identifiers` array as a `PrimaryId` with `idSource: "DSpace"`. Any pre-existing `PrimaryId` entries are demoted to `Id`.

```json
"identifiers": [
  {
    "typeDiscriminator": "PrimaryId",
    "idSource": "DSpace",
    "value": "0000000000000000000000000"
  },
  {
    "typeDiscriminator": "Id",
    "idSource": "Scopus",
    "value": "84892604475"
  }
]
```

**Rules:**
- Applied to both updated (matched) and newly created records
- Duplicate DSpace UUIDs are not added if already present
- If `uuid` is empty, a warning is printed to the processing log and the field is omitted

---

## Type Mapping

DSpace `dc.type` values are mapped to Pure output subtypes:

| DSpace type | Pure subtype URI |
|---|---|
| `journal article` | `/dk/atira/pure/researchoutput/researchoutputtypes/contributiontojournal/article` |
| `review article` | `/dk/atira/pure/researchoutput/researchoutputtypes/contributiontojournal/systematicreview` |
| `review` | `/dk/atira/pure/researchoutput/researchoutputtypes/contributiontojournal/systematicreview` |
| `conference paper` | `/dk/atira/pure/researchoutput/researchoutputtypes/contributiontoconference/paper` |
| `conference output` | `/dk/atira/pure/researchoutput/researchoutputtypes/contributiontoconference/other` |
| `conference poster` | `/dk/atira/pure/researchoutput/researchoutputtypes/contributiontoconference/poster` |
| `conference proceedings` | `/dk/atira/pure/researchoutput/researchoutputtypes/bookanthology/book` |
| `book part` | `/dk/atira/pure/researchoutput/researchoutputtypes/contributiontobookanthology/chapter` |
| `book` | `/dk/atira/pure/researchoutput/researchoutputtypes/bookanthology/book` |
| `report` | `/dk/atira/pure/researchoutput/researchoutputtypes/bookanthology/commissioned` |
| `working paper` | `/dk/atira/pure/researchoutput/researchoutputtypes/workingpaper/workingpaper` |
| `video` | `/dk/atira/pure/researchoutput/researchoutputtypes/nontextual/audiovisual_material` |
| `interactive resource` | `/dk/atira/pure/researchoutput/researchoutputtypes/nontextual/web_publication` |
| `newspaper article` | `/dk/atira/pure/researchoutput/researchoutputtypes/contributiontoperiodical/article` |
| `book review` | `/dk/atira/pure/researchoutput/researchoutputtypes/contributiontoperiodical/book` |
| `other` | `/dk/atira/pure/researchoutput/researchoutputtypes/othercontribution/other` |
| `data management plan` | `/dk/atira/pure/researchoutput/researchoutputtypes/othercontribution/other` |
| `doctoral thesis` | `/dk/atira/pure/researchoutput/researchoutputtypes/thesis/doc` |
| `master thesis` | `/dk/atira/pure/researchoutput/researchoutputtypes/thesis/master` |

**Note:**
- Unmapped types default to `/dk/atira/pure/researchoutput/researchoutputtypes/othercontribution/other`.
- `doctoral thesis` and `master thesis` mappings exist in the code but are currently commented out and will not be processed.

---

## Person Matching

Authors are matched via a pre-built name index supporting primary names, alternative names, and both name orders ("First Last" and "Last, First").

**Duplicate resolution priority:**
1. Paper evidence match (DOI or handle > title)
2. Internal Person > External Person
3. Visibility: FREE/CAMPUS > other
4. Most complete metadata (field count from Pure API)

### Contributor Roles

All four DSpace contributor fields are processed: `author`, `editor`, `translator`, `illustrator`. Special cases:

- **Author/editor overlap:** If the same name appears in both fields, one role is kept based on `dc.type` (editor preferred for `book`, `interactive resource`, `conference proceedings`; author preferred otherwise).
- **Editors-only non-book records:** If `dc.type` is not a book-like type and there are editors but no authors, editors are treated as authors (metadata correction).

---

## Language Mapping

ISO 639-3 codes from `dc.language.iso` are mapped to Pure locale codes. A broad set of languages is supported; a selection of notable mappings:

| DSpace code | Pure locale |
|---|---|
| `eng` | `en_IE` |
| `fra` / `fre` | `fr_FR` |
| `ger` / `deu` | `de_DE` |
| `spa` | `es_ES` |
| `gle` | `ga` |
| `wel` / `cym` | `cy_GB` |
| `gla` | `gd_GB` |
| `por` | `pt_PT` |
| `ita` | `it_IT` |
| `rus` | `ru_RU` |
| `zho` / `chi` | `zh_CN` |
| `jpn` | `ja_JP` |
| `ara` | `ar_SA` |

Unmapped codes default to `en_IE`.

**Irish-language workaround:** When `dc.language.iso` is `gle`, the abstract is written to both `ga` and `en_IE` keys in the `abstract` object. This is a workaround to ensure Irish-language abstracts display correctly in Pure.

---

## New Record Defaults

The following fields are hardcoded on all newly created records (unmatched DSpace items):

| Field | Value |
|---|---|
| `visibility` | `FREE` |
| `category` | `/dk/atira/pure/researchoutput/category/research` |
| `workflow.step` | `validated` |
| `managingOrganization` | First internal contributor's org (unless it is a Central University org — see below), or Library Repository UUID from org config |
| `language` | `en_IE` (overridden if `dc.language.iso` is present) |

---

## Organization Handling

**Internal organizations** are validated against the Pure API. If a UUID is invalid (not found as an internal org):

- If `COLLECT_EXTERNAL_ORGS = False`: the UUID is discarded and a warning is logged.
- If `COLLECT_EXTERNAL_ORGS = True`: the Pure external-organizations endpoint is checked. If found there, it is moved to `externalOrganizations`. If not found there either, it is discarded and a warning is logged.

**External organizations:** UUIDs listed in `EXTERNAL_ORGS_TO_IGNORE` (loaded from the org config file) are always filtered out.

**Managing organization:** Set to the first internal contributor's primary organization. If that organization is one of the Central University orgs defined in the org config (`CENTRAL_UNIVERSITY`), the Library Repository UUID is used instead. Also falls back to Library Repository UUID if no internal contributors exist.

**Record-level organizations** are collected from all resolved contributors and written to the top-level `organizations` (internal) and `externalOrganizations` (external, only if `COLLECT_EXTERNAL_ORGS = True`) arrays.

---

## Electronic Versions & Links

Repository DOIs (`10.13025/*`) are added as `authorsVersion` with the license derived from `dc.rights` (defaulting to `CC BY-NC`) and `OPEN` access (or `EMBARGOED` if an active embargo date is present).

Publisher DOIs are added as `publishersVersion`.

Version order in the output: repository DOI → publisher DOIs → other.

DOI links are removed from `links`; handles are kept. If DSpace and Pure have conflicting handles, a warning is printed and manual review is flagged.

---

## Publisher Matching

`dc.publisher` is looked up against `PUBLISHER_MAPPING_JSON` and set on the `publisher` field for records of type `BookAnthology`, `ContributionToBookAnthology`, `OtherContribution`, `WorkingPaper`, and `NonTextual`. Unmatched publisher names are written to `unmatched_publishers_YYYY-MM-DD.csv`.

---

## Output Structure

```
./record_matching/prod_all_output_YYYY-MM-DD/
├── matched/
│   ├── contributiontojournal_YYYY-MM-DD.json
│   ├── contributiontoconference_YYYY-MM-DD.json
│   └── ...
├── unmatched/
│   ├── contributiontojournal_YYYY-MM-DD.json
│   └── ...
├── logs/
│   ├── processing_log_YYYY-MM-DD.log
│   ├── status_log_YYYY-MM-DD.json
│   └── error_log_YYYY-MM-DD.log
├── matched_records_before_updates_YYYY-MM-DD.json
├── no_author_records_YYYY-MM-DD.csv
├── unmatched_contributors_YYYY-MM-DD.csv
├── unmatched_funders_YYYY-MM-DD.csv
└── unmatched_publishers_YYYY-MM-DD.csv
```

| Path | Contents |
|---|---|
| `matched/` | Updated records for existing Pure entries, grouped by type |
| `unmatched/` | New records to create in Pure, grouped by type |
| `processing_log` | Full console output with per-record detail |
| `status_log` | JSON array — one entry per DSpace record |
| `error_log` | Python tracebacks for unexpected exceptions only |
| `matched_records_before_updates` | Snapshot of Pure records before modification |
| `no_author_records.csv` | DSpace rows that were skipped because no contributors could be matched to Pure persons |
| `unmatched_contributors.csv` | Contributors not found in person mapping |
| `unmatched_funders.csv` | Funders not found in organization mapping |
| `unmatched_publishers.csv` | Publishers not found in publisher mapping |

### Status Log Entry

```json
{
  "handle": "10379/12345",
  "uuid": "abc-123-def",
  "pureType": "/dk/atira/pure/.../article",
  "matched": true,
  "duplicates": false,
  "success": true,
  "error": null,
  "matchType": "Publisher DOI",
  "matches": [
    {
      "pureUUID": "abc-123-def",
      "title": "Research Title",
      "matchType": "Publisher DOI"
    }
  ]
}
```

---

## Common Issues

### Summary Table

| Issue | Record output? | In `error_log`? | `status_log` error? | Where to find details |
|---|---|---|---|---|
| Not in Publications collection | ❌ | ❌ | ✅ `"Skipped: not in a Publications collection"` | `status_log` |
| No contributor fields at all | ❌ | ❌ | ✅ `"No contributors found in any contributor field"` | `status_log` |
| All contributors unmatched | ❌ | ❌ | ✅ `"No matched contributors"` | `status_log` + `no_author_records.csv` |
| Some contributors unmatched | ✅ (partial) | ❌ | ❌ | `processing_log` + `unmatched_contributors.csv` |
| Some/all funders unmatched | ✅ (partial/no funders) | ❌ | ❌ | `processing_log` + `unmatched_funders.csv` |
| Publisher not matched | ✅ (no publisher set) | ❌ | ❌ | `processing_log` + `unmatched_publishers.csv` |
| Invalid internal org UUID | ✅ | ❌ | ❌ | `processing_log` warning; UUID checked against external orgs endpoint, moved or discarded |
| No journal UUID | ✅ (as OtherContribution) | ❌ | ❌ | `processing_log` warning; type changed |
| Missing DSpace UUID | ✅ | ❌ | ❌ | `processing_log` warning; `identifiers` field omitted |
| Python exception | ❌ | ✅ | ✅ | `error_log` with traceback |

Only unexpected Python exceptions go to `error_log`. All other issues are handled gracefully and logged to `processing_log`.

### Unmatched Contributors Example

```
DSpace authors: "Smith, John", "Doe, Jane", "Unknown, Person"

  ➤ Checking match for author: 'Smith, John'
      ✅ Found 1 matches → Added author: John Smith
  ➤ Checking match for author: 'Doe, Jane'
      ✅ Found 1 matches → Added author: Jane Doe
  ➤ Checking match for author: 'Unknown, Person'
      ⚠️ No matches found — adding to unmatched

Result: record created with 2 contributors; "Unknown, Person" written to unmatched_contributors.csv
```

### No Journal UUID

```
⚠️ No journal UUID found for ContributionToJournal - changing to OtherContribution
```

The record is still created/updated but saved under `othercontribution_YYYY-MM-DD.json`.

---

## Performance

Lookup indices are built at startup for fast processing:

- **Person index:** all name variations → O(1) lookup
- **Organization index:** normalized org names → O(1) lookup
- **Publisher index:** normalized publisher names → O(1) lookup
- **Pure record indices:** DOIs, handles, titles → O(1) matching
- **API caches:** person metadata and org validation results are cached after first fetch

Typical throughput: ~1,000 records in 5–10 minutes with API calls enabled.