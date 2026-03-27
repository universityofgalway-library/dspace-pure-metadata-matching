# DSpace-Pure Author Matching

## Input Files

### 1. DSpace Authors JSON
```json
[
  {
    "firstName": "Sarah",
    "lastName": "O Brien",
    "papers": [
      {
        "handle": "http://hdl.handle.net/10379/12345",
        "doi": "https://doi.org/10.1234/example",
        "title": "Research Article"
      }
    ]
  }
]
```

### 2. Pure Internal Persons JSON
Person records for current university staff. These records may include ORCID identifiers, Scopus Author IDs, visibility settings, and organisation associations.

### 3. Pure External Persons JSON
Person records for external collaborators or past university staff. ORCID and Scopus IDs are **not** available for external persons; they cannot have internal organisation associations.

### 4. Irish Surnames JSON
```json
[
  {
    "canonical": "O'Brien",
    "alternatives": ["O Brien", "O' Brien"]
  },
  {
    "canonical": "Mac Aodha",
    "alternatives": ["MacAodha"]
  }
]
```

## Processing Steps

### Step 1: Load Irish Surname Index

The script builds a lookup index from the Irish surnames JSON:

```python
# Input
{"canonical": "O'Brien", "alternatives": ["O Brien", "O' Brien"]}

# Creates index (normalized lowercase keys)
{
  "o'brien": {"canonical": "O'Brien", "alternatives": ["O Brien", "O' Brien"]},
  "o brien": {"canonical": "O'Brien", "alternatives": ["O Brien", "O' Brien"]},
  "o' brien": {"canonical": "O'Brien", "alternatives": ["O Brien", "O' Brien"]}
}
```

Every variant (canonical or alternative) maps to the same entry, which holds the canonical form and its alternatives. If the Irish surnames file is missing, a warning is logged and Irish surname normalization is skipped entirely.

### Step 2: Index Pure Persons

`build_index_persons` processes both internal and external person lists. For each person it:

1. Extracts the primary name and any additional names from the `names` array
2. Looks up each surname in the Irish surnames index to get all normalized variants
3. Generates normalized first-name variants for matching (controlled by CLI flags — see [Usage](#usage))
4. Creates index keys of the form `(normalized_first, normalized_last)`
5. Indexes **both** direct order `(first, last)` and swapped order `(last, first)` to handle inverted names

For **internal persons**, the indexer also extracts:
- `visibility` (e.g. `"FREE"`, `"CAMPUS"`)
- ORCID identifier (from `person["orcid"]`)
- Scopus Author ID (from `person["identifiers"]`, matched by URI `/dk/atira/pure/person/personsources/scopusauthor`)
- Internal organisation UUIDs from staff, honorary staff, visiting scholar, and student association arrays
- External organisation UUIDs from `externalPositions`
- Primary internal organisation (the association flagged `primaryAssociation: true`)

For **external persons**, the indexer extracts:
- External organisation UUIDs from `externalOrganizations`, `externalPositions`, and `contributorAssociations`
- No ORCID or Scopus IDs are collected for external persons

**Example index entries for a Pure person**:
```python
# Pure person: primary name "Sarah O'Brien", alternative "S. O Brien"
# Irish surname index maps "o brien" → canonical "O'Brien"

("sarah", "o'brien") → [uuid_123]
("sarah", "o brien") → [uuid_123]
("sarah", "o' brien") → [uuid_123]
# Swapped order
("o'brien", "sarah") → [uuid_123]
("o brien", "sarah") → [uuid_123]
("o' brien", "sarah") → [uuid_123]
# Alternative name "S. O Brien" (initials only matched if --generate-initial-variants is set)
("s.", "o'brien") → [uuid_123]   # only with --generate-initial-variants
("s", "o'brien")  → [uuid_123]   # only with --generate-initial-variants
```

### Step 3: Match DSpace Authors

For each DSpace author `enrich_authors` performs the following:

#### 3.1 Normalize the name

First names are title-cased and hyphen-capitalized. Last names are hyphen-capitalized. Curly apostrophes (`'`, `'`) are normalized to straight apostrophes (`'`) before any comparison.

#### 3.2 Look up the surname in the Irish surnames index

```python
DSpace: "O Brien"
↓
Look up normalize("O Brien") = "o brien" in Irish surnames index
↓
Found! → variants: ["o'brien", "o brien", "o' brien"]
         canonical: "O'Brien"
         alternatives: ["O Brien", "O' Brien"]
```

If no match is found, the original surname is used as-is with a single normalized variant.

#### 3.3 Generate first-name variants

`get_firstname_variants` returns two separate lists:

| List | Purpose | Used for matching? |
|------|----------|--------------------|
| `all_variants` (index `[0]`) | Recorded in `alternativeFirstName` output field | No |
| `matching_variants` (index `[1]`) | Added to the lookup key set | Yes |

The logic depends on whether the name appears to be initials (contains `.` or is ≤ 2 non-punctuation characters) or a full name:

- **Already initials** (e.g. `"S."`, `"J.P."`): if `--generate-initial-variants` is set, spacing/punctuation variants are generated and added to **both** lists. Without the flag, no variants are generated.
- **Full name** (e.g. `"Sarah"`): if `--generate-initials-from-names` is set, single-letter initials are generated for `all_variants` **only** — they are **never** added to `matching_variants` and are **never** used for matching.

```python
# Full name, default flags (no variants generated for matching)
get_firstname_variants("Sarah")
→ all_variants=[], matching_variants=[]

# Full name, --generate-initials-from-names set
get_firstname_variants("Sarah", generate_initials_from_names=True)
→ all_variants=["S", "S."], matching_variants=[]   # recorded only, not matched

# Initials, --generate-initial-variants set
get_firstname_variants("S.", generate_initial_variants=True)
→ all_variants=["S", "S", "S.", ...], matching_variants=["S", "S.", ...]
```

#### 3.4 Search the Pure index

```python
For each surname_variant in ["o'brien", "o brien", "o' brien"]:
    For each norm_first_variant in {norm_first} | matching_variants:
        # Direct order
        internal_matches += index.get((norm_first_variant, surname_variant), [])
        external_matches += index.get((norm_first_variant, surname_variant), [])
        # Swapped order
        internal_matches += index.get((surname_variant, norm_first_variant), [])
        external_matches += index.get((surname_variant, norm_first_variant), [])
```

Results are deduplicated after the full scan.

#### 3.5 Collect metadata from matches

From **internal matches**:
- Alternative first and last names (canonicalized via the Irish surnames index)
- Internal and external organisation UUIDs
- Visibility per UUID
- Primary internal organisation UUID (first found across matched UUIDs)
- ORCID and Scopus ID (collected only for internal match UUIDs, as this data is unavailable in Pure for external persons in Pure)

From **external matches**:
- Alternative first and last names
- Internal and external organisation UUIDs (external persons can have both)


#### 3.6 Build the enriched record

The `alternativeFirstName` field is populated from:
1. First-name variants generated by `get_firstname_variants` (`all_variants[0]`)
2. Alternative first names found on any matched Pure person

The `alternativeLastName` field is populated from:
1. Surname alternatives from the Irish surnames index entry
2. Alternative last names found on any matched Pure person (canonicalized)

The `lastName` field in the output is always set to the **canonical form** from the Irish surnames index, or the original if no match was found.

### Step 4: Generate Output

Creates one JSON file per output option containing enriched author records:

```json
{
  "firstName": "Sarah",
  "lastName": "O'Brien",
  "orcid": "0000-0001-2345-6789",
  "scopusId": "12345678900",
  "papers": [
    {"handle": "http://hdl.handle.net/10379/12345", "doi": "...", "title": "..."}
  ],
  "paperCount": 3,
  "internal": true,
  "external": false,
  "internalDuplicates": true,
  "externalDuplicates": false,
  "internalUUIDs": [
    {"uuid": "abc-123", "visibility": "FREE"},
    {"uuid": "def-456", "visibility": "CAMPUS"}
  ],
  "externalUUIDs": [],
  "internalOrganizations": ["org-uuid-1", "org-uuid-2"],
  "externalOrganizations": [],
  "primaryInternalOrganisation": "org-uuid-1",
  "alternativeFirstName": ["S", "S."],
  "alternativeLastName": ["O Brien", "O' Brien"]
}
```

> **Note**: `orcid` and `scopusId` are always present in the output (empty string `""` if not found). They are only populated from internal person matches.

Paper deduplication is applied: if the same `handle` appears more than once for an author, only the first occurrence is kept.

## Key Behaviours

### Irish Surname Handling

The canonical form from the Irish surnames index is always used in the output `lastName` field, regardless of the spelling in the source data:

```
DSpace input: "O Brien"   → Output lastName: "O'Brien"
DSpace input: "O' Brien"  → Output lastName: "O'Brien"
DSpace input: "O'Brien"   → Output lastName: "O'Brien"
Pure person:  "Mac Aodha" → alternativeLastName includes: "MacAodha"
```

All spelling variants are still used during matching — only the output is normalised to canonical form.

### First-Name Matching Logic

By default (no flags), full names and initials only match if they appear verbatim in the Pure data (or as normalized variants). Full names do **not** match their own derived initials, and initials do **not** expand to match full names:

```
DSpace: "John"  + Pure: "J."     → No match (default)
DSpace: "J."    + Pure: "John"   → No match (default)
DSpace: "J."    + Pure: "J."     → Match ✓
DSpace: "J."    + Pure: "J"      → Match ✓ (with --generate-initial-variants)
DSpace: "J."    + Pure: "J. P."  → No match
```

Initials may be recorded as `alternativeFirstName` values (via `--generate-initials-from-names`) to support downstream merging workflows, but they are never used for matching.

### Name Order Flexibility

The index stores every name in both direct and swapped order, so authors whose names are inverted in either system are still matched:

```
Pure: firstName="O'Brien", lastName="Sarah"
→ Indexed as: ("sarah", "o'brien") and ("o'brien", "sarah")
→ Matches DSpace: firstName="Sarah", lastName="O'Brien" ✓
```

### Duplicate Detection

`internalDuplicates: true` means more than one internal Pure person matched the same DSpace author name. Similarly for `externalDuplicates`. If an author is matched in both internal and external lists, both `internal` and `external` will be `true`. All matching UUIDs are listed; the `primaryInternalOrganisation` is taken from the first internal match that has one.

Duplicate warnings are also logged:
- If two matched internal UUIDs have different ORCIDs, a warning is emitted
- If two matched internal UUIDs have different Scopus IDs, a warning is emitted

## Usage

```bash
# Strict matching (default — no generated variants)
python match_authors.py

# Generate spacing/punctuation variants from existing initials and use for matching
python match_authors.py --generate-initial-variants

# Generate initials from full names for recording in alternativeFirstName only
python match_authors.py --generate-initials-from-names

# Combine both options
python match_authors.py --generate-initial-variants --generate-initials-from-names

# Generate only one output file instead of all eight
python match_authors.py --option matched_internal

# Override output directory and filename prefix
python match_authors.py --output-dir /path/to/output --prefix test
```

## Output Options

Run with `--option` to filter results. If `--option` is not specified, all eight files are generated.

| Option | Description | Use Case |
|--------|-------------|----------|
| `all` | All authors | Full dataset |
| `matched_internal` | Has at least one Pure internal match | Current/past staff and students |
| `matched_external` | Has at least one Pure external match | External collaborators |
| `unmatched` | No Pure match of any kind | Potential new profiles |
| `matched_all_duplicates` | Multiple internal **or** external matches | Cleanup needed |
| `matched_internal_duplicates` | Multiple internal matches | Internal deduplication |
| `matched_external_duplicates` | Multiple external matches | External deduplication |
| `matched_internal_external_duplicates` | Matched in **both** internal and external lists | Investigate crossover |

## Statistics

After each output option is processed, the script reports:

```
--- Processing: All authors ---
Matching Authors: 100%|████████| 1500/1500 [00:05<00:00, 280.45author/s]
✅ Total DSpace authors processed: 1500
✅ Authors filtered out (missing first or last name): 23
✅ Authors remaining after filtering: 1477

=== DATA STATS ===
Total filtered entries: 1477
Total authors matched (internal or external): 892
Unmatched authors: 585
Internal matches: 856
External matches: 78
Matches in both internal & external: 42
Entries with internal duplicates: 67
Entries with external duplicates: 3
Entries with alternative first names: 1201
Entries with alternative last names: 234
Total unique papers across all authors: 3842
```

Results are also written to a dated log file at `./author_matching/match_authors_<TODAY>.log`.

## Complete Example

### Input

**DSpace Author**:
```json
{
  "firstName": "S.",
  "lastName": "O Brien",
  "papers": [{"handle": "10379/12345", "doi": "", "title": "Paper 1"}]
}
```

**Pure Internal Person 1**:
```json
{
  "uuid": "abc-123",
  "name": {"firstName": "Sarah", "lastName": "O'Brien"},
  "orcid": "0000-0001-2345-6789",
  "visibility": {"key": "FREE"},
  "staffOrganizationAssociations": [
    {"organization": {"uuid": "org-1", "systemName": "Organization"}, "primaryAssociation": true}
  ]
}
```

**Pure Internal Person 2**:
```json
{
  "uuid": "def-456",
  "name": {"firstName": "S.", "lastName": "O Brien"},
  "names": [{"name": {"firstName": "Sarah", "lastName": "O'Brien"}}],
  "visibility": {"key": "CAMPUS"}
}
```

**Irish Surnames**:
```json
[{"canonical": "O'Brien", "alternatives": ["O Brien", "O' Brien"]}]
```

### Processing

1. **Normalize first name**: `"S."` — contains `.`, treated as initials
2. **Look up `"O Brien"`** in Irish surnames index → Found! Canonical: `"O'Brien"`, variants: `["o'brien", "o brien", "o' brien"]`
3. **Generate first-name variants** for `"S."` (default flags — no variant generation):
   - matching_variants: `[]` — no variants unless `--generate-initial-variants` is set
   - all_variants: `[]`
   - Lookup uses just `{"s."}` as the first-name key set
4. **Search Pure index** (direct + swapped order):
   - `("s.", "o'brien")` → `[abc-123]`
   - `("s.", "o brien")` → `[def-456]`
   - `("s.", "o' brien")` → no match
   - (swapped order also checked)
5. **Deduplicate**: `[abc-123, def-456]` → **2 internal matches → `internalDuplicates: true`**
6. **Collect from Person 1** (`abc-123`):
   - Alternative name: `("Sarah", "O'Brien")`
   - Organisation: `["org-1"]`, primary org: `"org-1"`
   - Visibility: `"FREE"`
   - ORCID: `"0000-0001-2345-6789"`
7. **Collect from Person 2** (`def-456`):
   - Alternative name: `("Sarah", "O'Brien")` (from `names` array)
   - Organisation: none
   - Visibility: `"CAMPUS"`
   - ORCID: not present
8. **Build alternative names**:
   - `alternativeFirstName`: `["Sarah"]` (from matched persons; `"S."` is the primary name)
   - `alternativeLastName`: `["O Brien", "O' Brien"]` (from Irish surnames index alternatives)

### Output

```json
{
  "firstName": "S.",
  "lastName": "O'Brien",
  "orcid": "0000-0001-2345-6789",
  "scopusId": "",
  "papers": [{"handle": "10379/12345", "doi": "", "title": "The Best Paper"}],
  "paperCount": 1,
  "internal": true,
  "external": false,
  "internalDuplicates": true,
  "externalDuplicates": false,
  "internalUUIDs": [
    {"uuid": "abc-123", "visibility": "FREE"},
    {"uuid": "def-456", "visibility": "CAMPUS"}
  ],
  "externalUUIDs": [],
  "internalOrganizations": ["org-1"],
  "externalOrganizations": [],
  "primaryInternalOrganisation": "org-1",
  "alternativeFirstName": ["Sarah"],
  "alternativeLastName": ["O Brien", "O' Brien"]
}
```
