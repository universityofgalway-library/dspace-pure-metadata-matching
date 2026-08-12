# create_pure_xml.py

Matches records between a **DSpace CSV export** and a **Pure JSON export**, then produces a **Pure Research Output Import XML** file conforming to the `v1.publication-import.base-uk.pure.atira.dk` schema.

---

## Requirements

- Python 3.10+
- `python-dotenv` (for reading the optional `.env` API key file)
- `python-dateutil` (for parsing DSpace embargo date strings)
- Standard library otherwise (`csv`, `json`, `xml.etree.ElementTree`, `xml.dom.minidom`, `argparse`, `re`, `urllib`, `mimetypes`)

---

## Usage

```bash
# Minimal — output defaults to ./xml_import/{environment}_pure_import_YYYY-MM-DD.xml
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
| `--environment` | Yes | Which DSpace environment to target: `test` or `prod`. Determines the host written into `<storeName>`/`<source>`, the domain used when rewriting DSpace bitstream links, and which Pure Journals API endpoint/API key is used. See [DSpace environments](#dspace-environments) below. |
| `--default-version` | No | `<version>` value to use for files that exist only in DSpace (no Pure `versionType` to draw from). Defaults to `publishersversion`. |
| `--output` | No | Output XML path. Defaults to `./xml_import/{environment}_pure_import_YYYY-MM-DD.xml` |
| `--modified-by` | No | Only include Pure records whose `modifiedBy` field equals this value |
| `--modified-after` | No | Only include Pure records modified strictly after this date (`YYYY-MM-DD`) |

### DSpace environments

| `--environment` | Base URL | Host written to `<storeName>` / `<source>` | Pure Journals API | API key env var |
|---|---|---|---|---|
| `test` | `https://galway.dspace7-test.openrepository.com/` | `galway.dspace7-test.openrepository.com` | `https://cust-uk-cc-dspace3.devel.elsevierpure.com/ws/api/journals/{uuid}` | `PURE_ROOT_API_KEY_TEST` |
| `prod` | `https://researchrepository.universityofgalway.ie/` | `researchrepository.universityofgalway.ie` | `https://research.universityofgalway.ie/ws/api/journals/{uuid}` | `PURE_ROOT_API_KEY` |

Any DSpace bitstream URL taken from the CSV's `pdf_links` column has its domain rewritten onto whichever host is selected — the path and query string are left untouched. URLs that don't come from DSpace (Pure-hosted file URLs, `hdl.handle.net` links) are never rewritten.

### Pure API key (optional, journal lookups only)

If a Pure record needs a `<journal>` but neither the Pure JSON nor the DSpace CSV has enough information (see [Journal resolution](#journal-resolution) below), the script can call the Pure Journals REST API as a last resort. The API key is read from:

1. A `.env` file in the working directory (loaded via `python-dotenv`), or
2. A real process environment variable of the same name,

under the variable name shown in the table above for the selected `--environment`. Real environment variables take precedence over the `.env` file. If no key is found, the script still runs — journal lookups just fall back to whatever Pure JSON and the DSpace CSV already provide, and a warning is printed whenever an API call would have been needed.

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
| `dc.title` | Fallback for the publication title when Pure `title.value` is empty; also used in unmatched-row warnings |
| `dc.relation.ispartof` | Fallback for `<hostPublicationTitle>` for book-chapter records when Pure `hostPublicationTitle` is absent or empty |
| `dc.description.peer-reviewed` | Peer review flag fallback |
| `dc.description.sponsorship` | Fallback for funding text when Pure `fundingText` is missing or empty |
| `dc.identifier.doi` | DOI fallback if Pure has none |
| `dc.identifier.issn` | ISSN fallback for journal resolution |
| `dc.identifier.isbn` | ISBN fallback for `book` records only, when Pure `printISBNs` and `electronicISBNs` are both absent/empty |
| `dc.language.iso` | Language fallback |
| `dc.rights` | Licence fallback (e.g. `CC BY-NC-ND`) |
| `dc.type` | Publication type fallback |
| `dc.date.embargo` | Embargo-date fallback when Pure has no `embargoPeriod.endDate`; the DSpace string is parsed into a date structure |
| `journal_title` | Journal title fallback |
| `dc.identifier.journal` | Journal title fallback |

All other columns are loaded but not directly mapped to XML.

### Pure JSON

Array of research output objects as returned by the Pure REST API. The script reads:

 `pureId`, `uuid`, `typeDiscriminator`, `type.uri`
- `title.value`
- `abstract`
- `language.uri`
- `peerReview`, `publicationStatuses`
- `category.uri` (publication category; defaults to `research`)
- `workflow.step`, `visibility.key`
- `contributors[]` — name, role, correspondingAuthor; `person.uuid` (internal) or `externalPerson.uuid` (external) used as the `<person id="...">` attribute
- `organizations[]`, `managingOrganization`
- `journalAssociation.journal.uuid`, `journalAssociation.title.title`
- `publisher.uuid` — used for `<publisher id="...">` on non-journal/non-specialist-publication types; there is no DSpace publisher fallback
- `printISBNs`, `electronicISBNs` — used for `book` records only
- `hostPublicationTitle`, `hostPublicationSubTitle` — used for book-chapter records; `dc.relation.ispartof` is only a fallback for `hostPublicationTitle`
- `fundingText` — used for funding text; `dc.description.sponsorship` is only a fallback
- `electronicVersions[]` (DOI, file, and link versions)
- `identifiers[]` (to extract the DSpace UUID for matching)
- `links[]` — Handle URLs (for matching); repository DOIs (`10.13025/` prefix, promoted to `<electronicVersionDOI>`); all other non-DOI links written to `<urls>`
- `modifiedBy`, `modifiedDate` (for filtering and duplicate resolution)

---

## Matching logic

For each Pure JSON record, the script attempts to find its counterpart in the DSpace CSV using two strategies in priority order:

1. **DSpace UUID** — looks for an entry in Pure's `identifiers[]` array where `idSource == "DSpace"`, then matches its `value` against the CSV `uuid` column.
2. **Handle URL** — looks for entries in Pure's `links[]` array where `alias == "Handle"`, then matches the URL against the CSV `handle` column (bare handles like `10379/1234` are automatically prefixed with `http://hdl.handle.net/`).

Pure records with no match in the CSV are simply skipped — this is not reported, since the DSpace CSV is treated as the authoritative list of what needs to end up in the import.

**DSpace rows with no match in Pure are reported instead.** After matching, the script checks every `uuid`/`handle` in the DSpace CSV against what was actually consumed during matching, and prints one `WARNING` line per unmatched DSpace row (with its `uuid`, `handle`, and `dc.title`) to stderr. This surfaces DSpace items that would otherwise be silently dropped from the import.

### Duplicate resolution

If the same DSpace UUID maps to more than one Pure record, the most recently modified Pure record (by `modifiedDate`) is kept. A warning is printed to stderr for every collision.

### Publication metadata source priority

Pure JSON is the authoritative source for publication metadata. DSpace CSV values are used only as explicit fallbacks where documented below.

The file-matching logic is the exception: DSpace filenames and bitstream URLs are used to match Pure `FileElectronicVersion` entries, and DSpace-only bitstreams can be added when no matching Pure file exists.

---

## Output XML

The output conforms to the Pure Research Output Import schema (`v1.publication-import.base-uk.pure.atira.dk`). Each matched record becomes one publication element.

### ID placement

| Value | XML location |
|---|---|
| Pure `pureId` | `id` attribute on the publication element, e.g. `<contributionToJournal id="19125272" subType="article">` |
| DSpace UUID | `<externalIds><id type="DSpace">…</id></externalIds>` |
| DSpace Handle | `<existingStores><existingStore><storeContentId>…</storeContentId></existingStore></existingStores>` |
| Pure `person.uuid` / `externalPerson.uuid` | `id` attribute on each `<person>` element inside `<persons>` |

### Publication type mapping

The XML element tag and `subType` attribute are resolved from the Pure `type.uri` field via `PURE_TYPE_MAP`. If the URI is not recognised (or absent), the script falls back to the DSpace `dc.type` string via `DSPACE_TYPE_MAP`. If neither source provides a recognised type, the record is written as `<other subType="other">`.

`PURE_TYPE_MAP` is large (70+ entries) and covers every Pure research-output subtype currently in use, grouped by XML element. A representative sample:

| Pure URI (suffix) | XML tag | subType |
|---|---|---|
| `contributiontojournal/article` | `contributionToJournal` | `article` |
| `contributiontojournal/systematicreview` | `contributionToJournal` | `systematicreview` |
| `contributiontoconference/paper` | `contributionToConference` | `paper` |
| `bookanthology/book` | `book` | `book` |
| `bookanthology/edited_book` | `book` | `edited_book` |
| `contributiontobookanthology/chapter` | `chapterInBook` | `chapter` |
| `contributiontobookanthology/entry` | `chapterInBook` | `entry` |
| `workingpaper/workingpaper` | `workingPaper` | `workingpaper` |
| `thesis/doctoral` | `thesis` | `phd` |
| `thesis/master` | `thesis` | `master` |
| `nontextual/digitalorvisualproducts` | `nonTextual` | `digitalorvisualproducts` |
| `patent/patent` | `patent` | `patent` |
| `memorandum/academicmemorandum` | `memorandum` | `academicmemorandum` |
| `contributiontomemorandum/contributiontoacademicmemorandum` | `contributionToMemorandum` | `contributiontoacademicmemorandum` |
| `contributiontoperiodical/article` | `contributionToSpecialist` | `article` |
| `contributiontospecialistpublication/article` | `contributionToSpecialist` | `article` |
| `othercontribution/other` | `other` | `other` |

For the complete, authoritative list of every supported subtype, see the `PURE_TYPE_MAP` dictionary in the script itself.

**DSpace `dc.type` fallback (`DSPACE_TYPE_MAP`):**

| dc.type | XML tag | subType |
|---|---|---|
| `journal article` | `contributionToJournal` | `article` |
| `review article` | `contributionToJournal` | `systematicreview` |
| `review` | `contributionToJournal` | `systematicreview` |
| `book review` | `contributionToSpecialist` | `book` |
| `conference paper` | `contributionToConference` | `paper` |
| `conference output` | `contributionToConference` | `other` |
| `conference poster` | `contributionToConference` | `poster` |
| `conference proceedings` | `contributionToConference` | `other` |
| `book` | `book` | `book` |
| `book part` | `chapterInBook` | `chapter` |
| `report` | `book` | `book` |
| `working paper` | `workingPaper` | `workingpaper` |
| `newspaper article` | `contributionToSpecialist` | `article` |
| `video` | `nonTextual` | `audiovisual_material` |
| `interactive resource` | `nonTextual` | `web_publication` |
| `data management plan` | `other` | `other` |
| `other` | `other` | `other` |

### Title and subtitle

- `<title>` is taken from Pure `title.value`.
- If Pure `title.value` is empty, `dc.title` from DSpace is used as a fallback.
- `<subTitle>` is taken from the Pure subtitle field only. There is no DSpace subtitle fallback.

### Journal resolution

`<journal>` is mandatory for `contributionToJournal` and `contributionToSpecialist` records, so it's always resolved with the following priority:

1. **Pure JSON** — `journalAssociation.journal.uuid` (→ `id` attribute) and `journalAssociation.title.title` (→ `title`).
2. **DSpace CSV** — `journal_title` / `dc.identifier.journal` (→ title) and `dc.identifier.issn` (→ ISSNs), used only for whichever piece Pure JSON didn't already provide.
3. **Pure Journals API** — only called when a journal UUID is known from Pure JSON but the title and/or ISSNs are still missing after steps 1–2. Since the lookup is by UUID, it can't help when Pure JSON has no `journalAssociation` at all. Requires the API key described in [Pure API key](#pure-api-key-optional-journal-lookups-only).

If nothing is found anywhere, the record is **skipped** (not exported) and a warning is printed, since an empty `<journal/>` would not pass schema validation.

### Electronic versions

Electronic versions are taken from Pure `electronicVersions[]`. DSpace is used only for the documented DOI and embargo fallbacks, plus the file-matching/Dspace-only-file logic below.

If Pure carries no electronic versions but the DSpace record has a `dc.identifier.doi`, a `<electronicVersionDOI>` is generated from it as a fallback.

#### Pure `electronicVersions[]`

| Pure `typeDiscriminator` | XML element |
|---|---|
| `DoiElectronicVersion` | `<electronicVersionDOI>` |
| `FileElectronicVersion` | `<electronicVersionFile>` — see file matching rules below |
| `LinkElectronicVersion` | `<electronicVersionLink>` |

If Pure carries no electronic versions but the DSpace record has a `dc.identifier.doi`, a `<electronicVersionDOI>` is generated from it as a fallback.

#### File matching rules

For each `FileElectronicVersion` in the Pure JSON, the script looks up whether a file with the same normalised filename exists in the DSpace CSV (`pdf_links`/`pdf_handle_paths`). The three resulting cases — which can all coexist within a single record — are:

| Case | `<filename>` | `<fileLocation>` | `<file id="...">` |
|---|---|---|---|
| **DSpace + Pure match** — filename found in both | DSpace (`pdf_handle_paths`) | DSpace URL (`pdf_links`, domain rewritten per `--environment`) | `<handle>:<filename>` |
| **Pure only** — Pure file has no DSpace counterpart | Pure `fileName` | Pure file URL | Pure `fileId` |
| **DSpace only** — DSpace file has no Pure counterpart | DSpace (`pdf_handle_paths`) | DSpace URL (domain rewritten) | `<handle>:<filename>` |

DSpace-only files (case 3) are appended after all Pure `FileElectronicVersion` entries are processed. Each semicolon-separated entry in `pdf_links` / `pdf_handle_paths` produces a separate `<electronicVersionFile>` element. Additional DSpace-only fields:

| Field | Value |
|---|---|
| `<mimetype>` | Guessed from the filename extension (defaults to `application/pdf`) |
| `<filesize>` | Omitted — the CSV doesn't carry a size for DSpace-only bitstreams |
| `<source>` | Always `DSpace` |
| `<externalRepositoryState>` | Always `STORED` |
| `<version>` | `authorsversion` |
| `<licence>` / `<publicAccess>` | `dc.rights` → licence; `dc.date.embargo` → embargoed, otherwise open |

Filename comparison is case-insensitive and URL-decodes both sides, so e.g. `Paper.PDF` and `paper.pdf` are treated as the same file.

#### Embargo dates

For a Pure electronic version, `embargoPeriod.endDate` is authoritative and is emitted as a date structure containing `year`, `month`, and `day`.

If Pure has no embargo end date for that electronic version, the DSpace `dc.date.embargo` string is parsed and converted to the same date structure before being written to `<embargoEndDate>`.

### Publication category

Every record gets a `<publicationCategory>` element written between `<peerReviewed>` and `<publicationStatuses>`. The value is taken from `category.uri` in the Pure JSON (trailing path segment only, e.g. `research` from `/dk/atira/pure/researchoutput/category/research`), defaulting to `research` when the field is absent.

### Existing stores

Every matched record gets an `<existingStores><existingStore>` block, telling Pure the record's content already lives in the DSpace store being targeted:

```xml
<existingStores>
    <existingStore>
        <storeName>DSpace</storeName>
        <updateRequired>true</updateRequired>
        <storeContentId>10379/17513</storeContentId>
    </existingStore>
</existingStores>
```

`storeName` is the selected `--environment`'s host, `storeContentId` is the DSpace Handle (CSV `handle` column — not the UUID), and `updateRequired` is always `true`. This block is added whenever the matched DSpace CSV row has a `handle` value, independent of whether any file was found for the record.

### URLs

`<urls>` is built **only** from the Pure JSON `links[]` array — the DSpace CSV's `dc.identifier.uri` column and the Pure JSON `portalUrl` field are no longer used, since neither is part of the record's own `links[]` and including them produced duplicate or unwanted entries. For each entry in `links[]`:

- Repository DOIs (`10.13025/` prefix) are skipped).
- Everything else, **including Handle links**, is written to `<urls>`. The `<description>` text comes from the link's own `description.en_IE` field, falling back to `alias` if no description is present.

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
| workflow `validated` | `validated` |
| workflow `approved` | `approved` |
| workflow `forApproval` | `forApproval` |
| workflow `entryInProgress` | `entryInProgress` |
| (unrecognised/missing workflow step) | `forApproval` |
| visibility `FREE` | `Public` |
| visibility `BACKEND` / `CAMPUS` / `RESTRICTED` | `Restricted` |
| (unrecognised/missing visibility key) | `Public` |

### Type-specific fields

Depending on the resolved XML tag, additional elements are written:

| XML tag | Extra fields written |
|---|---|
| `contributionToJournal` | `<journal>` (record is skipped if it can't be resolved — see [Journal resolution](#journal-resolution)) |
| `contributionToSpecialist` | `<journal>` (same skip behaviour as above) |
| `book` | `<printIsbns>`, `<publisher>` |
| `chapterInBook` | `<hostPublicationTitle>` (falling back to `dc.relation.ispartof`, and then an em dash if neither is present), `<publisher>` |
| `workingPaper` | `<publisher>` |
| `thesis` | `<qualification>` (`phd` or `master`, guessed from `dc.type`), `<publisher>` |

---

## Console output

The script prints a short summary to stdout on each run, and also writes the **same console output** to a plain-text `.log` file alongside the XML (same directory and base filename, `.log` extension instead of `.xml`):

```
Environment: prod -> https://researchrepository.universityofgalway.ie/ (store host: researchrepository.universityofgalway.ie)
Pure API key: found (PURE_ROOT_API_KEY).
Loading DSpace CSV: dspace.csv
  -> 64 records by UUID, 64 by Handle loaded.
Loading Pure JSON: pure.json
  -> 120 records loaded.
  -> 115 records after filtering.
Matching records...
  WARNING: DSpace row not matched to any Pure record -- uuid='a1b2c3d4-...' handle='10379/17513' title='Some Title'
  -> 98 matched, 2 DSpace rows without a Pure match.
  -> 98 records after duplicate resolution.
Building XML...
XML written to: ./xml_import/prod_pure_import_2026-06-19.xml
  -> 98 publication element(s) exported.
Log written to: ./xml_import/prod_pure_import_2026-06-19.log
```

Warnings about unmatched DSpace rows, duplicate Pure records, missing journal info, and missing API keys are all written to stderr (and are still captured in the `.log` file).