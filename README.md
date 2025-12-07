# Scripts for DSpace-Pure Metadata Integration

# `match_records.py`

This Python script matches and synchronizes research output records between DSpace and Pure, updating existing Pure records with DSpace metadata according to precedence rules and creating new Pure records for unmatched DSpace entries.

## Configuration & Usage

### Input Files

1. **DSpace CSV** (e.g. `dspace_test_100.csv`): research outputs from DSpace
   - Comma-separated columns
   - Semicolon-separated values within columns
2. **Pure JSON** (e.g. `research_outputs_2025-11-20_all.json`): existing Pure research outputs
3. **Person Mapping JSON** (e.g. `test_authors_all.json`): pre-matched author mapping between DSpace names and Pure Person/ExternalPerson entities

### Environment Variables

Create a `.env` file with:

```bash
PURE_API_KEY=your_api_key_here
```

Edit the configuration section at the top of the script:

```python
DSpace_CSV = "./matching_test/dspace_test_100.csv"
Pure_JSON = "./matching_test/research_outputs/research_outputs_2025-11-20_all.json"
Person_Mapping_JSON = "./matching_test/matched_authors/test_authors_all.json"
OUTPUT_DIR = "./matching_test/test_output"
API_KEY = os.getenv("PURE_API_KEY", "")  # Loaded from .env file
```
### Basic Execution

```bash
python match_records.py
```

## Matching Logic

### Research Output Matching

The script matches DSpace records to Pure records using the following identifiers in order of priority:

#### Step 1: DOI Matching
- **Publisher DOI**: extracted from `dc.identifier.doi` (DSpace) > matched against `electronicVersions[].doi` (Pure)
- **Repository DOI**: extracted from `dc.identifier.uri` (DSpace, format: `10.13025/*`) > matched against `electronicVersions[].doi` and `links[].url` (Pure)

#### Step 2: Handle Matching
- Extracted from `dc.identifier.uri` (DSpace, format: `hdl.handle.net/*`)
- Matched against `links[].url` (Pure)

#### Step 3: Title Matching (Fallback)
- Normalized title comparison: `dc.title` (DSpace) > `title.value` (Pure)
- Case-insensitive, stripped from whitespaces

#### Step 4: Duplicate Resolution
- Prefer `visibility: "FREE"` or `"CAMPUS"`
- Prefer the record with most non-empty metadata fields, excluding the system fields
- Prefer real users (as opposed to `root`, `atira`, `sync_user`, `admin`, `system`)

#### Step 5: Update or Create record
- If matched, update the record according to the precedence rules
- If unmatched, create a new records according to DSpace-Pure type mapping

### Author Matching

For each author in `dc.contributor.author` (semicolon-separated), the script performs the following steps.

#### Step 1: Name Parsing
Handles two formats:
- **"Last, First"**: e.g., "Doe, John"
- **"First Last"**: e.g., "John Doe"

#### Step 2: Person Lookup
Searches the Person Mapping JSON for:
- **Primary name match**: firstName + lastName
- **Swapped name match**: handles reversed name orders
- **Alternative name match**: checks `alternativeFirstName` and `alternativeLastName` arrays

#### Step 3: Duplicate Resolution
If multiple matches are found, the following priority order is used.
1. **Internal over External**: Person (internal) preferred over ExternalPerson
2. **Visibility (Internal duplicates)**: prefer `visibility: "FREE"` or `"CAMPUS"`
3. **Metadata Completeness (External duplicates)**:
   - Fetches full external person records via API
   - Selects person with most non-empty metadata fields, excluding the system fields
4. **First Match**: if still tied, use first remaining option

#### Step 4: Duplicate Prevention
Before adding an author to a Pure record:
- Checks if author already exists by:
  - **Name match**: `(firstName, lastName)` tuple
  - **UUID match**: `person.uuid` or `externalPerson.uuid`
- Skips addition if duplicate found (preserves existing Pure data)

If no Person or ExternalPerson match is found, author names added to `keywordGroups` with `logicalName: "/dk/atira/pure/authors"`

## Metadata Update Rules

For **matched records**, the script updates Pure metadata according to these precedence rules:

| DSpace Field | Pure Target Field | Rule | Notes |
|--------------|-------------------|------|-------|
| `dc.contributor.author` | `contributors[]` | **Append** (no duplicates) | Map to Person/ExternalPerson; preserve existing authors and order of the authors |
| `dc.contributor.funder` | `fundingDetails.organizations[]` | **Fill if blank** | Prefer Pure when already authority-linked. Not implemented (requires funder matching)  |
| `dc.date.embargo` | `electronicVersions[].embargoPeriod.endDate` | **Overwrite** (repo version only) | Only for DOIs starting with `10.13025`; repository is authoritative for OA timing. |
| `dc.date.issued` | `publicationStatuses[].publicationDate` | **Fill if blank, upgrade only** | Flag year conflicts; don't overwrite existing year |
| `dc.description.abstract` | `abstract.en_GB` | **Fill if blank** | Do not overwrite Pure curated abstracts.|
| `dc.description.sponsorship` | `fundingText.en_GB` | **Fill if blank** | Prefer Pure's funding info where present.|
| `dc.identifier.doi` | `electronicVersions[]` | **Add if missing** | Create new electronic version (publisher version) if missing. Never overwrite a different DOI without review. |
| `dc.identifier.uri` (DOI) | `electronicVersions[]` | **Always add** | Create new electronic version as author's accepted manuscript (open access) |
| `dc.identifier.uri` (Handle) | `links[].url` | **Always add** | Add as link, never as an electronic version |
| `dc.language.iso` | `language.uri` | **Fill if blank** | Map ISO 639 codes to Pure codes |
| `dc.publisher` | `managingOrganization` | **Fill if blank** | Prefer authority-linked value in Pure. Not implemented (requires publisher matching) |
| `dc.rights` | `electronicVersions[].licenseType` | **Overwrite** (repo version only) | Only for DOIs starting with `10.13025`; OA licence is repository-authoritative |
| `dc.title` | `title.value` | **Fill if blank** | Preserve existing Pure titles |


## DSpace to Pure Type Mapping

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

### Electronic Version Types

- **Publisher Version** (`dc.identifier.doi`):
  - `versionType: "publishersversion"`
  - `accessType: "UNKNOWN"` (default)
  
- **Repository Version** (`dc.identifier.uri` with DOI `10.13025/*`):
  - `versionType: "authorsversion"`
  - `accessType: "OPEN_ACCESS"` or `"EMBARGOED"` (if embargo present)
  - `licenseType`: mapped from `dc.rights` (e.g., "CC BY-NC-ND")

## Output Structure

### Directory Layout

```
test_output/
├── matched/
│   ├── contributiontojournal_2025-12-06.json
│   ├── contributiontoconference_2025-12-06.json
│   ├── thesis_2025-12-06.json
│   └── ...
├── unmatched/
│   ├── contributiontojournal_2025-12-06.json
│   ├── contributiontoconference_2025-12-06.json
│   └── ...
└── logs/
    ├── status_log_2025-12-06.json
    ├── status_log_2025-12-06.csv
    ├── error_log_2025-12-06.log
    └── processing_log_2025-12-06.log
```

### File Organization

**Research Output JSONs** are organized by Pure type (extracted from `type.uri`):

- `contributiontojournal` - Journal articles, reviews
- `contributiontoconference` - Conference papers, posters
- `thesis` - Doctoral/master theses
- `bookanthology` - Books, reports, proceedings
- `workingpaper` - Working papers
- `nontextual` - AV materials, interactive resources
- `contributiontoperiodical` - Newspaper articles, book reviews
- `othercontribution` - Other

### Log Files

#### 1. Status Log (`status_log_YYYY-MM-DD.json` and `.csv`)

Tracks each DSpace record's matching status:

```json
{
  "handle": "http://hdl.handle.net/10379/1422",
  "uuid": "7f3a5b8c-9d2e-4f1a-a6c7-3e9d5b8a2f1c",
  "pure_type": "/dk/atira/pure/researchoutput/researchoutputtypes/contributiontoconference/paper",
  "matched": true,
  "duplicates": false,
  "success": true,
  "error": null
}
```

**Fields:**
- `handle`: DSpace handle URL
- `uuid`: Pure record UUID (empty if unmatched)
- `pure_type`: Full Pure subtype URI
- `matched`: `true` if record found in Pure, `false` if new
- `duplicates`: `true` if multiple Pure matches were found
- `success`: `true` if processing succeeded
- `error`: error message if `success: false`

#### 2. Error Log (`error_log_YYYY-MM-DD.log`)

Full Python tracebacks for any errors encountered during processing.

#### 3. Processing Log (`processing_log_YYYY-MM-DD.log`)

Console output captured to file, including:
- Data loading progress
- Author matching details
- Record processing status
- Summary statistics



