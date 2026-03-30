## Research Output Record Matching Script

Matches and enriches research output records between DSpace (institutional repository) and Pure (research information system).

### Overview

The script performs three main tasks:

1. **Match records** across systems using DOIs, handles, and title similarity
2. **Update existing Pure records** with DSpace metadata (precedence-based or override)
3. **Create new Pure records** for unmatched DSpace items

## Usage

```bash
python match_records.py
```

### Configuration

Edit these variables at the top of the script:

```python
OVERRIDE_MODE = False  # True: replace all fields; False: use precedence rules
DSPACE_CSV = "./dspace_data/export.csv"
PURE_JSON = "./pure_data/research-outputs.json"
PERSON_MAPPING_JSON = "./mappings/persons.json"
ORGANIZATION_MAPPING_JSON = "./mappings/organizations.json"
```

### Matching Strategy

Records are matched in priority order:

1. **Publisher DOI** (from `dc.identifier.doi`)
2. **Repository DOI** (from `dc.identifier.uri`, pattern `10.13025/*`)
3. **Handle** (from `dc.identifier.uri`, pattern `10379/*`)
4. **Title similarity** (90% threshold, exact or fuzzy match)

### Update Rules

**Precedence Mode** (`OVERRIDE_MODE = False`):
- Only fills blank fields in Pure records
- Adds new contributors/funders without removing existing ones
- Preserves manually-entered Pure data

**Override Mode** (`OVERRIDE_MODE = True`):
- Replaces all fields with DSpace values
- Removes existing contributors/funders and uses only DSpace data
- Use with caution—overwrites curator work

### Field Mapping

| DSpace Field | Pure Field | Rule |
|--------------|------------|------|
| `dc.contributor.author` | `contributors` | Add new, preserve existing (precedence mode) |
| `dc.contributor.funder` | `fundingDetails` | Add new funders |
| `dc.date.issued` | `publicationStatuses[0].publicationDate` | Fill if blank |
| `dc.identifier.doi` | `electronicVersions` (publisher version) | Add if missing |
| `dc.identifier.uri` (DOI) | `electronicVersions` (repository version) | Add if missing |
| `dc.identifier.uri` (handle) | `links` | Add as repository handle link |
| `dc.description.abstract` | `abstract` | Fill if blank |
| `dc.title` + `dc.title.subtitle` | `title` + `subTitle` | Fill if blank |
| `dc.language.iso` | `language` | Fill if blank |
| `dc.rights` | Repository DOI license type | Overwrite |
| `dc.date.embargo` | Repository DOI embargo period | Overwrite |

### Person Matching

Authors are matched using a pre-built name index supporting:
- Primary names: `firstName`, `lastName`
- Alternative names: `alternativeFirstName[]`, `alternativeLastName[]`
- Both name orders: "First Last" and "Last, First"

**Duplicate resolution priority:**
1. Internal Person > External Person
2. Visibility: FREE > CAMPUS > BACKEND/CONFIDENTIAL
3. Most complete metadata (field count from API)

### Organization Handling

**Internal organizations:**
- Validated against Pure API
- Invalid UUIDs moved to `externalOrganizations`

**External organizations:**
- University of Galway variants (29 UUIDs) filtered unless only org
- Collected from all contributors for top-level fields

**Managing organization:**
- First internal contributor's primary organization
- Falls back to Library Repository if no internal contributors

### Output Structure

```
./record_matching/test_output_YYYY-MM-DD/
├── matched/
│   ├── contributiontojournal_YYYY-MM-DD.json
│   ├── contributiontoconference_YYYY-MM-DD.json
│   └── ...
├── unmatched/
│   ├── contributiontojournal_YYYY-MM-DD.json
│   └── ...
└── logs/
    ├── processing_log_YYYY-MM-DD.log
    ├── status_log_YYYY-MM-DD.json
    └── error_log_YYYY-MM-DD.log
```

**Matched files:** Updates for existing Pure records (grouped by type)  
**Unmatched files:** New records to create (grouped by type)  
**Status log:** JSON array with match details per DSpace record  
**Error log:** Full stack traces for failures

### Status Log Format

```json
[
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
]
```

### Running the Script

```bash
# Install dependencies
pip install requests python-dotenv tqdm --break-system-packages

# Set API key in .env file
echo "PURE_ROOT_API_KEY=your-api-key-here" > .env

# Run script
python match_records.py
```

### Performance

The script builds lookup indices at startup for O(1) matching:

- **Person index:** ~50,000 name variations → instant lookup
- **Organization index:** ~10,000 org names → instant lookup  
- **Pure record indices:** DOIs, handles, titles → instant matching
- **API caches:** Person/org metadata cached after first fetch

Typical performance: ~1,000 records in 5-10 minutes (with API calls enabled).

## Common Issues

### "No matched contributors"

DSpace authors not found in person mapping. Check:
- Name format matches (prefer "Last, First")
- Alternative names are complete
- Person exists in Pure or external persons

**What happens:**
- Record is **skipped entirely** (not created/updated)
- Goes to status log with `"success": false` and `"error": "No matched contributors"`
- Does **NOT** appear in matched/ or unmatched/ folders
- Does **NOT** go to error_log (this is expected behavior, not a code error)

**Where to find it:**
```json
// In status_log_YYYY-MM-DD.json
{
  "handle": "10379/12345",
  "uuid": null,
  "matched": false,
  "success": false,
  "error": "No matched contributors"
}
```

### Unmatched Contributors

**What happens:**
- Script continues processing (doesn't skip the entire record)
- Unmatched contributor is **silently ignored** (not added to the record)
- Matched contributors ARE added
- Record is created/updated with only the matched contributors
- No error in status log or error_log

**Example scenario:**
```
DSpace has 3 authors: "Smith, John", "Doe, Jane", "Unknown, Person"
- "Smith, John" → matched to Person UUID abc-123
- "Doe, Jane" → matched to External Person UUID def-456
- "Unknown, Person" → no match found

Result:
✅ Record created with 2 contributors (Smith and Doe)
❌ "Unknown, Person" disappears (no trace in output)
```

**Where to find it:**
```
// In processing_log_YYYY-MM-DD.log
  ➤ Processing 3 author(s)
    ➤ Checking match for author: 'Smith, John'
      ✅ Found 1 matches
        ✅ Added new author: John Smith
    ➤ Checking match for author: 'Doe, Jane'
      ✅ Found 1 matches
        ✅ Added new author: Jane Doe
    ➤ Checking match for author: 'Unknown, Person'
        ⚠️ No matches found — adding to unmatched
✅ Added 2 contributors
```

**Important:** The unmatched contributors are tracked in a local variable `unmatched_contributors` but **never written anywhere**. They're lost.

### Unmatched Funders

**What happens:**
- Script continues processing (doesn't skip the record)
- Unmatched funder is **silently ignored** (not added to fundingDetails)
- Matched funders ARE added
- Record is created/updated with only the matched funders
- No error in status log or error_log

**Example scenario:**
```
DSpace has 2 funders: "Science Foundation Ireland", "Mystery Foundation"
- "Science Foundation Ireland" → matched to Organization UUID xyz-789
- "Mystery Foundation" → no match found

Result:
✅ Record created with 1 funder (SFI)
❌ "Mystery Foundation" disappears (no trace in output)
```

**Where to find it:**
```
// In processing_log_YYYY-MM-DD.log
  ➤ Processing 2 funders: ['Science Foundation Ireland', 'Mystery Foundation']
    ➤ Looking up funder: 'Science Foundation Ireland'
      ✅ Found 1 matches
      ✅ Added funder: Science Foundation Ireland (UUID: xyz-789, Internal: True)
    ➤ Looking up funder: 'Mystery Foundation'
      ⚠️ No match found for funder: Mystery Foundation
    ✅ Added 1 new funders to fundingDetails
```

### "Invalid internal org UUID"

Organization doesn't exist in Pure. The script automatically:
- Moves it to `externalOrganizations`
- Logs the UUID for investigation

**What happens:**
- Record is **processed successfully**
- UUID automatically moved to `externalOrganizations`
- Warning printed to console/processing log: `⚠️ Invalid internal org UUID {uuid} - moving to external`
- Record **appears in matched/ or unmatched/** folders (operation succeeds)
- Does **NOT** go to error_log or status log errors

**Where to find it:**
```
// In processing_log_YYYY-MM-DD.log
🔍 Validating organization UUIDs...
    ⚠️ Invalid internal org UUID abc-123-def - moving to external
```

**Result in output JSON:**
```json
{
  "externalOrganizations": [
    {
      "systemName": "ExternalOrganization",
      "uuid": "abc-123-def"
    }
  ]
}
```

### "No journal UUID found"


**"No journal UUID found"**  
Journal contribution missing `journal_uuid` column. The script:
- Changes type to `OtherContribution`
- Logs the issue

**What happens:**
- Record is **processed successfully**
- Type automatically changed from `ContributionToJournal` to `OtherContribution`
- Warning printed: `⚠️ No journal UUID found for ContributionToJournal - changing to OtherContribution`
- Record **appears in unmatched/othercontribution_YYYY-MM-DD.json** (not in contributiontojournal file)
- Does **NOT** go to error_log or status log errors

**Where to find it:**
```
// In processing_log_YYYY-MM-DD.log
⚠️ No journal UUID found for ContributionToJournal - changing to OtherContribution
```

**Result in output JSON:**
```json
{
  "typeDiscriminator": "OtherContribution",
  "type": {
    "uri": "/dk/atira/pure/researchoutput/researchoutputtypes/othercontribution/other"
  },
  "peerReview": false
}
```

### Summary Table

| Issue | Record Created? | In Output Files? | In error_log? | In status_log error? | Where to Find Details |
|-------|----------------|------------------|---------------|---------------------|----------------------|
| **No matched contributors** | ❌ No | ❌ No | ❌ No | ✅ Yes | `status_log` with `"error": "No matched contributors"` |
| **Some contributors unmatched** | ✅ Yes | ❌ No | `processing_log` warning only | Partial contributor list |
| **Invalid org UUID** | ✅ Yes | ✅ Yes | ❌ No | ❌ No | `processing_log` warning + output file shows `externalOrganizations` |
| **No journal UUID** | ✅ Yes | ✅ Yes (as OtherContribution) | ❌ No | ❌ No | `processing_log` warning + output file shows changed type |
| **Some funders unmatched** | ✅ Yes | ❌ No | `processing_log` warning only | Partial funder list |
| **ALL funders unmatched** | ✅ Yes | ❌ No | `processing_log` warnings | Record created without funders |

Only **code exceptions** (Python errors, API failures, malformed data) go to `error_log`.

### API Requirements

- Pure API key with read access to persons, external persons, and organizations
- Network access to Pure staging/production instance
- Rate limits: ~100 requests/hour for uncached lookups