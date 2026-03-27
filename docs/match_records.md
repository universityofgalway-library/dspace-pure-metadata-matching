# Scripts for DSpace-Pure Metadata Integration

# `match_records.py`

## Table of Contents

1. [Overview](#overview)
2. [Configuration & Usage](#configuration--usage)
   - [Basic Execution](#basic-execution)
   - [Configuration](#configuration)
   - [Modes of Operation](#modes-of-operation)
   - [Input Files](#input-files)
   - [Output Files](#output-files)
3. [Record Matching](#record-matching)
   - [Matching Logic](#matching-logic)
   - [Contributor Processing](#contributor-processing)
   - [Funder Processing](#funder-processing)
   - [Electronic Versions & DOI Handling](#electronic-versions--doi-handling)
4. [Deduplication Strategies](#deduplication-strategies)
   - [Record Deduplication](#record-deduplication-resolve_record_duplicate)
   - [Person Deduplication](#person-deduplication-resolve_author_duplicate)
   - [Organisation Deduplication](#organisation-deduplication-resolve_funder_duplicate)
   - [Contributor Deduplication Within a Record](#contributor-deduplication-within-a-record)
5. [Type Mapping](#type-mapping)
   - [Type Downgrade](#type-downgrade)
   - [Peer Review Defaults](#peer-review-defaults)
6. [Metadata Update Rules](#metadata-update-rules)
   - [Field-Level Precedence](#field-level-precedence)
   - [Author Keyword Group](#author-keyword-group)
   - [Subtitle Stripping](#title-subtitle-stripping)
   - [External Organisation Filtering](#external-organisation-filtering)
7. [Logging & Diagnostics](#logging--diagnostics)
   - [Status Log](#1-status-log-status_log_datejson)
   - [Console / Processing Log](#2-console--processing-log-processing_log_yyyy-mm-ddlog)
   - [Error Log](#3-error-log-error_log_yyyy-mm-ddlog)
8. [Caching & Performance](#caching--performance)
9. [Known Limitations & Edge Cases](#known-limitations--edge-cases)

## Overview
 
This script is a data migration and enrichment tool that bridges two research information systems: **DSpace** (an institutional repository) and **Pure** (a research information management system). For each record in a DSpace CSV export, the script attempts to find a corresponding record in a Pure JSON export and update it with enriched metadata according to precedence rules. If no Pure match is found, a new Pure record is created from the DSpace data.
 
The script is designed to be run as a batch process over tens of thousands of records, with extensive logging to support manual review of edge cases. **NB!** The script and all data files use American spelling (e.g. "organization", not "organisation") to match Pure metadata schemas.


**High-level processing loop per DSpace record:**
 
1. Skip if no contributors present in any role field.
2. Attempt to match to an existing Pure record (4-step cascade).
   - **If matched**: update the Pure record according to precedence rules.
   - **If unmatched**: create a new Pure record from scratch.
5. Write outputs to type-partitioned JSON files.
6. Append to processing log.

## Configuration & Usage

### Basic Execution

```bash
python match_records.py
```

### Configuration

All configuration is set via constants at the top of the script. Key settings:
 
| Constant | Purpose | Default |
|---|---|---|
| `OVERRIDE_MODE` | When `True`, DSpace data overwrites existing Pure data in all fields. When `False`, metadata is updated according to the precedence rules. | `False` |
| `COLLECT_EXTERNAL_ORGS` | When `True`, external organisations are collected from external authors and attached to contributors and records. | `False` |
| `TITLE_SIMILARITY_THRESHOLD` | Minimum fuzzy match score (0–1) for title-based matching to be accepted. | `0.9` |
| `DSPACE_CSV` | Path to the DSpace metadata export. | Configurable |
| `PURE_JSON` | Path to the Pure research outputs export. | Configurable |
| `PERSON_MAPPING_JSON` | Path to the author name-to-UUID mapping file. | Configurable |
| `ORGANIZATION_MAPPING_JSON` | Path to the organisation name-to-UUID mapping file. | Configurable |
| `OUTPUT_DIR` | Root directory for all output files. | Date-stamped |
 
An `.env` file is required to provide the `PURE_ROOT_API_KEY` for API-based organisation validation. Without a key, organisation validation is skipped and some deduplication features are degraded.

### Modes of Operation
 
#### Standard Mode (`OVERRIDE_MODE = False`)
 
- The default setting.
- Updates strictly follow the precedence rules (see [Field-Level Precedence](#field-level-precedence)).
- Existing contributors, funders, and organisations are preserved and new ones appended.
- Recommended for initial enrichment passes.
 
#### Override Mode (`OVERRIDE_MODE = True`)
 
- DSpace data is written to all fields unconditionally.
- Existing Pure contributors are ignored; the contributor list is rebuilt entirely from DSpace.
- `managingOrganization`, `organizations`, `fundingDetails`, `fundingText`, `language`, `abstract`, `title`, `subTitle`, and `publicationStatuses` are all overwritten.
- Existing contributors are cleared before processing DSpace contributors.
- Recommended for corrective re-runs where DSpace is considered the source of truth.

### Input Files

1. **DSpace CSV** (e.g. `dspace_test_100.csv`): research outputs from DSpace
   - Comma-separated columns
   - Semicolon-separated values within columns
2. **Pure JSON** (e.g. `pure_test_research-outputs_2026-03-03.json`): existing Pure research outputs
3. **Person Mapping JSON** (e.g. `updated_merged_all_authors_2026-02-26.json`): pre-matched author mapping between DSpace names and Pure Person/ExternalPerson entities
4. **Organisation Mapping JSON** (e.g. `organisations_mapping_2026-03-02`): pre-matched list of organisations (names, uuids) used to populate funder information in Pure

#### DSpace CSV
 
A flat CSV export from DSpace with one row per item. Required columns used by the script:
 
| Column | Purpose |
|---|---|
| `uuid` | DSpace UUID |
| `dc.title` | Primary title |
| `dc.title.subtitle` | Subtitle (also falls back to `dc.title.alternative`) |
| `dc.contributor.author` | Semicolon-separated author names |
| `dc.contributor.editor` | Semicolon-separated editor names |
| `dc.contributor.translator` | Semicolon-separated translator names |
| `dc.contributor.illustrator` | Semicolon-separated illustrator names |
| `dc.contributor.funder` | Semicolon-separated funder names |
| `dc.date.issued` | Publication date (multiple formats supported) |
| `dc.date.embargo` | Embargo end date |
| `dc.description.abstract` | Abstract text |
| `dc.description.sponsorship` | Free-text funding acknowledgement |
| `dc.identifier.doi` | Publisher DOI |
| `dc.identifier.uri` | Semicolon-separated URIs (Handles and repository DOIs) |
| `dc.language.iso` | ISO 639-3 language code (e.g., `eng`) |
| `dc.rights` | Rights/licence type|
| `dc.type` | Item type (mapped to Pure subtype) |
| `journal_uuid` | Pure journal UUID (for contributions to journals & contributions to periodicals) |
 
#### Pure JSON
 
A list of Pure research output objects as returned by the Pure API. Key fields used for matching and updating: `uuid`, `typeDiscriminator`, `type.uri`, `title.value`, `subTitle.value`, `contributors`, `electronicVersions`, `links`, `publicationStatuses`, `fundingDetails`, `fundingText`, `keywordGroups`, `language`, `abstract`, `journalAssociation`, `managingOrganization`, `organizations`.
 
#### Person Mapping JSON
 
A list of person objects, each with:
- `firstName`, `lastName`: canonical name
- `alternativeFirstName`, `alternativeLastName`: lists of name variations
- `internal` (bool): if a person is internal 
- `internalUUIDs`: a list of Pure UUIDs for internal persons, including their visibility (`FREE`, `CAMPUS`, `BACKEND`, `CONFIDENTIAL`)
- `internalDuplicates` (bool): whether a person has internal duplicates in Pure
- `external` (bool): if a person is external
- `externalUUIDs`: a list of Pure UUIDs for external persons
- `externalDuplicates` (bool): whether a person has external duplicates in Pure
- `primaryInternalOrganization`: preferred organisation UUID for internal persons
- `internalOrganizations`: a list of internal organisation UUIDs associated with an internal person
- `externalOrganizations`: a list of external organisation UUIDs associated with any person
- `orcid`: person's ORCID number (if available)
- `scopusId`: person's Scopus ID (if available)
- `papers`: a list of known papers (`doi`, `handle`, `title`) used for person disambiguation
- `paperCount`: the number of papers associated with this person
- `dspaceMerge` (bool): if the record is a result of the merge of 2+ DSpace authors
- `sourceAuthorIds`: a list of DSpace author IDs that were merged *(these IDs are assigned by the DSpace author merging script and aren't present in DSpace)*

**NB!** A person can have both internal & external UUIDs and both internal & external duplicates. This reflects the duplicates that exist in Pure. 
 
#### Organisation Mapping JSON
 
A list of organisation objects, each with:
- `name`: list of known name strings for the organisation
- `uuid`: Pure organisation UUID
- `internal` / `external`: boolean flags
- `visibility`: visibility key (`FREE`, `CAMPUS`, `BACKEND`, `CONFIDENTIAL`)
 
### Output Files
 
All outputs are written to a date-stamped directory (`OUTPUT_DIR`).
 
| Path | Content |
|---|---|
| `matched/<type_key>_<date>.json` | Updated Pure records (matched to existing) |
| `unmatched/<type_key>_<date>.json` | New Pure records (not matched) |
| `matched_records_before_updates_<date>.json` | Snapshot of Pure records before any updates were applied |
| `logs/processing_log_<date>.log` | Full stdout log (console output mirrored to file) |
| `logs/status_log_<date>.json` | Per-record structured log (match type, UUID, errors, duplicates) |
| `logs/error_log_<date>.log` | Full Python tracebacks for records that raised exceptions |
| `unmatched_contributors_<date>.csv` | Contributors not found in the person mapping |
| `unmatched_funders_<date>.csv` | Funders not found in the organisation mapping |
| `no_author_records_<date>.csv` | Records skipped because no contributor fields were populated |
 
Records in the matched and unmatched directories are partitioned by Pure type key (e.g., `contributiontojournal`, `contributiontobookanthology`). Each file is a JSON array written by `append_record_to_file`, which deduplicates on `uuid` — re-running the script replaces existing records with the same UUID rather than duplicating them.

#### Directory Layout

```
test_output_<date>/
├── matched/
│   ├── contributiontojournal_<date>.json
│   ├── contributiontoconference_<date>.json
│   └── ...
├── unmatched/
│   ├── contributiontojournal_<date>.json
│   ├── contributiontoconference_<date>.json
│   └── ...
├── logs/
    ├── status_log_<date>.json
    ├── error_log_<date>.log
    └── processing_log_<date>.log
├── matched_records_before_updates_<date>.json
├── unmatched_contributors_<date>.csv
├── unmatched_funders_<date>.csv
└── no_author_records_<date>.csv
```


## Record Matching

Matching is attempted in a strict priority cascade. The script stops at the first strategy that returns at least one result.

### Matching logic
 
#### Strategy 1: Publisher DOI
 
The `dc.identifier.doi` field is normalised to a canonical `https://doi.org/10.xxxx` form and looked up against an index of Pure records keyed by their their publisher DOIs. Publisher DOIs are indexed from two sources in Pure records: the `electronicVersions` array and the `links` array (excluding repository DOIs in the `10.13025` namespace).
 
#### Strategy 2: Repository DOI
 
DOIs in the `10.13025` namespace are extracted from `dc.identifier.uri` (which may contain multiple semicolon-separated values) and looked up against a separate index of Pure records keyed by their repository DOIs. Repository DOIs are indexed from two sources in Pure records: the `electronicVersions` array and the `links` array.
 
#### Strategy 3: Handle
 
All `hdl.handle.net` URLs are extracted from `dc.identifier.uri` and looked up against an index built from two sources in Pure records: the `links` array (Handle entries) and electronic versions whose DOIs resolve to handle URLs.
 
#### Strategy 4: Title Similarity
 
Applied only when strategies 1–3 all fail. Two sub-strategies are tried in order:
 
**4a. Exact title match:** The normalised DSpace title (and the combined title+subtitle variant) is looked up directly in a pre-built index of normalised Pure titles.
 
**4b. Fuzzy title match using a token index:** If the exact lookup fails, a token-based inverted index (`title_token_index`) is used to retrieve a small candidate set before any fuzzy scoring takes place. The query title is tokenised (words longer than 3 characters, stop words excluded), and only Pure records that share at least one token are considered. The top 200 candidates by shared token count are then scored using `rapidfuzz.fuzz.token_set_ratio` across three comparison variants:
- DSpace title vs Pure title
- DSpace title+subtitle combined vs Pure title
- DSpace title vs Pure title+subtitle combined
- DSpace title+subtitle combined vs Pure title+subtitle combined
 
The best score across the three variants must meet the `TITLE_SIMILARITY_THRESHOLD` (default 90%) to be accepted. A short-circuit length check (>50% relative length difference) skips clearly mismatched pairs before fuzzy comparison. If the query title contains no indexable tokens (e.g. it consists entirely of stop words or very short words), the candidate set is empty and no fuzzy match is attempted.
 
#### Pre-built Lookup Indices
 
All four strategies use pre-built in-memory data structures constructed once before the main processing loop:
- Strategies 1–3 use `defaultdict(list)` dictionaries keyed by normalised identifiers — all lookups are O(1).
- Strategy 4a uses a normalised title dictionary — also O(1).
- Strategy 4b uses `title_token_index`, an inverted index mapping significant title words to sets of Pure record positions. Candidate retrieval is O(q × k) where q is the number of query tokens and k is the average posting list length, followed by fuzzy scoring over at most 200 candidates — effectively constant time in practice compared to the previous O(n) full scan.

### Contributor Processing
 
#### Name Parsing
 
Contributor names from DSpace are semicolon-separated and parsed individually. Each name is parsed into first/last components:
- `"Last, First"` format: split on the first comma.
- `"First Last"` format: last token is the surname, all preceding tokens are the first name.
 
#### Role Resolution
 
DSpace contributor fields map to Pure roles as follows:
 
| DSpace field | Pure role URI fragment |
|---|---|
| `dc.contributor.author` | `/author` |
| `dc.contributor.editor` | `/editor` |
| `dc.contributor.translator` | `/translator` |
| `dc.contributor.illustrator` | `/illustrator` |
 
Role URIs are constructed dynamically from the record's Pure type key (e.g., `/dk/atira/pure/researchoutput/roles/contributiontojournal/author`).
 
#### Author/Editor Overlap Resolution
 
If the same name appears in both `dc.contributor.author` and `dc.contributor.editor`, the script resolves the conflict based on `dc.type`:
 
- **Book-like types** (`book`, `interactive resource`, `conference proceedings`): the name is kept as **editor** and removed from the author list.
- **All other types**: the name is kept as **author** and removed from the editor list.
 
#### Missing Author Correction
 
If `dc.type` is a non-book type but `dc.contributor.editor` is populated and `dc.contributor.author` is empty, the editors are treated as authors. This corrects a common metadata error in DSpace exports where book chapter authors were entered in the editor field.
 
#### No-contributor Gatekeeping
 
Records with no content in any of the four contributor fields (`author`, `editor`, `translator`, `illustrator`) are skipped entirely before matching is attempted. They are logged to the status log with error `"No contributors found in any contributor field"`.
 
For new record creation (unmatched path), if contributor matching produces zero successfully resolved contributors, the record is also skipped (logged as `"No matched contributors"`).
 
#### Organisation Validation
 
After building the contributor list, all internal organisation UUIDs are batch-validated against the Pure API. For any UUID that returns a non-200 response from the internal organisations endpoint, the behaviour depends on `COLLECT_EXTERNAL_ORGS`:

- `COLLECT_EXTERNAL_ORGS = False`: the UUID is omitted entirely from the contributor and a warning is written to the processing log.
- `COLLECT_EXTERNAL_ORGS = True`: the Pure external organisations endpoint is checked for the same UUID. If found, the UUID is attached to the contributor as an `externalOrganization` and the type change is recorded in the processing log. If not found in external organisations either, the UUID is omitted entirely and a warning is written to the processing log.

This prevents write failures caused by stale or incorrect UUIDs in the person mapping while avoiding silent data loss.
 
### Funder Processing
 
#### Name Matching
 
Funder names from `dc.contributor.funder` (semicolon-separated) are normalised by lowercasing and replacing punctuation with spaces. The normalised name is looked up in a pre-built index of organisation names from the organisation mapping.
 
#### Unmatched Funders
 
If a funder name cannot be matched, it is added to the `_unmatched_funders` global list and written to `unmatched_funders_<date>.csv` at the end of processing.
 
If no `dc.description.sponsorship` text is available and there are unmatched funders, their names are concatenated and written to the Pure `fundingText` field as a fallback. This preserves the funding information in a human-readable form even without a matched UUID.
 
### Electronic Versions & DOI Handling

#### Electronic Version Types

- **Publisher Version** (`dc.identifier.doi`):
  - `versionType: "publishersversion"`
  - `accessType: "UNKNOWN"` (default)
  
- **Repository Version** (`dc.identifier.uri` with DOI `10.13025/*`):
  - `versionType: "authorsversion"`
  - `accessType: "OPEN_ACCESS"` or `"EMBARGOED"` (if embargo present)
  - `licenseType`: mapped from `dc.rights` (e.g., "CC BY-NC-ND")
 
#### DOI Normalisation
 
All DOIs throughout the script are normalised to the canonical form `https://doi.org/10.xxxx` by the `normalize_doi` function, which strips any existing prefix (`https://doi.org/`, `http://doi.org/`, `doi:`, or no prefix) and replaces it with `https://doi.org/`. This normalised form is used consistently for both matching and storage — DOIs are written to `electronicVersions` in the full `https://doi.org/10.xxxx` form.
 
#### Embargo Handling
 
Embargo status is computed by comparing the parsed embargo end date against today's date (`TODAY`). If active, the electronic version's `accessType` is set to `EMBARGOED` and an `embargoPeriod.endDate` is attached. Expired embargos result in `OPEN` access type.
 
#### Repository vs Publisher DOIs
 
The script distinguishes DOIs by prefix:
- **Repository DOIs** (`10.13025/...`): mapped to `authorsVersion`, with `CC BY-NC-ND` licence. These always appear first in the `electronicVersions` array.
- **Publisher DOIs** (all other `10.` prefixes): mapped to `publishersVersion`. These appear after repository versions.
 
#### Handle Links
 
Handle links are written to the Pure `links` array with `alias: "Handle"`. All DOI links that may exist in the Pure `links` array are removed unconditionally — DOIs belong in `electronicVersions`, not links. All other non-handle, non-DOI links are preserved as-is. The handle selection logic depends on what is available in DSpace and Pure for a particular record:

**DSpace handles present:**
1. Each DSpace handle is compared against existing Pure handles (normalised before comparison).
   - If exactly one DSpace handle matches a Pure handle, that handle is used as the canonical handle.
   - If multiple DSpace handles match Pure handles, the first matching one is used and a warning is written to the processing log.
   - If no DSpace handle matches any Pure handle, the first DSpace handle is used. If Pure had existing handles, this is noted in the processing log.
2. On the Pure side, if multiple existing Pure handles match DSpace handles, all matching Pure handles are preserved and the record is flagged in the processing log for manual review.

**No DSpace handles present:** all existing Pure handles are preserved unchanged and the record is flagged in the processing log for manual review, since the absence of a DSpace handle may indicate a metadata gap requiring human judgement.
 
## Deduplication Strategies
 
Both record-level and person-level duplicates are handled explicitly.
 
### Record Deduplication (`resolve_record_duplicate`)
 
When a matching strategy returns more than one Pure record, the best candidate is selected by scoring each record across four criteria, applied in descending priority:
 
| Priority | Criterion | Score |
|---|---|---|
| 1 | Visibility (`FREE` or `CAMPUS`) | 1 point |
| 2 | Number of filled non-system fields | 0.5 per field |
| 3 | Modified by a real user (not `root`, `atira`, `sync_user`, `admin`, `system`) | 2 points |
| 4 | Number of internal contributors | 1 per contributor |
 
The record with the highest combined score is selected. The `duplicates: true` flag is set in the status log for any record where this resolution was invoked.
 
### Person Deduplication (`resolve_author_duplicate`)
 
When a contributor name matches more than one person in the mapping, the best candidate is selected using a four-level scoring key, evaluated in strict priority order:
 
**Level 1: Paper evidence (highest priority)**
 
The person's pre-indexed set of known papers (`_paper_dois`, `_paper_handles`, `_paper_titles`) is compared against the current DSpace record's identifiers:
 
| Match type | Score |
|---|---|
| DOI match | 2 |
| Handle match | 2 |
| Title match | 1 |
| No match | 0 |
 
A confirmed paper evidence match always wins before any other criterion is considered, regardless of whether the candidate is internal or external.
 
**Level 2: Person type**
 
Internal Pure persons (staff) are preferred over external persons, who are preferred over unclassified entries.
 
| Type | Score |
|---|---|
| Internal | 2 |
| External | 1 |
| Other | 0 |
 
**Level 3: Visibility**
 
For internal persons, the visibility of their UUID record is used:
 
| Visibility | Score |
|---|---|
| `FREE` or `CAMPUS` | 1 |
| `BACKEND` or `CONFIDENTIAL` | 0 |
 
**Level 4: Metadata richness**
 
If an API key is available, the number of populated fields on the person record is fetched from the Pure API and used as a tiebreaker. Results are cached to avoid repeated API calls for the same UUID.
 
### Organisation Deduplication (`resolve_funder_duplicate`)
 
When a funder name matches more than one organisation, the best candidate is selected by:
 
1. Internal organisations are preferred over external (type score: 2 vs 1).
2. Within each type, visibility `FREE` > `CAMPUS` > other.
 
### Contributor Deduplication Within a Record
 
When updating an existing Pure record (non-override mode), a contributor from DSpace is not added again if they already appear in the Pure record. Existing contributors are identified by both UUID and name (including all name variations from the `names` array). The matching uses a set of normalised `(first, last)` tuples covering all combinations of name variants in both normal and reversed order.
 
## Type Mapping
 
Used for **unmatched records** to create them on Pure.

| DSpace `dc.type` | Pure Subtype URI |
|------------------|---------------|
| journal article | `/dk/atira/pure/researchoutput/researchoutputtypes/contributiontojournal/article` |
| review article | `/dk/atira/pure/researchoutput/researchoutputtypes/contributiontojournal/systematicreview` |
| review | `/dk/atira/pure/researchoutput/researchoutputtypes/contributiontojournal/systematicreview` |
| doctoral thesis | `/dk/atira/pure/researchoutput/researchoutputtypes/thesis/doc` |
| master thesis | `/dk/atira/pure/researchoutput/researchoutputtypes/thesis/master` |
| conference paper | `/dk/atira/pure/researchoutput/researchoutputtypes/contributiontoconference/paper` |
| conference output | `/dk/atira/pure/researchoutput/researchoutputtypes/contributiontoconference/other` |
| conference poster | `/dk/atira/pure/researchoutput/researchoutputtypes/contributiontoconference/poster` |
| book part | `/dk/atira/pure/researchoutput/researchoutputtypes/contributiontobookanthology/chapter` |
| book | `/dk/atira/pure/researchoutput/researchoutputtypes/bookanthology/book` |
| report | `/dk/atira/pure/researchoutput/researchoutputtypes/bookanthology/commissioned` |
| conference proceedings | `/dk/atira/pure/researchoutput/researchoutputtypes/bookanthology/book` |
| working paper | `/dk/atira/pure/researchoutput/researchoutputtypes/workingpaper/workingpaper` |
| video | `/dk/atira/pure/researchoutput/researchoutputtypes/nontextual/audiovisual_material` |
| interactive resource | `/dk/atira/pure/researchoutput/researchoutputtypes/nontextual/web_publication` |
| newspaper article | `/dk/atira/pure/researchoutput/researchoutputtypes/contributiontoperiodical/article` |
| book review | `/dk/atira/pure/researchoutput/researchoutputtypes/contributiontoperiodical/book` |
| other | `/dk/atira/pure/researchoutput/researchoutputtypes/othercontribution/other` |
| data management plan | `/dk/atira/pure/researchoutput/researchoutputtypes/othercontribution/other` |
 
### Type Downgrade
 
If a record maps to `ContributionToJournal` or `ContributionToPeriodical` but no `journal_uuid` is present in the DSpace row and no journal association already exists in Pure, the type is downgraded to `OtherContribution` with `peerReview: false`. This prevents API write failures caused by journal contributions lacking a journal reference.
 
### Peer Review Defaults
 
Types that require a `peerReview` field receive a default value based on type:
- `ContributionToJournal`, `ContributionToBookAnthology`, `BookAnthology`: `true`
- All other applicable types: `false`
- `WorkingPaper`, `ContributionToPeriodical`, `Thesis`, `Memorandum`: field omitted entirely


## Metadata Update Rules

Precedence rules govern which system's data takes priority for each field. All rules below apply in **standard mode** (`OVERRIDE_MODE = False`). In **override mode**, DSpace data is always written unconditionally (see [Modes of Operation](#modes-of-operation)).
 
### Field-Level Precedence

| DSpace Field | Pure Target Field | OpenAIRE | Rule | Notes |
|--------------|-------------------|----------|------|-------|
| `dc.contributor.author` | `contributors[]` | M | **Append** (no duplicates) | Map to Person/ExternalPerson by UUID/ORCID/email/name; create External Persons if no match; preserve existing authors and order of the authors |
| `dc.contributor.editor` | `contributors[]` |  | **Append** (no duplicates) | Map to Person/ExternalPerson by UUID/ORCID/email/name; create External Persons if no match; preserve existing authors and order of the authors |
| `dc.contributor.funder` | `fundingDetails.organizations[]` | MA | **Fill if blank** | Prefer Pure when already authority-linked.  Create External Organisation if no match  |
| `dc.date.embargo` | `electronicVersions[].embargoPeriod.endDate` | MA | **Overwrite** (repo version only) | Only for DOIs starting with `10.13025`; repository is authoritative for OA timing. |
| `dc.date.issued` | `publicationStatuses[].publicationDate` | M | **Fill if blank, upgrade only** | Flag year conflicts; don't overwrite existing year |
| `dc.description.abstract` | `abstract.en_GB` | MA |  **Fill if blank** | Do not overwrite Pure curated abstracts.|
| `dc.description.sponsorship` | `fundingText.en_GB` | MA | **Fill if blank** | Prefer Pure's funding info where present.|
| `dc.identifier.doi` | `electronicVersions[]` | M | **Add if missing** | Create new electronic version (publisher version) if missing. Never overwrite a different DOI without review. |
| `dc.identifier.uri` (DOI) | `electronicVersions[]` | M | **Always add** | Add repository DOI by creating a new electronic version as author's accepted manuscript (open access); set repository DOI first in CRIS display.  |
| `dc.identifier.uri` (Handle) | `links[].url` | R | **Always add** | Add as link, never as an electronic version |
| `dc.language.iso` | `language.uri` | MA | **Fill if blank** | Map ISO 639 codes to Pure codes |
| `dc.publisher` | `managingOrganization` | MA | **Fill if blank** | Prefer authority-linked value in Pure. Create Publisher if no match, link to Journal entity. |
| `dc.rights` | `electronicVersions[].licenseType` | M | **Overwrite** (repo version only) | Only for DOIs starting with `10.13025`; OA licence is repository-authoritative |
| `dc.title` | `title.value` | **Fill if blank** | M | Preserve existing Pure titles; check for overlaps with Pure subtitle to avoid duplication within display title |

### Author Keyword Group
 
When contributors are successfully resolved and added, the `keywordGroups` entry with `logicalName: /dk/atira/pure/authors` (a legacy plain-text author list) is removed from the record. This group is only present in older Pure records as a fallback before proper person linking was established.
 
### Title Subtitle Stripping
 
Before writing a title to Pure, the script checks whether the subtitle is embedded at the end of the title string (e.g., `"Main Title: Subtitle"`). If so, the subtitle portion and any preceding colon are stripped from the title field to avoid duplication. Matching is punctuation-insensitive and case-insensitive.

### External Organisation Filtering
 
A hardcoded list (`EXTERNAL_ORGS_TO_IGNORE`) contains UUIDs for all known variants of "University of Galway" / "NUI Galway" in the Pure external organisation database. These are silently excluded from contributor-level `externalOrganizations` and record-level `externalOrganizations`. This filtering is applied even if an ignored UUID is the only available organisation. This filter only makes a difference if `COLLECT_EXTERNAL_ORGS` is set to `True`; if `False`, no external organisations are collected.


## Logging & Diagnostics
 
### 1. Status Log (`status_log_<date>.json`)
 
One entry per DSpace record. 

Tracks each DSpace record's matching status:

```json
  {
    "handle": "http://hdl.handle.net/10379/6474",
    "uuid": "fdf47230-6997-400e-8252-e0e92be337ee",
    "pureType": "/dk/atira/pure/researchoutput/researchoutputtypes/contributiontojournal/article",
    "matched": true,
    "duplicates": false,
    "success": true,
    "error": null,
    "matches": [
      {
        "pureUUID": "fdf47230-6997-400e-8252-e0e92be337ee",
        "title": "Defining the heathen Irish and the pagan African: two similar discourses a century apart",
        "matchType": "Publisher DOI"
      }
    ],
    "matchType": "Publisher DOI"
  }

```

| Field | Description |
|---|---|
| `handle` | Handle extracted from `dc.identifier.uri` |
| `uuid` | UUID of the matched Pure record (null if unmatched) |
| `pureType` | Pure type URI of the matched/created record |
| `matched` | `true` if matched to an existing Pure record |
| `duplicates` | `true` if more than one Pure record matched |
| `matchType` | Which strategy produced the match (e.g., `"Handle"`, `"Title Similarity (92.3%)"`) |
| `success` | `true` if the record was processed without errors |
| `error` | Error message if `success` is `false` |
| `matches` | Array of all Pure records that matched, with UUID, title, and match type |
 
### 2. Console / Processing Log (`processing_log_YYYY-MM-DD.log`)

Console output mirrored to `logs/processing_log_<date>.log`, including:
- Data loading progress
- Author matching details
- Record processing status
- Summary statistics
 
Per-record output uses emoji prefixes for fast visual scanning:
- `✅` — success
- `⚠️` — warning / degraded (skipped, fallback used)
- `❌` — error
- `➤` — processing step
- `ℹ️` — informational

#### 3. Error Log (`error_log_YYYY-MM-DD.log`)

Full Python tracebacks for any errors encountered during processing.
 
## Caching & Performance
 
| Cache | Scope | Purpose |
|---|---|---|
| `_person_metadata_cache` | Internal persons | Stores field counts from the Pure persons API to avoid repeat calls |
| `_external_person_metadata_cache` | External persons | Same as above for external-persons endpoint |
| `_org_validation_cache` | Organisations | Stores boolean validity results from both the Pure internal organisations API and the external organisations API (keyed with an external:: prefix to avoid collision) |
 
All three caches are module-level dicts that persist across all records in a single run. Organisation UUIDs are batch-validated per record (collecting all unique UUIDs, then validating in one pass) to reduce API round trips.
 
Lookup indices (`person_index`, `org_index`, `pure_by_doi`, `pure_by_handle`, `pure_by_title`, `title_token_index`) are all built once before the main loop. Identifier-based matching (strategies 1–3) and exact title matching (strategy 4a) are O(1) dictionary lookups. Fuzzy title matching (strategy 4b) uses `title_token_index` to reduce the candidate pool to at most 200 records before scoring, avoiding the O(n) full scan of the previous approach.

 
## Known Limitations & Edge Cases
 
- **Date parsing and `dayfirst` convention:** Date parsing is handled by `python-dateutil`, which supports ISO 8601 and most common formats robustly. For ambiguous formats (e.g. `01/02/03`), the `dayfirst` parameter controls interpretation — it defaults to `True` (European order). If you need to parse US month-first dates, set `dayfirst=False` in the `parse_date` calls. Strings that cannot be parsed at all fall back to a year-extraction regex, and ultimately to `(1970, 1, 1)` if no four-digit year is found.
- **Irish-language abstracts:** Records with `dc.language.iso: gle` have their abstract written to both the `en_IE` and `ga` keys as a workaround for a display limitation. This means the abstract will appear under both languages in Pure.
- **Re-run behaviour for output files:** Output JSON files are deduplicated on `uuid` before writing. If a record with the same `uuid` already exists in the output file from a previous run, it is replaced rather than appended, making re-runs idempotent for matched records. New (unmatched) records created from DSpace have no `uuid` yet, so they are keyed under `None` and will overwrite each other if the script is re-run on the same unmatched set — consider clearing the unmatched output directory between runs if this is a concern.
