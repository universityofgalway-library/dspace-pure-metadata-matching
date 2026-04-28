# DSpace-Pure Author Matching

Matches DSpace authors against Pure internal and external person records and outputs enriched JSON with UUIDs, organisation affiliations, alternative names, and identifiers.

---

## Requirements

```bash
pip install regex tqdm
```

Python ≥ 3.10.

---

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

---

## Configuration

Edit these paths at the top of the script:

```python
DSpace_Authors_JSON     = "./author_matching/dspace_authors.json"
Pure_Internal_JSON      = "./pure_entities/pure_persons.json"
Pure_External_JSON      = "./pure_entities/pure_external-persons.json"
IRISH_SURNAMES_JSON     = "./author_matching/irish_surnames.json"
OUTPUT_DIR              = f"./author_matching/{TODAY}"
```

`OUTPUT_DIR` is the default output directory (`./author_matching/<YYYY-MM-DD>`). It can be overridden at runtime with `--output-dir`.

---

## CLI Options

| Flag | Default | Description |
|---|---|---|
| `--output-dir PATH` | `./author_matching/<TODAY>` | Override default output directory. |
| `--prefix STRING` | `authors` | Prefix for output filenames. |
| `--option OPTION` | *(all eight)* | Generate only one named output file instead of all eight. See Output Options table. |
| `--generate-initial-variants` | `False` | Generate spacing/punctuation variants from existing initials (e.g. `J.` → `J`, `J P`, etc.) and use them for matching. Applied to both the Pure person index and DSpace author lookup. |
| `--generate-initials-from-names` | `False` | Generate initials from full names (e.g. `John` → `J`, `J.`) for recording in `alternativeFirstName` **only** — never used for matching. Applied to both the Pure person index and DSpace author lookup. |

---

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

Papers without a `handle` value are silently dropped during processing. If the same handle appears more than once for an author, only the first occurrence is kept. Authors with a missing or blank `firstName` or `lastName` are filtered out and counted in the summary statistics.

### 2. Pure Internal Persons JSON

Person records for current university staff. May include ORCID identifiers, Scopus Author IDs, visibility settings, and organisation associations.

### 3. Pure External Persons JSON

Person records for external collaborators or past staff. ORCID and Scopus IDs are **not** available for external persons, and they cannot have internal organisation associations.

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

If this file is missing, a warning is logged and Irish surname normalisation is skipped entirely.

---

## Processing Steps

### Step 1: Load Irish Surname Index

Builds a lookup dict from the Irish surnames JSON. Every variant (canonical + alternatives) is indexed under its normalised lowercase form, all pointing to the same entry:

```python
# Input
{"canonical": "O'Brien", "alternatives": ["O Brien", "O' Brien"]}

# Index entries created (normalized lowercase keys)
{
  "o'brien": {"canonical": "O'Brien", "alternatives": ["O Brien", "O' Brien"]},
  "o brien": {"canonical": "O'Brien", "alternatives": ["O Brien", "O' Brien"]},
  "o' brien": {"canonical": "O'Brien", "alternatives": ["O Brien", "O' Brien"]}
}
```

Curly apostrophes (`'`, `'`) are normalized to straight apostrophes (`'`) before any index lookup or comparison.

### Step 2: Index Pure Persons

`build_index_persons` processes both internal and external person lists. The `--generate-initial-variants` and `--generate-initials-from-names` flags control variant generation at this stage as well as during DSpace author matching. For each person it:

1. Extracts the primary name and any additional names from the `names` array
2. Looks up each surname in the Irish surnames index to get all normalised variants
3. Generates normalised first-name variants for matching (controlled by CLI flags)
4. Creates index keys of the form `(normalized_first, normalized_last)`
5. Indexes **both** direct order `(first, last)` and swapped order `(last, first)` to handle inverted names

For **internal persons**, the indexer also extracts:
- `visibility` (e.g. `"FREE"`, `"CAMPUS"`)
- ORCID (from `person["orcid"]`)
- Scopus Author ID (from `person["identifiers"]`, matched by URI `/dk/atira/pure/person/personsources/scopusauthor`)
- Internal organisation UUIDs from staff, honorary staff, visiting scholar, and student association arrays
- External organisation UUIDs from `externalPositions`
- Primary internal organisation (the association flagged `primaryAssociation: true`)

For **external persons**, the indexer extracts:
- External organisation UUIDs from `externalOrganizations`, `externalPositions`, and `contributorAssociations`
- No ORCID or Scopus IDs are collected

**Example index entries:**

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
# Alternative name "S." — only matched if --generate-initial-variants is set
("s.", "o'brien") → [uuid_123]   # only with --generate-initial-variants
("s",  "o'brien") → [uuid_123]   # only with --generate-initial-variants
```

### Step 3: Match DSpace Authors

`enrich_authors` processes each DSpace author in turn.

#### 3.1 Normalise the name

First names are title-cased (each word capitalised) and then hyphen-capitalised (letter after `-` or `–` uppercased). Last names are hyphen-capitalised only — their original casing is otherwise preserved at this stage. Curly apostrophes are normalised to straight apostrophes.

#### 3.2 Look up the surname in the Irish surnames index

```python
DSpace: "O Brien"
↓
normalize("O Brien") = "o brien" → found in Irish surnames index
↓
variants:   ["o'brien", "o brien", "o' brien"]
canonical:  "O'Brien"
alternatives: ["O Brien", "O' Brien"]
```

If no match is found, the original surname is used as-is.

#### 3.3 Generate first-name variants

`get_firstname_variants` returns two separate lists:

| List | Index | Purpose | Used for matching? |
|------|-------|----------|--------------------|
| `all_variants` | `[0]` | Recorded in `alternativeFirstName` output field | No |
| `matching_variants` | `[1]` | Added to the lookup key set | Yes |

Behaviour depends on whether the name is initials (contains `.` or is ≤ 2 non-punctuation characters) or a full name:

- **Already initials** (e.g. `"S."`, `"J.P."`): if `--generate-initial-variants` is set, spacing/punctuation variants are generated and added to **both** lists. Without the flag, no variants are generated.
- **Full name** (e.g. `"Sarah"`): if `--generate-initials-from-names` is set, single-letter initials are generated for `all_variants` **only** — they are never added to `matching_variants` and are never used for matching.

```python
# Full name, default flags
get_firstname_variants("Sarah")
→ all_variants=[], matching_variants=[]

# Full name, --generate-initials-from-names set
get_firstname_variants("Sarah", generate_initials_from_names=True)
→ all_variants=["S", "S."], matching_variants=[]   # recorded only, never matched

# Initials, --generate-initial-variants set
get_firstname_variants("S.", generate_initial_variants=True)
→ all_variants=["S", ...], matching_variants=["S", "S.", ...]
```

#### 3.4 Search the Pure index

```python
for surname_variant in ["o'brien", "o brien", "o' brien"]:
    for norm_first_variant in {norm_first} | matching_variants:
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
- Alternative first and last names (last names canonicalised via the Irish surnames index)
- Internal and external organisation UUIDs
- Visibility per UUID
- Primary internal organisation UUID (first found across matched UUIDs)
- ORCID and Scopus ID

From **external matches**:
- Alternative first and last names
- External organisation UUIDs

ORCID and Scopus IDs are collected across all internal match UUIDs into a set. If an author has internal duplicates, they may yield multiple distinct values — in that case a warning is logged for both ORCID and Scopus ID separately, and the first value found is used. These identifiers are never collected from external matches.

#### 3.6 Build the enriched record

The `alternativeFirstName` field is populated from:
1. First-name variants from `get_firstname_variants` (`all_variants[0]`)
2. Alternative first names found on any matched Pure person

The `alternativeLastName` field is populated from:
1. Surname alternatives from the Irish surnames index entry
2. Alternative last names found on any matched Pure person (canonicalised)

The `lastName` in the output is always the **canonical form** from the Irish surnames index, or the original if no match was found.

---

## Output Options

Run with `--option` to produce a single filtered file. Without `--option`, all eight files are generated.

| Option | Description | Use Case |
|--------|-------------|----------|
| `all` | All authors | Full dataset |
| `matched_internal` | At least one Pure internal match | Current/past staff and students |
| `matched_external` | At least one Pure external match | External collaborators |
| `unmatched` | No Pure match of any kind | Potential new profiles |
| `matched_all_duplicates` | Multiple internal **or** external matches | Cleanup needed |
| `matched_internal_duplicates` | Multiple internal matches | Internal deduplication |
| `matched_external_duplicates` | Multiple external matches | External deduplication |
| `matched_internal_external_duplicates` | Matched in **both** internal and external lists | Investigate crossover |

---

## Output Format

One JSON file per output option, named `<prefix>_<option>_<TODAY>.json`:

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
  "primaryInternalOrganization": "org-uuid-1",
  "alternativeFirstName": ["S", "S."],
  "alternativeLastName": ["O Brien", "O' Brien"]
}
```

**Field notes:**
- `orcid` and `scopusId` are always present (empty string `""` if not found); populated from internal matches only
- `internalUUIDs` is a list of `{"uuid": "...", "visibility": "..."}` objects; `externalUUIDs` is a plain list of UUID strings
- `internalDuplicates: true` means more than one internal Pure person matched this author; all matching UUIDs are listed

---

## Key Behaviours

### Irish Surname Handling

The canonical form is always used in the output `lastName`, regardless of source spelling:

```
DSpace:  "O Brien"   → output lastName: "O'Brien"
DSpace:  "O' Brien"  → output lastName: "O'Brien"
Pure:    "Mac Aodha" → alternativeLastName includes: "MacAodha"
```

All spelling variants are still used during matching — only the output is normalised.

### First-Name Matching Logic

By default, full names and initials only match if they appear verbatim (or as normalised variants) in the Pure data. Full names do **not** match their own derived initials; initials do **not** expand to match full names:

```
DSpace: "John"  + Pure: "J."   → No match (default)
DSpace: "J."    + Pure: "John" → No match (default)
DSpace: "J."    + Pure: "J."   → Match ✓
DSpace: "J."    + Pure: "J"    → Match ✓ (with --generate-initial-variants only)
```

### Name Order Flexibility

Both direct and swapped name orders are indexed, so inverted names in either system are still matched:

```
Pure: firstName="O'Brien", lastName="Sarah"
→ indexed as: ("sarah", "o'brien") and ("o'brien", "sarah")
→ matches DSpace: firstName="Sarah", lastName="O'Brien" ✓
```

### Duplicate Warnings

If two matched internal UUIDs carry different ORCIDs or different Scopus IDs, a warning is logged for each:

```
⚠️ WARNING: Multiple distinct ORCIDs for author 'Sarah O'Brien': {'0000-...1', '0000-...2'}
⚠️ WARNING: Multiple distinct Scopus IDs for author 'Sarah O'Brien': {'12345', '67890'}
```

---

## Statistics

After each output option is processed, the script prints and logs a summary:

```
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

A full log is also written to `./author_matching/match_authors_<TODAY>.log`.

---

## Complete Example

### Input

**DSpace Author:**
```json
{"firstName": "S.", "lastName": "O Brien", "papers": [{"handle": "10379/12345", "doi": "", "title": "The Best Paper"}]}
```

**Pure Internal Person 1:**
```json
{
  "uuid": "abc-123",
  "name": {"firstName": "S.", "lastName": "O'Brien"},
  "orcid": "0000-0001-2345-6789",
  "visibility": {"key": "FREE"},
  "names": [{"name": {"firstName": "Sarah", "lastName": "O'Brien"}}],
  "staffOrganizationAssociations": [
    {"organization": {"uuid": "org-1", "systemName": "Organization"}, "primaryAssociation": true}
  ]
}
```

**Pure Internal Person 2:**
```json
{
  "uuid": "def-456",
  "name": {"firstName": "O Brien", "lastName": "S."},
  "visibility": {"key": "CAMPUS"}
}
```

**Irish Surnames:**
```json
[{"canonical": "O'Brien", "alternatives": ["O Brien", "O' Brien"]}]
```

### Processing (default flags)

1. **Normalise name:** `"S."` — already capitalised, no hyphens; `"O Brien"` — hyphen-capitalised (no hyphens, no change)

2. **Look up `"O Brien"`** in Irish surnames index → canonical: `"O'Brien"`, variants: `["o'brien", "o brien", "o' brien"]`

3. **Generate first-name variants** for `"S."` (default flags):
   - `"S."` contains `.` → treated as initials; no variants generated without `--generate-initial-variants`
   - Lookup uses only `{"s."}` as the first-name key set

4. **Build index entries for Person 1** (`abc-123`, primary name `firstName: "S."`, `lastName: "O'Brien"`; alternative `firstName: "Sarah"`, `lastName: "O'Brien"`):
```python
   ("s.", "o'brien")  → [abc-123]
   ("s.", "o brien")  → [abc-123]
   ("s.", "o' brien") → [abc-123]
   ("sarah", "o'brien")  → [abc-123]
   # ... and swapped order equivalents
```

5. **Build index entries for Person 2** (`def-456`, primary name `firstName: "O Brien"`, `lastName: "S."`):
   - `"O Brien"` looked up in Irish surnames index → canonical `"O'Brien"`, variants: `["o'brien", "o brien", "o' brien"]`
   - `"S."` is the last name here, treated as a plain string
```python
   ("o brien", "s.")  → [def-456]
   ("o'brien", "s.")  → [def-456]
   ("o' brien", "s.") → [def-456]
   # swapped:
   ("s.", "o brien")  → [def-456]
   ("s.", "o'brien")  → [def-456]
   ("s.", "o' brien") → [def-456]
```

6. **Search Pure index** with `norm_first = "s."`, surname variants `["o'brien", "o brien", "o' brien"]`:
   - `("s.", "o'brien")` → `[abc-123]` + `[def-456]` (from swapped index of Person 2)
   - `("s.", "o brien")` → `[abc-123]` + `[def-456]`
   - `("s.", "o' brien")` → `[abc-123]` + `[def-456]`

7. **Deduplicate:** `[abc-123, def-456]` → 2 internal matches → `internalDuplicates: true`

8. **Collect from Person 1** (`abc-123`): alternative name `("Sarah", "O'Brien")`, org `"org-1"`, primary org `"org-1"`, visibility `"FREE"`, ORCID `"0000-0001-2345-6789"`

9. **Collect from Person 2** (`def-456`): no alternative names, no org, visibility `"CAMPUS"`, no ORCID

10. **Collect ORCID/Scopus** across all internal matches:
    - `abc-123` → ORCID `"0000-0001-2345-6789"`
    - `def-456` → no ORCID
    - `orcid_values = {"0000-0001-2345-6789"}` → single value, no warning

11. **Build alternative names:**
    - `alternativeFirstName`: `["Sarah"]` (from Person 1's alternative name entry)
    - `alternativeLastName`: `["O Brien", "O' Brien"]` (from Irish surnames index alternatives; canonical `"O'Brien"` is already the `lastName`)

---

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
  "primaryInternalOrganization": "org-1",
  "alternativeFirstName": ["Sarah"],
  "alternativeLastName": ["O Brien", "O' Brien"]
}
```