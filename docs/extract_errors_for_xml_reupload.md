# extract_errors_for_xml_reupload.py

Builds a "reupload" XML file for Pure containing only the research-output
entries that failed to import, so you can fix the underlying issue and
re-submit just the failed records instead of the whole batch.

## What it does

1. Parses the original Pure import XML and indexes every direct child of
   `<publications>` (e.g. `<book>`, `<chapterInBook>`,
   `<contributionToJournal>`, ...) by its `id` attribute.
2. Parses the Pure error-log CSV and extracts the id from each row's log
   text, using the pattern:
   ```
   Importing content with source id '5418807'
   ```
3. Matches the two sets of ids and copies only the matching XML elements
   (unmodified, deep-copied) into a new `<publications>` document with the
   same namespaces as the original.
4. Reports what was matched, and — importantly — lists any error-log id
   that had **no** corresponding entry in the XML, so nothing is silently
   dropped or guessed.

The script never fabricates data: an entry is only ever included in the
output if its id was found in **both** the source XML and the error log.

## Requirements

- Python 3
- [`lxml`](https://lxml.de/) (`pip install lxml`)

## Usage

```bash
python extract_errors_for_xml_reupload.py --xml data.xml --errors errors.csv
```

This writes the result next to the input XML, e.g.:

```
data.xml  →  reupload_data.xml
```

### Arguments

| Argument | Required | Default | Description |
|---|---|---|---|
| `--xml` | Yes | — | Path to the original Pure import XML file. |
| `--errors` | Yes | — | Path to the Pure error-log CSV file. |
| `--output` | No | `<xml_dir>/reupload_<xml_filename>` | Path to write the filtered XML file. |
| `--status-filter` | No | *(none — all rows considered)* | Only consider CSV rows whose `Status` column contains this text (case-insensitive), e.g. `ERROR`. |
| `--column` | No | *(none — all columns searched)* | Only search this column (by exact CSV header name) for the id pattern, instead of scanning every column. |

### Examples

Use the default output path:
```bash
python extract_errors_for_xml_reupload.py --xml temp_pure_import_2026-07-22.xml --errors errors.csv
# writes reupload_temp_pure_import_2026-07-22.xml in the same folder
```

Specify a custom output path:
```bash
python extract_errors_for_xml_reupload.py \
  --xml temp_pure_import_2026-07-22.xml \
  --errors errors.csv \
  --output /path/to/fixed_batch.xml
```

Only consider rows explicitly marked as errors, and only search a specific column:
```bash
python extract_errors_for_xml_reupload.py \
  --xml data.xml \
  --errors errors.csv \
  --status-filter ERROR \
  --column "Title and description"
```

## Output

Console summary (stdout):
```
Error log rows considered:      200
Unique ids found in error log:  200
Entries in source XML:          63
Entries copied to reupload XML: 1
Wrote 1 entries to reupload_data.xml
```

Warnings (stderr) — never hidden, always shown:
- Rows in the error log that matched the status filter but contained no
  recognizable `Importing content with source id '...'` phrase.
- Ids found in the error log that have no matching entry in the source XML
  (listed individually), e.g. because the record was removed from the XML
  since the log was generated, or the id was mistyped.

## Notes on encoding

Pure error-log CSV exports are sometimes not valid UTF-8 (commonly
Windows-1252 / Latin-1 due to characters like curly quotes or accented
names in exception traces). The script automatically tries, in order:
`utf-8-sig` → `utf-8` → `cp1252` → `latin-1`, so it works without manual
conversion in either case.

## Notes on XML matching

- Matching is based solely on the `id` attribute of each direct child of
  `<publications>` — the element's type (`book`, `chapterInBook`, etc.)
  does not need to be known in advance.
- The original element's content, formatting, and namespaces are preserved
  exactly as found in the source file; nothing is rewritten or
  reformatted beyond copying it into the new document.
