# DSpace CSV Author Extraction Script

## Overview

This script extracts author names and their associated publications from DSpace CSV exports. It processes multiple contributor fields and outputs structured JSON data suitable for author matching and analysis.

## How It Works

### 1. Configuration
Three configurable lists at the top of the script:

```python
EXCLUDED_DC_TYPES = [
    "doctoral thesis",
    "master thesis"
]

CONTRIBUTOR_FIELDS = [
    "dc.contributor.author",
    "dc.contributor.advisor", 
    "dc.contributor.editor",
    "dc.contributor.illustrator",
    "dc.contributor.translator"
]

NAME_STOPWORDS = [
    "university",
    "college",
    "academy",
    "institute",
    "association"
]
```

### 2. Main Processing Flow

```
CSV Input → Filter by DC type → Extract contributors → Fix name issues → 
Filter stopwords → Merge duplicates → JSON Output
```

### 3. Name Processing Pipeline

For each contributor name, the script:

1. **Parses** the name from "Last, First" format
2. **Fixes misplaced prefixes in Irish surnames**
3. **Normalizes apostrophes** (curvy → straight)
4. **Filters** out stopwords and numbers
5. **Validates** name structure
6. **Normalizes** for deduplication
7. **Capitalizes** for output

## Core Functions

### `parse_names(name_field)`

**Purpose**: Parse semicolon-separated name strings into (first, last) tuples.

**Input format**: `"Last, First; Last2, First2"` or `"First Last; First2 Last2"`

**Example**:
```python
Input:  "O'Brien, Sarah; Smith-Jones, John"
Output: [("Sarah", "O'Brien"), ("John", "Smith-Jones")]
```

**Processing**:
1. Splits on semicolons
2. Handles comma-separated format (Last, First)
3. Handles space-separated format (First Last)
4. Replaces curvy apostrophes with straight ones
5. Calls `fix_misplaced_prefix()` to correct Irish surnames, where the prefix is incorrectly recorded as a part of the first name

### `fix_misplaced_prefix(first, last)`

**Purpose**: Fix cases where Irish/Scottish surname prefixes appear in the first name field.

**Common problem in CSV exports**:
```
CSV:     "Sarah Mc", "Garrigle"
Fixed:   "Sarah", "McGarrigle"

CSV:     "John O'", "Brien"  
Fixed:   "John", "O'Brien"

CSV:     "Mary Mac", "Donald"
Fixed:   "Mary", "Mac Donald"
```

**Prefixes handled**:
- **No space after**: Mc, O'
- **Space after**: Mac, Ó, Ní, Nic, Mhic, De, Mac Giolla, Mac Con, Uí, Mac an, Nic an, Ua

**Algorithm**:
1. Check if last word(s) of first name match a known prefix
2. Extract actual first name (everything before prefix)
3. Reconstruct last name with prefix properly attached
4. Apply appropriate spacing rule for that prefix

### `contains_stopword(first, last, stopwords)`

**Purpose**: Filter out institutional and test names.

**Filters**:
1. **Any digits** anywhere in the name (regex: `\d`)
2. **Stopwords** (case-insensitive substring match)

**Examples**:
```python
"John Smith"         → Keep
"John Smith123"      → Filter (digit)
"Author1 Test"       → Filter (digit)
"John University"    → Filter (stopword)
"College Office"     → Filter (stopword)
```

### `valid_author_name(first, last, strict=True)`

**Purpose**: Apply structural validation rules when `--strict-names` is enabled.

**Rules** (when strict=True):
1. Both names must be at least 2 characters
2. First names with dots must be ≥9 characters (filters short initials)
3. First names with spaces must be ≥7 characters
4. Neither name can be empty

**Examples** (strict mode):
```python
"J.", "Smith"           → Reject (too short with dot)
"J. P.", "Smith"        → Keep (9 chars)
"John", "S"             → Reject (last name too short)
"Mary Jane", "O'Brien"  → Keep
```

### `normalize_name_key(first, last)`

**Purpose**: Create consistent keys for deduplication.

**Transformations**:
1. Lowercase
2. Strip whitespace
3. Collapse multiple spaces to single space

**Example**:
```python
Input:  "  SARAH  Jane ", "O'BRIEN  "
Output: ("sarah jane", "o'brien")
```

This allows:
- "Sarah Jane O'Brien"
- "SARAH JANE O'BRIEN"  
- "sarah jane o'brien"

...to be treated as the same author.

### `normalize_full_name(first, last)`

**Purpose**: Clean and capitalize names for final JSON output.

**Transformations**:
1. Replace curvy apostrophes with straight ones
2. Strip whitespace
3. Apply title case capitalization

**Example**:
```python
Input:  "sarah jane", "o'brien"
Output: ("Sarah Jane", "O'Brien")
```


## Usage

### Basic Usage:
```bash
python extract_authors.py input.csv output.json
```

### With Options:

**Strict name filtering**:
```bash
python extract_authors.py input.csv output.json --strict-names
```

**Disable name normalization** (keep exact name variations separate):
```bash
python extract_authors.py input.csv output.json --no-normalization
```

**Custom DC type exclusions**:
```bash
python extract_authors.py input.csv output.json --exclude-types "conference paper" "book review"
```

**No DC type exclusions**:
```bash
python extract_authors.py input.csv output.json --no-exclusions
```

**Custom stopwords**:
```bash
python extract_authors.py input.csv output.json --stopwords "corporation" "foundation" "council"
```

**Disable stopword filtering**:
```bash
python extract_authors.py input.csv output.json --no-stopword-filter
```

**Combine multiple options**:
```bash
python extract_authors.py input.csv output.json \
  --strict-names \
  --exclude-types "thesis" "dissertation" \
  --stopwords "test" "example"
```

## Output Summary

After processing, the script prints a summary:

```
=== Extraction Summary ===
Items excluded (by DC type): 150
Items processed: 1250
Authors filtered (stopwords): 23
Authors found: 487
JSON written to: output.json

Excluded DC types: doctoral thesis, master thesis
Name stopwords: university, college, academy, institute, association
```

## Special Handling

### 1. DOI Processing

**Repository DOIs excluded**:
```python
Input:  "10.13025/repository/12345"
Output: ""  # Empty - this is the repository's own DOI

Input:  "10.1234/journal.article"
Output: "https://doi.org/10.1234/journal.article"
```

**DOI normalization**:
```python
Input:  "10.1234/example"
Output: "https://doi.org/10.1234/example"

Input:  "doi:10.1234/example"
Output: "https://doi.org/10.1234/example"
```

### 2. Contributor Field Processing

Extracts from multiple fields in this order:
1. dc.contributor.author
2. dc.contributor.advisor
3. dc.contributor.editor
4. dc.contributor.illustrator
5. dc.contributor.translator

**Deduplicates** within each item so if someone appears in multiple roles, they're only counted once per paper.


## Performance Notes

- Processing ~10,000 rows takes approximately 5-10 seconds
- Memory usage is proportional to the number of unique authors
- Deduplication happens in-memory using dictionary keys
- Output JSON is sorted alphabetically by last name, then first name

## Error Handling

The script handles:
- **Missing fields**: Treats as empty strings
- **Malformed names**: Skips if validation fails
- **Empty rows**: Ignores
- **Missing handles**: Skips the entire item
- **Encoding issues**: Uses UTF-8 throughout

## Output Structure

Each author object contains:
```json
{
  "firstName": "String (title case)",
  "lastName": "String (title case)", 
  "papers": [
    {
      "handle": "String (Handle URL)",
      "doi": "String (full DOI URL or empty)",
      "title": "String (original formatting)"
    }
  ]
}
```

## Workflow Example

### Input CSV:
```csv
dc.contributor.author,dc.contributor.advisor,dc.identifier.uri,dc.identifier.doi,dc.title,dc.type
"O'Brien, Sarah; Mc Crae, John",,"http://hdl.handle.net/10379/12345","10.1234/example","Sample Article","article"
"Smith, Author1",,"http://hdl.handle.net/10379/12346",,"Test Document","article"
"Johnson, Mary; Brown, College",,"http://hdl.handle.net/10379/12347",,"Research Paper","article"
"Jones, Robert","Lee, Susan","http://hdl.handle.net/10379/12348",,"PhD Study","doctoral thesis"
```

### Processing Steps:

**Row 1**: "O'Brien, Sarah; Mc Crae, John"
1. ✅ Not excluded (type: "article")
2. Parse: `[("Sarah", "O'Brien"), ("John", "Mc Crae")]`
3. Fix prefix: `("John", "Mc Crae")` → `("John", "McCrae")`
4. ✅ No stopwords
5. ✅ Valid names
6. Normalize for deduplication: `("sarah", "o'brien")`, `("john", "mccrae")`
7. Capitalize for output: `("Sarah", "O'Brien")`, `("John", "Mccrae")`
8. Associate with handle: `10379/12345`

**Row 2**: "Smith, Author1"
1. ✅ Not excluded (type: "article")
2. Parse: `[("Author1", "Smith")]`
3. ❌ **Contains digit** → FILTERED OUT

**Row 3**: "Johnson, Mary; Brown, College"
1. ✅ Not excluded (type: "article")
2. Parse: `[("Mary", "Johnson"), ("College", "Brown")]`
3. "Mary Johnson": ✅ Pass all filters
4. "College Brown": ❌ **Contains stopword "college"** → FILTERED OUT
5. Only "Mary Johnson" is kept

**Row 4**: "Jones, Robert" + advisor "Lee, Susan"
1. ❌ **Excluded** (type: "doctoral thesis")
2. **Not processed**

### Output JSON:

```json
[
  {
    "firstName": "Sarah",
    "lastName": "O'Brien",
    "papers": [
      {
        "handle": "http://hdl.handle.net/10379/12345",
        "doi": "https://doi.org/10.1234/example",
        "title": "Sample Article"
      }
    ]
  },
  {
    "firstName": "John",
    "lastName": "Mccrae",
    "papers": [
      {
        "handle": "http://hdl.handle.net/10379/12345",
        "doi": "https://doi.org/10.1234/example",
        "title": "Sample Article"
      }
    ]
  },
  {
    "firstName": "Mary",
    "lastName": "Johnson",
    "papers": [
      {
        "handle": "http://hdl.handle.net/10379/12347",
        "doi": "",
        "title": "Research Paper"
      }
    ]
  }
]
```