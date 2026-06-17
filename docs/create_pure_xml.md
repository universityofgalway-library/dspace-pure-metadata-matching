# create_pure_xml.py

Matches records between a **DSpace CSV export** and a **Pure JSON export**, then produces a **Pure Research Output Import XML** file conforming to the `v1.publication-import.base-uk.pure.atira.dk` schema.

---

## Requirements

- Python 3.10+
- Standard library only (`csv`, `json`, `xml.etree.ElementTree`, `xml.dom.minidom`, `argparse`, `re`)

---

## Usage

```bash
# Minimal — output defaults to pure_import_YYYY-MM-DD.xml
python create_pure_xml.py --csv dspace.csv --json pure.json --environment test

# Explicit output path, PROD environment
python create_pure_xml.py --csv dspace.csv --json pure.json --environment prod --output import.xml

# Filter by who last modified the Pure record and/or when
python create_pure_xml.py --csv dspace.csv --json pure.json --environment test \
    --modified-by "john@example.com" \
    --modified-after "2025-01-01"

# Override the default version used for files that exist only in DSpace
python create_pure_xml.py --csv dspace.csv --json pure.json --environment prod \
    --default-version authorsversion
```

### Arguments

| Argument | Required | Description |
|---|---|---|
| `--csv` | Yes | Path to the DSpace CSV export file |
| `--json` | Yes | Path to the Pure JSON export file |
| `--environment` | Yes | Which DSpace environment to target: `test` or `prod`. Determines the host written into `<storeName>`/`<source>`, and the domain used when rewriting DSpace bitstream links. See [DSpace environments](#dspace-environments) below. |
| `--default-version` | No | `<version>` value to use for files that exist only in DSpace (no Pure `versionType` to draw from). Defaults to `publishersversion`. |
| `--output` | No | Output XML path. Defaults to `pure_import_YYYY-MM-DD.xml` in the working directory |
| `--modified-by` | No | Only include Pure records whose `modifiedBy` field equals this value |
| `--modified-after` | No | Only include Pure records modified strictly after this date (`YYYY-MM-DD`) |

### DSpace environments

| `--environment` | Base URL | Host written to `<storeName>` / `<source>` |
|---|---|---|
| `test` | `https://galway.dspace7-test.openrepository.com/` | `galway.dspace7-test.openrepository.com` |
| `prod` | `https://researchrepository.universityofgalway.ie/` | `researchrepository.universityofgalway.ie` |

Any DSpace bitstream URL taken from the CSV's `pdf_links` column has its domain rewritten onto whichever host is selected — the path and query string are left untouched. URLs that don't come from DSpace (Pure-hosted file URLs, `hdl.handle.net` links) are never rewritten.

---

## Input formats

### DSpace CSV

Standard DSpace metadata export. The script uses the following columns:

| Column | Used for |
|---|---|
| `uuid` | Primary match key (DSpace UUID) |
| `handle` | Fallback match key; also written as `<existingStores><existingStore><storeContentId>` and used to build the `<file id="...">` attribute for DSpace-only files |
| `pdf_links` | Full DSpace bitstream URL for the item's file. Used as `<fileLocation>` (domain rewritten per `--environment`) when that file isn't already represented in the Pure JSON |
| `pdf_handle_paths` | Old-style handle bitstream path (e.g. `/10379/17513/1/name.pdf`), parsed to recover the bitstream sequence number and filename for the DSpace-only `<file>` block |
| `dc.title.subtitle` | Subtitle |
| `dc.description.abstract` | Abstract (if Pure has none) |
| `dc.description.peer-reviewed` | Peer review flag fallback |
| `dc.description.sponsorship` | Funding text |
| `dc.identifier.doi` | DOI fallback if Pure has none |
| `dc.identifier.uri` | Handle URL(s) written to `<urls>` |
| `dc.identifier.issn` | ISSN written into `<journal>` and `<externalIds>` |
| `dc.identifier.isbn` | ISBN written into `<printIsbns>` |
| `dc.language.iso` | Language fallback |
| `dc.publisher` / `publisher_name` | Publisher name |
| `dc.rights` | Licence fallback (e.g. `CC BY-NC-ND`) |
| `dc.type` | Publication type fallback |
| `dc.date.embargo` / `dc.description.embargo` | Embargo start/end dates |
| `dc.title.alternative` | Host publication title for book chapters |
| `journal_title` | Journal title |
| `dc.identifier.journal` | Journal title fallback |

All other columns are loaded but not directly mapped to XML.

### Pure JSON

Array of research output objects as returned by the Pure REST API. The script reads:

- `pureId`, `uuid`, `typeDiscriminator`, `type.uri`
- `title.value`, `abstract`
- `language.uri`
- `peerReview`, `publicationStatuses`
- `workflow.step`, `visibility.key`
- `contributors[]` (name, role, correspondingAuthor)
- `organizations[]`, `managingOrganization`
- `electronicVersions[]` (DOI, file, and link versions)
- `identifiers[]` (to extract the DSpace UUID)
- `links[]` (to extract Handle URLs)
- `modifiedBy`, `modifiedDate` (for filtering and duplicate resolution)

---

## Matching logic

For each Pure JSON record, the script attempts to find its counterpart in the DSpace CSV using two strategies in priority order:

1. **DSpace UUID** — looks for an entry in Pure's `identifiers[]` array where `idSource == "DSpace"`, then matches its `value` against the CSV `uuid` column.
2. **Handle URL** — looks for entries in Pure's `links[]` array where `alias == "Handle"`, then matches the URL against the CSV `handle` column (bare handles like `10379/1234` are automatically prefixed with `http://hdl.handle.net/`).

Pure records with no match in the CSV are skipped and reported in the console output.

### Duplicate resolution

If the same DSpace UUID maps to more than one Pure record, the most recently modified Pure record (by `modifiedDate`) is kept. A warning is printed to stderr for every collision.

---

## Output XML

The output conforms to the Pure Research Output Import schema (`v1.publication-import.base-uk.pure.atira.dk`). Each matched record becomes one publication element.

### ID placement

| Value | XML location |
|---|---|
| Pure `pureId` | `id` attribute on the publication element, e.g. `<contributionToJournal id="19125272" subType="article">` |
| DSpace UUID | `<externalIds><id type="DSpace">…</id></externalIds>` |
| DSpace Handle | `<existingStores><existingStore><storeContentId>…</storeContentId></existingStore></existingStores>` |

### Publication type mapping

The XML element tag and `subType` attribute are resolved from the Pure `type.uri` field. If the URI is not recognised, the script falls back to the DSpace `dc.type` string.

**From Pure `type.uri`:**

| Pure URI (suffix) | XML tag | subType |
|---|---|---|
| `contributiontojournal/article` | `contributionToJournal` | `article` |
| `contributiontojournal/review` | `contributionToJournal` | `review` |
| `contributiontoconference/paper` | `contributionToConference` | `paper` |
| `contributiontoconference/poster` | `contributionToConference` | `poster` |
| `bookanthology/book` | `book` | `book` |
| `bookanthology/commissioned_report` | `book` | `book` |
| `contributiontobookanthology/chapter` | `chapterInBook` | `chapter` |
| `workingpaper/workingpaper` | `workingPaper` | `workingpaper` |
| `thesis/doctoral` | `thesis` | `phd` |
| `thesis/master` | `thesis` | `master` |
| `nontextual/digitalorvisualproducts` | `nonTextual` | `digitalorvisualproducts` |
| `patent/patent` | `patent` | `patent` |
| `memorandum/academicmemorandum` | `memorandum` | `academicmemorandum` |
| `other/other` | `other` | `other` |

**DSpace `dc.type` fallback:**

| dc.type | XML tag | subType |
|---|---|---|
| `journal article` | `contributionToJournal` | `article` |
| `review` / `review article` / `book review` | `contributionToJournal` | `review` |
| `conference paper` | `contributionToConference` | `paper` |
| `conference poster` | `contributionToConference` | `poster` |
| `book` | `book` | `book` |
| `book part` | `chapterInBook` | `chapter` |
| `report` | `book` | `book` |
| `working paper` | `workingPaper` | `workingpaper` |
| `newspaper article` | `contributionToSpecialist` | `article` |
| `video` / `interactive resource` | `nonTextual` | `digitalorvisualproducts` |
| `data management plan` / `other` | `other` | `other` |

If neither source provides a recognised type, the record is written as `<other subType="other">`.

### Electronic versions

All electronic versions present in the Pure JSON are written:

| Pure `typeDiscriminator` | XML element |
|---|---|
| `DoiElectronicVersion` | `<electronicVersionDOI>` |
| `FileElectronicVersion` | `<electronicVersionFile>` (includes `fileLocation` pointing at the Pure file URL) |
| `LinkElectronicVersion` | `<electronicVersionLink>` |

If Pure carries no electronic versions but the DSpace record has a `dc.identifier.doi`, a `<electronicVersionDOI>` element is generated from it.

### DSpace-only files

Pure is always checked first. If the DSpace CSV row references a bitstream (via `pdf_links` / `pdf_handle_paths`) whose filename doesn't match any `fileName` already present among the record's Pure `FileElectronicVersion` entries, an extra `<electronicVersionFile>` is appended so the file isn't silently dropped from the import:

| Field | Source |
|---|---|
| `<file id="...">` | `{handle}:{sequence}:{filename}` — sequence and filename parsed out of `pdf_handle_paths`, handle from the CSV `handle` column |
| `<filename>` | Filename parsed out of `pdf_handle_paths` |
| `<fileLocation>` | The `pdf_links` URL, with its domain rewritten onto the selected `--environment` |
| `<mimetype>` | Guessed from the filename extension (defaults to `application/pdf`) |
| `<filesize>` | Omitted — the CSV doesn't carry a size for these bitstreams |
| `<source>` | The selected `--environment`'s host |
| `<externalRepositoryState>` | Always `STORED` |
| `<version>` | `--default-version` (default `publishersversion`), since there's no Pure `versionType` to draw from |
| `<licence>` / `<publicAccess>` | Same DSpace fallback as the rest of the script: `dc.rights` → licence, `dc.date.embargo` → embargoed, otherwise open |

Filename comparison is case-insensitive and URL-decodes both sides, so e.g. `Paper.PDF` and `paper.pdf` are treated as the same file.

### Existing stores

Every matched record gets an `<existingStores><existingStore>` block, telling Pure the record's content already lives in the DSpace store being targeted:

```xml
<existingStores>
    <existingStore>
        <storeName>researchrepository.universityofgalway.ie</storeName>
        <updateRequired>true</updateRequired>
        <storeContentId>10379/17513</storeContentId>
    </existingStore>
</existingStores>
```

`storeName` is the selected `--environment`'s host, `storeContentId` is the DSpace Handle (CSV `handle` column — not the UUID), and `updateRequired` is always `true`. This block is added whenever the matched DSpace CSV row has a `handle` value, independent of whether any file was found for the record.

### Licence and access

Licence is taken from the Pure `licenseType.uri` (e.g. `cc_by_nc_nd`). If absent, it falls back to the DSpace `dc.rights` string, which is mapped as follows:

| dc.rights | Licence code |
|---|---|
| `CC BY` | `cc_by` |
| `CC BY-NC` | `cc_by_nc` |
| `CC BY-NC-ND` | `cc_by_nc_nd` |
| `CC BY-NC-SA` | `cc_by_nc_sa` |
| `CC BY-ND` | `cc_by_nd` |
| `CC BY-SA` | `cc_by_sa` |
| `CC0` | `cc0` |

Access is taken from the Pure `accessType.uri`. If a `dc.date.embargo` value is present in the DSpace record, access is set to `embargoed` and the embargo start/end dates are written.

### Value mappings

| Pure value | XML value |
|---|---|
| workflow `validated` | `approved` |
| workflow `approved` | `approved` |
| workflow `forApproval` / `pendingApproval` | `forApproval` |
| visibility `FREE` | `Public` |
| visibility `BACKEND` / `CAMPUS` / `RESTRICTED` | `Restricted` |

---

## Console output

The script prints a short summary to stdout on each run:

```
Environment: prod → https://researchrepository.universityofgalway.ie/ (store host: researchrepository.universityofgalway.ie)
Loading DSpace CSV: dspace.csv
  → 64 records by UUID, 64 by Handle loaded.
Loading Pure JSON: pure.json
  → 120 records loaded.
  → 115 records after filtering.
Matching records…
  → 98 matched, 17 unmatched Pure records.
  → 98 records after duplicate resolution.
Building XML…
XML written to: pure_import_2026-06-16.xml
  → 98 publication element(s) exported.
```

Warnings about skipped or duplicate records are written to stderr.

---

## Extending the script

**Adding a new publication type:** add an entry to `PURE_TYPE_MAP` (keyed on the full Pure type URI) and/or `DSPACE_TYPE_MAP` (keyed on the lowercased `dc.type` string), both mapping to `(xml_tag, subType)`. If the new type needs type-specific child elements (e.g. a `<conference>` block), add a branch in `_add_type_specific_fields()`.

**Adding a new licence:** add an entry to `LICENSE_MAP` (keyed on the URI suffix) and to `RIGHTS_TO_LICENSE` (keyed on the lowercased `dc.rights` string).

**Adding a new field:** most field-writing logic lives in named `build_*` functions called from `build_record_element()`. Add a new `build_*` function and call it there.

**Adding a new DSpace environment:** add an entry to `DSPACE_BASE_URLS` (e.g. `"staging": "https://..."`) — it automatically becomes a valid `--environment` choice.