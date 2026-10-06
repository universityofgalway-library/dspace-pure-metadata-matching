# Research Output Record Matching Script

Matches and enriches research output records between DSpace (institutional repository) and Pure (research information system).

---

## Overview

The script performs three main tasks:

1. **Match records** across systems using DOIs, handles, and title similarity
2. **Update existing Pure records** with DSpace metadata (precedence-based or override)
3. **Create new Pure records** for unmatched DSpace items

### Script versions

This document describes **`match_records_v2.py`**. **`match_records.py`** behaves the same except for three things it does not do:

- check "same person" against the person mapping's IDs and name variants (it compares person UUIDs only);
- merge duplicate contributors already in Pure;
- reduce internal contributors to their primary organisation.

These are covered in [Same person — checked against the person mapping](#same-person--checked-against-the-person-mapping) and [Primary organisation only](#primary-organisation-only-match_records_v2py).

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
JOURNAL_MAPPING_JSON      = "./pure_entities/pure_journals.json"
OUTPUT_DIR                = f"./record_matching/prod_all_output_{TODAY}"
```

`USE_TEST_ENV` also controls which org config file is loaded:
- `True` → `./scripts/test_orgs_config.json`
- `False` → `./scripts/prod_orgs_config.json`

All input files (CSV and JSON, including the org config) are read as UTF-8 **with or without a byte-order mark (BOM)** — DSpace CSV exports start with a BOM, which is handled automatically. Output files are written as plain UTF-8.

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
| `JOURNAL_MAPPING_JSON` | JSON array | Pure's full journal dump — used to validate `journal_uuid` from the DSpace CSV, and to look journals up by ISSN (`issns`, `additionalSearchableIssns`) and by title (`titles`, `additionalSearchableTitles`); see [Journal Matching](#journal-matching) |

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
| `dc.identifier.doi` | Publisher DOI(s). **Multi-entry field** (values separated by ` ; `); every DOI in it is used. A repository DOI (`10.13025/*`) in this field is treated as a repository DOI. See [DOI & Handle Normalisation](#doi--handle-normalisation) |
| `dc.identifier.uri` | Handle and/or repository DOI (semicolon-separated) |
| `dc.description.abstract` | Abstract text |
| `dc.description.sponsorship` | Funding acknowledgement text |
| `dc.language.iso` | ISO 639-3 language code (e.g. `eng`, `gle`) |
| `dc.publisher` | Publisher name — matched against `PUBLISHER_MAPPING_JSON` for applicable record types |
| `dc.type` | Resource type (e.g. `journal article`, `book`) |
| `journal_uuid` | Pure journal UUID from the enrichment step (optional) — validated against `JOURNAL_MAPPING_JSON`; see [Journal Matching](#journal-matching) |
| `journal_issn` | ISSN(s) of the journal found by the enrichment step (optional, multi-entry) — used for ISSN matching |
| `journal_title` | Title of the journal found by the enrichment step (optional) — used for title matching |
| `dc.identifier.issn` | ISSN(s) from DSpace (optional, multi-entry: `,`, `;` or ` ; ` separated) — used for ISSN matching |
| `dc.identifier.journal` | Journal title from DSpace (optional, multi-entry) — used for title matching |
| `dc.subject` | Semicolon-separated free-text keywords (optional) — added as a free-keywords group; see [Subject Keywords](#subject-keywords) |
| `pdf_handle_paths` | Semicolon-separated PDF paths (optional). Only used to clean up HTML-entity-encoded filenames for logging — see [Filename Cleaning](#filename-cleaning); does not otherwise affect matching or field updates. |

---

## Matching Strategy

**Which DSpace rows are processed.** Rows are skipped (counted as *out of scope* in the final summary) when they are not in the Publications collection, when their `dc.type` is **`dataset`** — datasets are not uploaded from DSpace to Pure (`EXCLUDED_DSPACE_TYPES` at the top of the script) — or when all contributor fields are empty. Skipped rows still count as DSpace items for the identity checks below (their Handles and DOIs belong to them).

Records are matched in priority order:

0. **DSpace UUID — highest priority.** A Pure record that carries this DSpace item's UUID (an identifier with `idSource: "DSpace"`) is its record; when one is found, no other matching is done.

**A Pure record that already carries a DSpace UUID is matched only through that UUID** — it is never a candidate in steps 1–4 for any other DSpace item, whatever its DOI, Handle or title (these may be wrong on such a record; its DSpace UUID is authoritative). Steps 1–4 only consider Pure records **without** a DSpace UUID. The run log shows how many Pure records are linked and how many are not.

1. **Publisher DOI** — every publisher DOI in `dc.identifier.doi` (multi-entry field) is looked up. A Pure record reached through several DOIs, or indexed twice (e.g. via an electronic version and a DOI link), is counted only once.
2. **Repository DOI** — from `dc.identifier.uri`, pattern `10.13025/*`, followed by any repository DOI found in `dc.identifier.doi`
3. **Handle** — from `dc.identifier.uri`, pattern `10379/*`
4. **Title** — two sub-strategies applied in order:
   - **Exact** — normalised title string match against index
   - **Fuzzy** — token-based candidate retrieval + fuzzy scoring, 90% threshold

DOIs and handles are compared in normalised form on both sides — see [DOI & Handle Normalisation](#doi--handle-normalisation).

### Safeguards against false matches

**Title matches** (steps 4a and 4b) compare every combination of title and subtitle on both sides. A candidate is rejected if:
- **it already belongs to another DSpace item** — the Pure record carries a DSpace UUID that isn't this row's, and that item is in the CSV. It is that item's record, so it is never matched to another item by title (identifier matches are handled separately, see below). Consequence: when the same work was deposited twice in DSpace, the second deposit gets its own Pure record unless an identifier links it;
- **both sides have publisher DOIs and none agree** — different DOIs mean different outputs, whatever the titles say (e.g. two articles both titled "Introduction");
-  **the numbers in the two titles differ** (items of a numbered series: "Factsheet No. 17" vs "No. 18", "Volume 5" vs "Volume 6", "2014" vs "1998-2022"). Otherwise it is only accepted if:
- **the full title is identical** (title + subtitle on both sides; case, punctuation and the title/subtitle split ignored, **numbers kept** — so "An Reiviú 2015" ≠ "An Reiviú 2024" and "Part 1" ≠ "Part 2") **and** the research output type is the same **and** the publication year is the same (when either year is missing it can't be compared and isn't required — generic titles such as "Introduction" or "Editorial" recur every year); or
- **otherwise** (fuzzy match, or equal only when one side's subtitle is ignored — e.g. Pure "Introduction" + subtitle vs DSpace "Introduction"): the **publication year and the research output type are both confirmed to be the same**.

**Short titles need a common author.** When a title match passes all of the rules above but either side's full title (title + subtitle) has **at most 5 words, small words included** (`SHORT_TITLE_MAX_WORDS` — generic titles such as "Introduction", "Editorial", "Book review"), at least one person must appear on both sides: the same surname (case, accents and apostrophe form ignored) with the same first initial, or no initial on one side. DSpace people come from the author, editor, translator and illustrator fields (institution names excluded); Pure people from the record's contributors. A Pure record without contributors can't confirm a short title, so it isn't matched. DOI, Handle and DSpace UUID matches are not affected.

**Fuzzy title matches** (step 4b) must also pass a **word-level check**: for at least one combination of title / title + subtitle on each side, every content word of each title has a counterpart in the other. Small words (*a, an, the, of, and, in, on, for, to, with, by, at, from, as, or, into, its, their, is, are, be*) are ignored, and spelling variants and small typos count as the same word (word similarity ≥ 80, `TITLE_WORD_SIMILARITY`: "behaviour"/"behavior", "centre"/"center", "cell"/"cells"); case, punctuation and markup tags such as `[clc]` are ignored. **An extra or missing content word means no match** — "The cost-effectiveness of …" vs "The effectiveness of …", "… in tension …" vs "… in compression …", "Correction to: …" vs the original article. This is deliberately strict: titles that differ by a generic word ("… Report" vs "… Final Report") are also kept apart.

The DSpace item's type is the Pure type its `dc.type` maps to (an unmapped type counts as Other contribution, as for new records). On the Pure side, when the Pure record belongs to DSpace item(s) that are in the CSV, those items' `dc.type` is used — so a record created as Other contribution because its journal wasn't found doesn't distort the comparison; otherwise the Pure record's own type. Years: the Pure record's publication years plus those linked items' years. When the best fuzzy candidate is rejected, the next best that passes is used.

**Publisher DOI matches** with a Pure record that already belongs to a **different** DSpace item (in the CSV or not) are only kept if the two describe the same output: the titles agree under the strict **word-level check** (see below) **and** the research output type is the same. Otherwise the DOI match is rejected — the same DOI on two different DSpace items is a data error (a DOI copied to the wrong item) or a DOI shared by several outputs (a book's DOI on its chapters, or chapter vs book). Such cases are listed in `publisher_doi_conflicts_YYYY-MM-DD.csv` and counted in the final summary. DOI matches with a Pure record that belongs to no DSpace item, or to this row's item, are unaffected.

Every rejected candidate is printed in the processing log and recorded in the status log as `rejectedMatches` (`pureUUID`, `reason`).

### Choosing among duplicates

When several Pure records match one DSpace row, the record to update is chosen as follows:

1. **DSpace UUID first.** A record "has a DSpace UUID" if one of its `identifiers` has `idSource: "DSpace"` and a non-empty value (as `Id` or `PrimaryId`).
   - **Exactly one** duplicate has a DSpace UUID → that record is updated; no further comparison.
   - **Several** have one → duplicates without a DSpace UUID are discarded, and the standard comparison below runs on the rest only.
   - **None** has one → the standard comparison runs on all of them.
2. **Standard comparison:** visibility (FREE/CAMPUS) → number of internal contributors → field completeness → whether last modified by a real user.

Which case applied is recorded in the status log as `duplicateResolution`.

### One DSpace identity per record

Every updated Pure record carries exactly **one** DSpace identity: one DSpace UUID (identifier with `idSource: "DSpace"`), one repository Handle link and one repository DOI electronic version — all of the same DSpace item. Other identifiers (Scopus, ORCID, …), other links and other electronic versions are not touched; an existing Handle link or repository DOI electronic version of that item is kept as it is (with its metadata), otherwise it is created.

Which item: the CSV row's — unless the Pure record is already linked to a **different** DSpace item, in which case `DSPACE_UUID_PREFERENCE` (set at the top of the script) decides:

| `DSPACE_UUID_PREFERENCE` | Result |
|---|---|
| `"dspace"` (default) | The CSV row's DSpace UUID, Handle and repository DOI are written; the other item's are removed. |
| `"pure"` | Pure's existing DSpace UUID is kept, with that item's Handle and repository DOI (taken from the CSV when the item is in it). |

If that item's Handle or repository DOI isn't known (item not in the CSV, or it has none), Pure's existing ones are kept — except those the CSV shows belong to a different DSpace item; if more than one remains, a manual-review warning is printed. The status log records `dspaceUuidResolution` for every mismatch. New records always carry a single identity (the CSV row's).

### DSpace UUID mismatches

Since Pure records with a DSpace UUID are only matched through that UUID (step 0), a mismatch can no longer arise from steps 1–4; the report remains as a safeguard. Any DSpace UUID counts in step 1, not only the UUID of the DSpace item being processed. Whenever the Pure record about to be updated — whether it was the **only match** or was **chosen among duplicates** — already carries DSpace UUID(s) and none of them equals the processed item's `uuid` (compared case-insensitively), the record is still updated, but the case is reported:

- a warning line in the processing log: `⚠️ DSpace UUID mismatch (single match | chosen among duplicates): DSpace item … is updating Pure record …, which already has DSpace UUID(s) […]`
- a row in `dspace_uuid_mismatches_YYYY-MM-DD.csv` with: `dspace_uuid`, `pure_record_dspace_uuids`, `handle`, `pure_uuid`, `dspace_title`, `pure_title`, `portal_url`, `case`, `match_type` (how the record was matched: Publisher DOI, Repository DOI, Handle, Title (Exact), Title Similarity), `resolution` (per `DSPACE_UUID_PREFERENCE` — see [One DSpace identity per record](#one-dspace-identity-per-record))
- the `DSpace UUID mismatches` line in the final counts

Records whose DSpace UUID is the processed item's own are never reported.

---

## Field Mapping & Update Rules

### Override Mode (`OVERRIDE_MODE = True`)

- Replaces all mapped fields with DSpace values
- Removes existing contributors/funders and uses only DSpace data
- Use with caution — overwrites curator work

### Precedence Mode (`OVERRIDE_MODE = False`)

- Uses precedence rules to update data in Pure
- Adds new funders without removing existing ones
- Contributors: built on what Pure already has — see [Contributors in Updates](#contributors-in-updates). Nothing already in Pure is discarded.

| DSpace Field | Pure Field | Rule |
|---|---|---|
| `uuid` | `identifiers` (PrimaryId, idSource: DSpace) | Always set; demotes existing PrimaryId to Id |
| `dc.contributor.*` | `contributors` | DSpace contributors in DSpace order (reusing the linked Pure contributors), then all other Pure contributors kept; no person added twice, Pure duplicates merged — see [Contributors in Updates](#contributors-in-updates) |
| `dc.contributor.funder` | `fundingDetails` | Add new funders |
| `dc.date.issued` | `publicationStatuses[0].publicationDate` | Fill if blank |
| `dc.identifier.doi` | `electronicVersions` (publisher version) | Each distinct DOI added once, if not already present in any form; values that aren't DOIs are skipped with a warning |
| `dc.identifier.uri` (DOI `10.13025/*`) | `electronicVersions` (repository version) | Add if missing; access/licence/version-type always set — see [Electronic Versions & Links](#electronic-versions--links) |
| `dc.identifier.uri` (handle) | `links` | Set as repository handle link |
| `dc.description.abstract` | `abstract` | Fill if blank |
| `dc.description.sponsorship` | `fundingText` | Fill if blank |
| `dc.title` + `dc.title.subtitle` | `title` + `subTitle` | Fill if blank; subtitle stripped from title if embedded (see below) |
| `dc.language.iso` | `language` | Fill if blank |
| `dc.date.embargo` | `accessType` / `embargoPeriod` on the repository electronic version | Always overwrite — see [Electronic Versions & Links](#electronic-versions--links) |
| `dc.subject` | `keywordGroups` (free keywords) | Add new keywords; existing ones are preserved, not overwritten — see [Subject Keywords](#subject-keywords) |
| `dc.publisher` | `publisher` | Fill if blank (BookAnthology, ContributionToBookAnthology, OtherContribution, WorkingPaper, NonTextual types only) |
| `journal_uuid` / ISSNs / journal titles | `journalAssociation.journal.uuid` | Fill if blank — `journal_uuid`, then ISSN, then title; see [Journal Matching](#journal-matching) |
| _(always)_ | `workflow.step` | Always set to `validated` on every output record |
| _(always)_ | `accessType` on every electronic version | Mandatory field in Pure — always ensured to be present; see [Electronic Versions & Links](#electronic-versions--links) for exactly how per EV type |

### Contributors in Updates

In precedence mode, the contributor list of an existing record is built on the Pure record, never replacing it:

1. **DSpace contributors first, in DSpace order** (grouped by role: authors, editors, translators, illustrators). For each one matched to a person, the Pure contributor already linked to that person is **reused**, keeping everything Pure has on it (role, organisations, corresponding-author flag, …); only the name spelling is aligned with the person mapping. A person not yet on the record is added as a new contributor.
2. **Every other Pure contributor is kept**, after the DSpace ones, in its original Pure order — including contributors not listed in DSpace and those whose DSpace name couldn't be matched.
3. **No person is added twice**, and **duplicates already in Pure are merged** (see below).

If no DSpace contributor can be matched at all, the Pure contributors are left exactly as they are and not sent. New records and **override mode** use only the DSpace contributors (in DSpace order); the "no person added twice" rule applies to them too.

#### Same person — checked against the person mapping

The person mapping sometimes describes one person in several entries (e.g. with first and last name swapped, or as separate internal and external profiles). Entries that share any UUID are treated as **one person**, with all of their UUIDs and all of their name variants (names and alternative names, in both orders). Everywhere below, "same person" means the same person in this sense.

**Reusing a Pure contributor** for a DSpace contributor:
1. **By ID first** — a Pure contributor linked to **any** of the person's UUIDs (not only the first one) is reused; the exact UUID is preferred, then an internal contributor.
2. **By name only as a fallback** — a Pure contributor with the same name is reused only if its UUID is **not** in the person mapping. If the mapping says its UUID belongs to a **different** person, it is not reused: the DSpace person is added, the Pure contributor is kept, and a warning is printed.

**No person added twice** — a DSpace contributor who is the same person as one already on the list is skipped: the same name repeated in DSpace, two spellings (`Lang, Mark` / `Lang, M. J.`), apostrophe variants, the same person as author and editor, or two mapping entries of one person.

**Merging duplicates already in Pure** — a Pure contributor that is the same person as one already on the list is merged into it rather than kept twice:
- **same UUID** → merged;
- **different UUIDs of the same person in the mapping** → merged only if **both** contributors' names are among that person's name variants; otherwise both are kept and a warning is printed for review;
- **the same name alone never merges** two contributors (they may be different people).

When two entries are merged, the earlier one is kept — except that an **internal** contributor always wins over an external one. The kept entry keeps all its own data, takes the **corresponding-author** flag if either entry had it, and — if both are the same type (internal/internal or external/external) — the **union of their organisations** (`organizations`, `externalOrganizations`). Each merge is logged in the processing log (`🔗 Merged duplicate Pure contributor …`), with a note if the two entries had different roles.

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

**Apostrophes are equivalent.** When names are compared, these characters are all treated as a straight apostrophe (`'`): right curly `’`, left curly `‘`, modifier letter `ʼ`, prime `′` and acute accent `´`. So `O'Malley`, `O’Malley` and `OʼMalley` — or `D'Arcy` and `D’Arcy` — are the same name. This applies wherever person names are compared, across all three name sources (DSpace contributor fields, existing Pure contributors, and the person mapping file), including the author/editor overlap check. It affects comparison only: names written to Pure are not rewritten by it.

**Reading DSpace contributor names.** Before matching:
- HTML character references are decoded before the field is split into names, so `O&apos;Dowd, Colin` stays one name (the `;` of `&apos;` is not taken as a separator).
- **Institution names are dropped entirely** — names containing one of these words, as whole words in any case, are neither added as contributors nor listed as unmatched: university, college, academy, institute, association, department, school, nuig, ollscoil, centre, center, laboratory, institution, organisation, organization, foundation, society, proceedings, bank, programme, union, education, research, national, international, group, committee (`NAME_STOPWORDS`).

**Matching order.** Each name is first looked up **exactly as before**, so a name that matched before always matches the same person. Only if that finds nobody, corrected readings are tried:
1. a trailing comma with nothing after it is removed (`Damien Haberlin,` → `Damien Haberlin`);
2. digits attached to the end of a word are removed — footnote marks (`Ní Fhlathartaigh1, Mary` → `Ní Fhlathartaigh, Mary`);
3. a surname prefix left at the end of the first name is moved to the surname (`Shea, Emma O’` → `O’Shea, Emma`; `Bruijn, Jos de` → `de Bruijn, Jos`). Prefixes: Mac Giolla, Mac Con, Mac an, Nic an, van der, Mhic, Mac, Nic, Mc, O' (any apostrophe form), Ó, Ní, de, Uí, Ua, van, von, La. A bare `O` without an apostrophe is not a prefix (it is usually a middle initial: `Amer, Amal O`).

If none of these finds anybody, the same readings are tried **ignoring accents** (`Ni Riordain` = `Ní Ríordáin`). Names that still find nobody are listed as unmatched under their original DSpace spelling.

**Accented spelling preferred.** The name written on a contributor is the person mapping's — except that if the same name exists with more accents, that spelling is used, wherever it comes from: the mapping's alternative names, the DSpace name, or (when reusing a Pure contributor) the name Pure already has. It keeps the mapping's capitalisation.

**Duplicate resolution priority:**
1. Paper evidence match (DOI or handle > title)
2. Internal Person > External Person
3. Visibility: FREE/CAMPUS > other
4. Most complete metadata (field count from Pure API)

### Contributor Roles

All four DSpace contributor fields are processed: `author`, `editor`, `translator`, `illustrator`. Special cases:

- **Author/editor overlap:** If the same name appears in both fields, one role is kept based on `dc.type` (editor preferred for `book`, `interactive resource`, `conference proceedings`; author preferred otherwise).
- **Same person twice:** each person is added to a record only once, checked against the person mapping's IDs and name variants — see [Contributors in Updates](#contributors-in-updates).
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
| `managingOrganization` | First internal contributor's org (unless it is a Central University org — see below), or Library Repository UUID from org config (always for records with only external contributors) |
| `organizations` | Internal contributors' organisations, plus the managing organisation — never empty |
| `language` | `en_IE` (overridden if `dc.language.iso` is present) |

---

## Organization Handling

**Internal organizations** are validated against the Pure API. If a UUID is invalid (not found as an internal org):

- If `COLLECT_EXTERNAL_ORGS = False`: the UUID is discarded and a warning is logged.
- If `COLLECT_EXTERNAL_ORGS = True`: the Pure external-organizations endpoint is checked. If found there, it is moved to `externalOrganizations`. If not found there either, it is discarded and a warning is logged.

**External organizations:** UUIDs listed in `EXTERNAL_ORGS_TO_IGNORE` (loaded from the org config file) are always filtered out.

**Managing organization:** Set to the first internal contributor's primary organization. If that organization is one of the Central University orgs defined in the org config (`CENTRAL_UNIVERSITY`), the Library Repository UUID is used instead. Also falls back to Library Repository UUID if no internal contributors exist.
- **New records** and **override mode**: set as above.
- **Updates in precedence mode**: Pure's managing organisation is kept (it is only set when Pure has none) — **except** when the record, after the update, has **only external contributors**: then the managing organisation is set to the Library Repository.

**Record-level organizations** are collected from all resolved contributors and written to the top-level `organizations` (internal) and `externalOrganizations` (external, only if `COLLECT_EXTERNAL_ORGS = True`) arrays.

**The managing organisation is always in `organizations`.** Unlike `externalOrganizations`, the `organizations` list can't be empty: the managing organisation is added to it (at the end) whenever it isn't already there — in new records and in updates, in both modes. For a record with only external contributors, `organizations` is therefore `[Library Repository]`.

**Orphan record-level organisations are removed** (updates of existing records, in both precedence and override mode). The contributors the record will have after the update are checked — the new list if contributors are updated, otherwise Pure's existing contributors:
- a record-level `organizations` entry is kept only if at least one contributor has that organisation in its `organizations`;
- a record-level `externalOrganizations` entry is kept only if at least one contributor has it in its `externalOrganizations`.

Everything else is removed, and the removed UUIDs are recorded in the status log as `removedOrphanOrganizations`. Entries without a UUID are left untouched. This step doesn't read or change `managingOrganization`; afterwards, the managing organisation is added back to `organizations` if it isn't there (see above), so the list is never empty. New records need no such step — their lists are built only from their contributors.

### Primary organisation only (`match_records_v2.py`)

Every **internal** contributor lists **only its primary organisation** in `organizations` — whether it was newly built, reused from Pure, kept from Pure, or merged. The primary organisation comes from the person mapping, across all entries of the same person:
- its `primaryInternalOrganization`, if set;
- otherwise its **only** internal organisation, if it has exactly one.

If neither applies — the person has several organisations and no primary, or isn't in the person mapping — the contributor's organisations are **left as they are** and a warning is written to the processing log (`⚠️ Primary organisation of … is not known in the person mapping`). For newly built contributors in that situation the first organisation listed in the mapping is used, as before.

The contributor's `externalOrganizations` and all external contributors are not touched. Record-level organisations that are no longer attached to any contributor are then removed as orphans (see above).

If no DSpace contributor could be matched, Pure's contributors are still sent when this rule (or merging duplicates) changes them; if nothing needs changing, they are left untouched.

---

## Electronic Versions & Links

`accessType` is a mandatory field on every Pure electronic version, and each type is treated differently depending on whether it's sourced from the DSpace repository:

**Repository-sourced** — the repository DOI (`10.13025/*`) electronic version, and any `FileElectronicVersion` on a record that is linked to DSpace (an `identifiers` entry with `idSource: "DSpace"`, present already or added this run via the `uuid` column). Both get identical, always-overwritten treatment:
- `accessType`: `open`, unless an active embargo is present (`dc.date.embargo` / `dc.description.embargo`), in which case `embargoed` with `embargoPeriod` set to the resolved end date.
- `licenseType`: always `cc_by`.
- `versionType`: always `authorsversion` ("Author accepted manuscript").

A repository DOI only ever exists because the institutional repository minted it for a DSpace item, so it's treated as repository-sourced unconditionally — no DSpace-link check is applied to it (unlike the file version, which needs one, since a file could exist on a record that merely matched by title or DOI).

**Everything else** — publisher DOI electronic versions, any other non-file electronic version (e.g. a link-type version), and a `FileElectronicVersion` on a record that is **not** DSpace-linked. Pure's own metadata always takes precedence here:
- `accessType`: left exactly as Pure already has it; only defaulted to `Unknown` (`/dk/atira/pure/core/openaccesspermission/unknown`) if missing entirely — **except** when the EV already has an `embargoPeriod` set. Pure's own validation requires `accessType` to be `Embargoed` whenever an embargo end date is present, even once that date has passed (Pure treats the file as effectively open once the date arrives, but insists the status field still say `Embargoed`). Uploading an EV with an embargo end date but a missing or different `accessType` is rejected outright with `validation.accessextensionembargo.embargodateswhennotembargoed`. So if `embargoPeriod` is present and `accessType` isn't already `Embargoed`, it's corrected to `Embargoed` regardless of whether it was missing or set to something else.
- `licenseType`, `versionType`, `embargoPeriod`: never touched — left exactly as Pure already has them, whether present or absent. (For a brand new record being created for the first time, there's nothing to preserve, so these are simply left unset — only the `Unknown`/`Embargoed` accessType default applies.)

Version order in the output: repository DOI → publisher DOIs → other → file versions.

DOI links are removed from `links`; handles are kept. If DSpace and Pure have conflicting handles, a warning is printed and manual review is flagged.

### No duplicate DOIs or handles

- **Publisher DOIs:** each distinct DOI appears once. If Pure already holds the same DOI more than once (in any form), a single electronic version is kept — the copy with the most filled fields, so no access/licence/version metadata is lost; ties keep the first. A DOI from DSpace that already exists in Pure in any form is not added again.
- **Repository DOI:** at most one.
- **Handle links:** one link per handle. Pure copies that differ only in form (`http`/`https`, letter case, trailing slash, `handle.net` host) count as the same handle; the copy with the most metadata is kept.
- `dc.identifier.doi` values that aren't DOIs (ISBNs, article numbers, web pages, `NA`, …) are **not** added as DOI electronic versions; a warning is printed instead.

### DOI & Handle Normalisation

**DOIs** are recognised and compared in one normalised form, `https://doi.org/10.…` (lower case), from any of: a bare `10.…` DOI, `doi.org` or `dx.doi.org` URLs (`http`/`https`, including the `https:/` typo), and `doi:`, `DOI:`, `DOI: `, `DOI ` or `:` prefixes. Trailing full stops are removed (`10.1080/…1339786.` → `…1339786`). `dc.identifier.doi` is split on ` ; ` (a semicolon with a space on at least one side), so DOIs that themselves contain a bare `;` — e.g. SICI DOIs such as `…3.0.CO;2-E` — are not split.

**Handles** are recognised and compared as `http://hdl.handle.net/10379/…` from any of: `hdl.handle.net`, `handle.net` or `www.handle.net` hosts, with or without `http(s)://`, in any letter case, with or without trailing slashes. Any `hdl.handle.net` URL — including another institution's handle — is still recognised as a handle link.

---

## Subject Keywords

`dc.subject` (semicolon-separated) is parsed and added to the record's `keywordGroups` as a free-keywords group. Applies to both updated and newly created records.

- **Existing free keywords are kept.** They are read from both places Pure stores them — the group's `keywords` list and its `keywordContainers` — and merged with the DSpace subjects. Duplicates are detected case-insensitively (the existing spelling wins), and the merged list is sorted alphabetically.
- **All other keyword groups are passed through unchanged** — e.g. ASJC subject areas and Sustainable Development Goals (`ClassificationsKeywordGroup`, including their own `keywordContainers` with `structuredKeyword` entries) and Pure's authors group. The free-keywords group is placed after them.
- **Shape written** — the same as a user-entered group on a valid Pure record: the merged list appears both in `keywords` and in one `ACCEPTED` / `USER_SUPPLIED` keyword container. (A group with only `keywords` is not picked up by Pure.)

```json
{
  "typeDiscriminator": "FreeKeywordsKeywordGroup",
  "logicalName": "keywordContainers",
  "name": { "en_IE": "Keywords" },
  "keywords": [
    { "locale": "en_IE", "freeKeywords": [ /* merged keywords */ ] }
  ],
  "keywordContainers": [
    {
      "state": "ACCEPTED",
      "origin": "USER_SUPPLIED",
      "freeKeywords": [
        { "locale": "en_IE", "freeKeywords": [ /* same merged keywords */ ] }
      ]
    }
  ]
}
```

---

## Filename Cleaning

DSpace-exported filenames in `pdf_handle_paths` can be HTML-entity-encoded (e.g. an accented character rendered as `&#769;`). Before being written to any log or output, these are decoded (HTML entity unescape) and Unicode-normalized (NFKC) so accented characters and ligatures render correctly instead of showing raw entity markup.

---

## Publisher Matching

`dc.publisher` is looked up against `PUBLISHER_MAPPING_JSON` and set on the `publisher` field for records of type `BookAnthology`, `ContributionToBookAnthology`, `OtherContribution`, `WorkingPaper`, and `NonTextual`. Unmatched publisher names are written to `unmatched_publishers_YYYY-MM-DD.csv`.

---

## Journal Matching

For `ContributionToJournal` **and** `ContributionToPeriodical` records (treated identically — no journal type in Pure is excluded), the journal is found in four steps, stopping at the first that succeeds:

1. **`journal_uuid`** from the DSpace CSV — used if it is one of the journals in `JOURNAL_MAPPING_JSON`. A UUID that isn't (stale, mistyped, merged/deleted journal) is treated as missing, rather than being sent to Pure and rejected.
2. **ISSN** — all ISSNs in `journal_issn` and `dc.identifier.issn` (both multi-entry) are checked against **every** ISSN of every Pure journal, in both `issns` and `additionalSearchableIssns`.
   - ISSNs are normalised to `NNNN-NNNC` (hyphen optional, `x` → `X`). Values from `dc.identifier.issn` must pass the ISSN check digit, so page ranges such as `1690-1697` are ignored.
   - If all matching journals are **the same journal** — their titles are spelling variants of each other (duplicate Pure records) — the one with the most complete metadata is used.
   - If the matching journals have **genuinely different titles** (one ISSN matching several titles, or several ISSNs belonging to different journals), the ISSN result is not used and step 3 decides.
3. **Title** — titles in `dc.identifier.journal` and `journal_title` are compared with every Pure journal title in `titles` and `additionalSearchableTitles` (e.g. "J Cell Sci"): lowercased, `&` counted as "and", punctuation removed (accents are kept). If several journals match, the one with the most complete metadata is used.
4. **Nothing found** — a new record is **downgraded to `OtherContribution`**; an existing Pure record is left as it is (its type is never changed; only a warning is printed).

**Most complete metadata** = most filled top-level fields, excluding system fields (`uuid`, `pureId`, `version`, `created…`/`modified…`, `portalUrl`, …); remaining ties go to the journal listed first in `JOURNAL_MAPPING_JSON`.

**Spelling variants** — two journal titles are variants when, after lowercasing, `&` → `and`, dropping `(…)` qualifiers such as "(Switzerland)" and removing punctuation and spaces, they contain **the same numbers** and are **at least 95% similar**, or one is a word-by-word abbreviation of the other (e.g. "J. Civ. Struct. Health Monit." / "Journal of Civil Structural Health Monitoring"). Because numbers must match, "Ethnomusicology Ireland 9" and "… 10" are different journals.

**Existing Pure records** only get a journal when they have none; an existing `journalAssociation` is never replaced.

**Logging:**
- status log: `journalMatchedBy` (`ISSN` or `title`), `journalCandidates` (all journals that qualified, when there were several), `typeChangedToOther` (for downgraded new records);
- `unmatched_journals_YYYY-MM-DD.csv` — one row per downgraded new record, and per existing journal-type record that has no journal and none could be found, with: `handle`, `title`, `dc.identifier.issn`, `journal_issn`, `dc.identifier.journal`, `journal_title`, `reason` (e.g. `ISSN not found in Pure; no journal title in DSpace`), `pure_uuid` (existing records only). New records skipped for having no matched contributors are not listed.
- the `Unmatched journals` line in the final counts.

`JOURNAL_MAPPING_JSON` must exist and be readable — the script stops at startup otherwise.

---

## Pure IDs (`pureId`) in the Output

`pureId` is a Pure system field. In **updates** of existing records, only the **record-level** `pureId` is supplied, right after `uuid`, to identify the record. Every nested `pureId` — carried over from Pure in contributors, electronic versions and their files, identifiers, keyword groups, etc. — is removed. Nested objects stay linked to their Pure entities through their UUID references (e.g. `person.uuid` / `externalPerson.uuid` on every contributor, `fileId` on files). **New records** contain no `pureId` at all.

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
├── unmatched_publishers_YYYY-MM-DD.csv
├── unmatched_journals_YYYY-MM-DD.csv
├── dspace_uuid_mismatches_YYYY-MM-DD.csv
└── publisher_doi_conflicts_YYYY-MM-DD.csv
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
| `unmatched_journals.csv` | Journal-type records for which no journal was found — see [Journal Matching](#journal-matching) |
| `dspace_uuid_mismatches.csv` | Records updated although they carry a different DSpace item's UUID — see [DSpace UUID mismatches](#dspace-uuid-mismatches) |
| `publisher_doi_conflicts.csv` | Publisher DOI matches rejected because the DOI is on a different DSpace item with a different title: `dspace_uuid`, `handle`, `dspace_title`, `doi`, `pure_uuid`, `pure_title`, `pure_record_dspace_uuids` |

The CSV files are only written when they have at least one row.

### Final Counts

At the end of a run, the processing log shows:

```
   Skipped (out of scope): …
     ↳ Not in Publications collection: …
     ↳ Dataset (not uploaded to Pure): …
     ↳ No contributors in any field: …
   Matched to existing Pure record: …
   Unmatched (new records created): …
   Successfully processed: …
   Failed (total): …
     ↳ No contributors matched to Pure persons: …
     ↳ Other errors: …
   Unmatched contributors: …
   Unmatched funders: …
   Unmatched publishers: …
   Unmatched journals: …
   DSpace UUID mismatches: …
   Publisher DOI conflicts: …
   Logs saved to: …
```

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

Additional fields appear only when relevant:

| Field | When |
|---|---|
| `duplicateResolution` | Several Pure records matched — how the one to update was chosen |
| `rejectedMatches` | Title or publisher-DOI candidates rejected by the safeguards — `pureUUID` and `reason` |
| `dspaceUuidResolution` | The Pure record was linked to a different DSpace item — which DSpace UUID was kept |
| `journalMatchedBy` | Journal found by `ISSN` or `title` (not needed for `journal_uuid`) |
| `journalCandidates` | Several journals qualified — all of their UUIDs |
| `typeChangedToOther` | New record downgraded to `OtherContribution` (no journal found) |
| `removedOrphanOrganizations` | Orphan record-level organisations removed — per list, the removed UUIDs |
| `unmatchedContributors` / `unmatchedFunders` | Contributors / funders that could not be matched |

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
| No journal found (new record) | ✅ (as OtherContribution) | ❌ | ❌ | `processing_log` warning; `unmatched_journals.csv`; `typeChangedToOther` in `status_log` |
| No journal found (existing record) | ✅ (type unchanged) | ❌ | ❌ | `processing_log` warning; `unmatched_journals.csv` |
| Updated record carries another DSpace item's UUID | ✅ | ❌ | ❌ | `processing_log` warning; `dspace_uuid_mismatches.csv` |
| Orphan organisations removed | ✅ | ❌ | ❌ | `processing_log`; `removedOrphanOrganizations` in `status_log` |
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

### No Journal Found

```
⚠️ No journal UUID found for ContributionToJournal - changing to OtherContribution
```

Printed for a new record when neither `journal_uuid`, ISSN nor title matching found a journal. The record is still created, but saved under `othercontribution_YYYY-MM-DD.json`, and listed in `unmatched_journals.csv` with the reason.

---

## Performance

Lookup indices are built at startup for fast processing:

- **Person index:** all name variations → O(1) lookup
- **Organization index:** normalized org names → O(1) lookup
- **Publisher index:** normalized publisher names → O(1) lookup
- **Pure record indices:** DOIs, handles, titles → O(1) matching
- **API caches:** person metadata and org validation results are cached after first fetch

Typical throughput: ~1,000 records in 5–10 minutes with API calls enabled.