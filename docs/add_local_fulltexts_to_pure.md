# add_local_fulltexts_to_pure.py

Uploads full texts that are missing from DSpace, but were found elsewhere and saved to local storage, to the matching Pure records. Only files that passed the manual check are uploaded: rows with **Correct = Y / Yes / True** in the metadata sheets.

This script is standalone. The helper functions it shares with `add_pdfs_to_pure.py` are copied unchanged, so both scripts use the same rules:
- record matching
- file-name comparison
- the PDF check
- cleaning the record before the PUT

Moving them into a shared module is left for later.

---

## Requirements

- Python 3.10+
- `requests`, `python-dotenv`, `tqdm`, `python-dateutil` (the same as `add_pdfs_to_pure.py`)
- LibreOffice (`soffice`), only needed if any correct row points to a `.doc` / `.docx` file
- A `.env` file in the working directory:

```
PURE_ROOT_API_KEY_TEST=...   # UAT (--test)
PURE_ROOT_API_KEY=...        # Production and TEMP (--temp)
```

---

## Inputs

| Input | Option | Notes |
|---|---|---|
| Project folder | `--root` | Holds the metadata sheets and the download folders, as delivered. Nothing has to be renamed or moved. |
| DSpace export | `--dspace-csv` | Same export as for `add_pdfs_to_pure.py` (e.g. `enriched_dspace_prod_items_2026-10-05.csv`). |
| Pure export | `--pure-json` | Research-outputs JSON (a list of records). |

### Project folder

```
<root>/
  DSpace-Fulltexts-0001-1000_with_fulltext.csv    <- read
  DSpace-Fulltexts-0001-1000_with_fulltext.xlsx   <- ignored (only .csv is read)
  ...
  README.txt, Work summary Ella.docx              <- ignored
  Full-Text-Downloads-0002-1000-Atmire/           <- files for sheet 0001-1000
  Full-Text-Downloads-0002-1000-CJ/               <- files for sheet 0001-1000
  Full-Text-Downloads-Ella-0002-1000/             <- files for sheet 0001-1000
  Full-Text-Downloads-CJ-2000-3000/               <- files for sheet 2000-3000
  Fulll-Text-Downloads-4000-4999-Atmire/          <- files for sheet 4000-4999
  Full-Text-Downloads-Ella-misc/                  <- searched for every sheet
```

**Which files belong to which sheet.** The number range is read from each name: the last `<number>-<number>` in it. Prefixes and suffixes (Atmire, CJ, Ella, the `Fulll` typo) don't matter.
- **Folders with a range:** a sheet's files are looked for in every folder whose range **overlaps** the sheet's range. Overlap rather than an exact match is needed because the first batch's folders say `0002-1000` while its sheet says `0001-1000`. The sheet ranges don't overlap each other, so every folder belongs to exactly one sheet.
- **Folders without a range** (`Full-Text-Downloads-Ella-misc`) are searched for every sheet.
- **Sub-folders** inside a download folder are searched too.

**What gets reported.** The log lists every folder with its range and the folders each sheet uses (also in `sheets_<ts>.csv`). It also warns about:
- a folder that belongs to no sheet (it is not searched)
- a folder that belongs to several sheets
- a CSV with no range in its name (it is ignored)

With the folder names in your screenshot, the mapping comes out as:

| Sheet | Folders searched |
|---|---|
| 0001-1000 | `0002-1000-Atmire`, `0002-1000-CJ`, `Ella-0002-1000`, `Ella-misc` |
| 1001-1999 | `1001-1999-Atmire`, `1001-1999-CJ`, `Ella-1001-1999`, `Ella-misc` |
| 2000-3000 | `2000-3000-Atmire`, `CJ-2000-3000`, `Ella-misc` |
| 3001-3999 | `3001-3999-Atmire`, `CJ-3001-3999`, `Ella-misc` |
| 4000-4999 | `Fulll-…-4000-4999-Atmire`, `CJ-4000-4999`, `Ella-misc` |
| 5000-6183 | `5000-6183-Atmire`, `CJ-5000-6183`, `Ella-misc` |

**Same name in several of a sheet's folders.** This can happen, for example when Atmire and CJ both downloaded the same file.
- **Identical content** (checked by SHA-256): the first copy is used, and the log says so.
- **Different content:** the row fails with `file_not_found`, and the detail lists the paths, so you can resolve it by hand.

A file in another batch's folder is **not** used.

The log and converted-PDF folders, any top-level folder that contains them (such as `logs/`), and hidden folders are never treated as download folders. The default log folder and converted-PDF folder are created in the current working directory. If you run the script from inside the project folder, they are excluded automatically.

### Metadata sheets

The script reads only the `*.csv` files at the top level of the project folder. Column names are matched case-insensitively, and surrounding spaces are ignored.

| Column | Use |
|---|---|
| `id` | DSpace item UUID |
| `dc.identifier.doi[]` (or `dc.identifier.doi`) | Publisher DOI, used as a back-up match. Several values may be separated by `||` or ` ; `. |
| `Filename` | Name of the local file |
| `Correct` | Result of the manual check. `Y`, `Yes` and `True` (any case, spaces ignored) mean upload; anything else means don't. |
| `Full text Source`, `WORKING Full text link` | Used only for the Sci-Hub check (see below). The source is also copied to the log. |

**Ignored column:** `Download status`.

**Reading the sheets:**
- **Delimiter:** comma, semicolon or tab, detected automatically.
- **Encoding:** UTF-8 (with or without BOM), falling back to Windows-1252.
- **Other CSV files:** a CSV without the `id`, `Filename` and `Correct` columns is reported as "not a metadata sheet" and ignored.

### How `Filename` is resolved

Each of these is tried in order:
1. the cell as written (trimmed)
2. its last path component, so a stray leading `/` is ignored (`/2026-05-19-10.1093_JSCR_RJW069.pdf`)
3. the percent-decoded form (`%20` → space)

The same three are then tried ignoring case.

---

## Usage

```bash
# 1. Always start with a dry run
python add_local_fulltexts_to_pure.py \
    --root "/path/to/Full-Text project folder" \
    --dspace-csv enriched_dspace_prod_items_2026-10-05.csv \
    --pure-json pure_research_outputs.json \
    --test --dry-run

# 2. Real run on UAT
python add_local_fulltexts_to_pure.py ... --test

# 3. Production
python add_local_fulltexts_to_pure.py ...
```

### Options

| Option | Default | Meaning |
|---|---|---|
| `--test` | off | UAT (`galway-staging`), key `PURE_ROOT_API_KEY_TEST` |
| `--temp` | off | TEMP (`galway-test`), key `PURE_ROOT_API_KEY` |
| `--base-url URL` | – | Use another Pure API base URL (must end with `/ws/api/`). Used for testing. |
| `--dry-run` | off | Do everything except upload and PUT. **No Pure API calls** are made, so the existing-file check uses the export. `.doc`/`.docx` files *are* converted, because the PDF's size is needed for the check. |
| `--refresh-from-pure` / `--no-refresh-from-pure` | **on** | Re-read each matched record from Pure (GET) before checking and updating it. On by default, because the sheets, the export and Pure date from different times. Each record is read once per run. |
| `--allow-sci-hub` | off | Also upload rows whose `Full text Source` or `WORKING Full text link` mentions Sci-Hub. By default they are skipped (see step 1). |
| `--include-all-collections` | off | Also process DSpace items outside the *Publications* collection, and of types `dataset`, `doctoral thesis` or `master thesis`. By default these are skipped, as in `add_pdfs_to_pure.py`. |
| `--converted-dir DIR` | `./converted_pdfs` | Where PDFs converted from `.doc`/`.docx` are kept. |
| `--soffice PATH` | found on `PATH` | LibreOffice executable. |
| `--log-dir DIR` | `./logs/local_fulltext_upload_logs/test` with `--test`, `.../temp` with `--temp`, otherwise `.../prod` | Log folder (created if missing). A `--base-url` run without `--test`/`--temp` goes to `prod`. |

---

## What happens to each row

Rows are processed in sheet order. Sheets are read in alphabetical path order. Only rows marked correct are processed.

1. **Repeated or conflicting rows.** Rows are keyed by DSpace UUID and file name.
   - A second row with the same key is skipped as `duplicate_row`. The log names the first row.
   - If the same file is also marked **N / No / False** in any sheet, including with a comment after it ("N similar but not same"), the row is skipped as `conflicting_rows`.
   - **Sci-Hub:** if `Full text Source` or `WORKING Full text link` mentions Sci-Hub (any spelling: `Sci-Hub`, `SciHub`, `sci-hub.se` links …), the row is skipped as `skipped_sci_hub_source`. The project README says Sci-Hub must not be the basis for ingest, and that a legitimate open-access source should be recorded before a row is approved. A row approved with Sci-Hub still listed may therefore not have had its open-access status confirmed. Record the legitimate source in the sheet, or run with `--allow-sci-hub` once you have confirmed these rows.
2. **DSpace item.** The row is joined by UUID to the DSpace export, to get the title, Handle, collection, type and embargo.
   - **Collection and type:** items outside *Publications*, or of an excluded type, are skipped.
   - **Embargo:** items with an **active DSpace embargo** are skipped (`skipped_active_embargo`). Uploading them as open access would break the embargo. No correct row in the current sheet has one.
   - **UUID not in the export:** the row is still matched, but by UUID only. The publisher-DOI back-up needs the title.
3. **Local file.** The file is found by name in the sheet's download folders and then checked by type:
   - **`.pdf`:** used as is.
   - **`.doc` / `.docx`:** converted to PDF with LibreOffice. The PDF is uploaded as `<same name>.pdf` and kept in `--converted-dir`. A later run reuses the kept PDF instead of converting again, so it compares exactly the same bytes and size against Pure.
   - **Anything else:** fails with `unsupported_file_type`.
   - **PDF check:** the file to upload must be larger than 1 KB and contain `%PDF` in its first 1 KB. This is the same check as `add_pdfs_to_pure.py`, and it catches HTML pages saved as `.pdf`.
4. **Pure record.** This uses `find_pure_record` from `add_pdfs_to_pure.py`, unchanged.
   - **DSpace UUID:** first, a Pure record whose `identifiers` include `idSource: "DSpace"` with this UUID.
   - **Back-up, only for Pure records without any DSpace UUID:**
     - Handle, then repository DOI (10.13025), both taken from the DSpace export.
     - Then the publisher DOI, taken from the sheet and the DSpace export. This step also requires an identical title (title + subtitle, case and punctuation ignored, numbers kept).
   - **Several Pure records found:** the row is skipped as `ambiguous_match`.
5. **Refresh.** The record is re-read from Pure, unless `--no-refresh-from-pure` or `--dry-run` is given.
6. **Is the file already on the record?** This compares the file to upload (the converted PDF for Word files) with every file already on the record:

   | Existing file | Result |
   |---|---|
   | same name **and** same size | skip: `skipped_same_name_and_size` |
   | different name, **exactly** the same size | skip: `skipped_same_size` |
   | same name, Pure reports no size | skip: `skipped_same_name_size_unknown` (cannot be ruled out) |
   | anything else | upload |

   "Same name" uses `same_file_name` from `add_pdfs_to_pure.py`: case, accents and punctuation are ignored, and Pure's `_NNN_` garbled names are recognised. For a converted file, both the original `.docx` name and the `.pdf` name are compared. Every skip goes into the log with the existing file's name, size and fileId.
7. **Upload and link.** The file is uploaded to `research-outputs/file-uploads` (as `application/pdf`). The record is then PUT straight away, because an upload expires after 2 hours. The PUT adds one new `FileElectronicVersion`:
   - **Version:** Accepted author manuscript (`versiontype/authorsversion`)
   - **Licence:** CC BY (`licenses/cc_by`)
   - **Access:** Open (`openaccesspermission/open`)
   - **Visible on portal:** today
   - **File name:** the local file name (or `<stem>.pdf` for converted files)

   **Existing files are not changed.** `add_pdfs_to_pure.py` resets every file on the record to CC BY and updates existing files' metadata; this script does neither. The record is cleaned as in `add_pdfs_to_pure.py` (system fields and nulls removed) and the other electronic versions are sent back as they were.
8. After a successful PUT, the record is read from Pure again. This logs the new file's `fileId`, and lets later rows for the same record (e.g. a second file) see the current state.

---

## Statuses

| Status | Group | Meaning |
|---|---|---|
| `success` | success | Uploaded and linked |
| `dry_run` | success | Would be uploaded |
| `skipped_same_name_and_size` | skipped | Same name and size already in Pure |
| `skipped_same_size` | skipped | Another name, exactly the same size already in Pure |
| `skipped_same_name_size_unknown` | skipped | Same name in Pure, size unknown |
| `duplicate_row` | skipped | Same UUID and file in an earlier row |
| `conflicting_rows` | skipped | Same file also marked not correct |
| `skipped_sci_hub_source` | skipped | Full-text source is Sci-Hub (see `--allow-sci-hub`) |
| `skipped_dspace_collection` | skipped | DSpace item not in *Publications* |
| `skipped_dspace_type` | skipped | Excluded DSpace type |
| `skipped_active_embargo` | skipped | Active embargo in DSpace |
| `no_match` | skipped | No Pure record (the detail says if a DOI matched with another title) |
| `ambiguous_match` | skipped | Several Pure records match |
| `file_not_found` | failed | No local file in the sheet's folders (the folders searched are listed), or several different files with this name |
| `unsupported_file_type` | failed | Not `.pdf`, `.doc` or `.docx` |
| `conversion_failed` | failed | LibreOffice missing or failed |
| `invalid_pdf` | failed | Fails the PDF check |
| `refresh_failed` | failed | GET of the record failed |
| `upload_failed` | failed | File upload failed |
| `put_failed` | failed | PUT failed (the upload expires unused) |

## Output (`--log-dir`, one set per run)

| File | Contents |
|---|---|
| `run_<ts>.log` | Everything printed, row by row, plus the summary |
| `results_<ts>.csv` | One line per processed row, **written as it goes**, so it survives an interrupted run |
| `results_<ts>.json` | The same, as JSON |
| `success_<ts>.csv`, `skipped_<ts>.csv`, `failed_<ts>.csv` | The results split by group (see table above) |
| `sheets_<ts>.csv` | Every CSV found: its row count, number of correct rows and the download folders searched for it, or why it was ignored |

**Columns:**
- **Row:** `sheet`, `sheet_line`, `dspace_uuid`, `publisher_doi`, `sheet_filename`, `full_text_source`, `local_path`, `converted_from`, `upload_file_name`, `file_size`
- **Matched items:** `title`, `handle`, `pure_uuid`, `pure_id`, `match_type`
- **Pure file:** `existing_pure_file` (for skips), `upload_key`, `pure_file_id`, `pure_file_pure_id`, `pure_file_name`
- **Outcome:** `status`, `detail`, `timestamp`

Rows not marked correct are counted in the summary, but are not listed individually.

**Re-running is safe.** Files uploaded earlier are found by name and size and skipped. Failed rows can simply be retried with the next run.

---

## Findings from the current data

These come from `DSpace-Fulltexts-0001-1000_with_fulltext.csv` and `enriched_dspace_prod_items_2026-10-05.csv`:

- **Format:** the sheet is comma-separated with CRLF line endings, and has 6 empty trailing columns. One of those holds a stray URL, which is ignored.
- **`Correct` values:**
  - It has 12 distinct values, including `"Y  "`, `"y"`, `"N "` and `"N similar but not same"`.
  - After trimming, 650 of 999 rows are correct.
- **File types:** all 650 correct rows are `.pdf`. The 4 Word files and 2 `.bin` files in the sheet are all on rows that are not marked correct.
- **DSpace export:** all 650 correct UUIDs are in it. Their sheet DOIs equal the export DOIs once normalised. None has an embargo.
- **7 correct rows are in the DSpace "Duplicates" collection**, and are skipped by default. Use `--include-all-collections` to process them. A Duplicates item is probably not in Pure under its own UUID, and its DOI only reaches Pure records with no DSpace link. These 7 are worth a manual look:
  - `0d12a713-cabc-485c-a7e6-7cf97109f498`
  - `114135ee-2fe9-4810-9813-55df62da8d77`
  - `1bf396de-369e-4675-bffb-6d2480dc7255`
  - `29642ccf-236d-4b87-8898-f2187dfa13a2`
  - `322524c8-0502-4ea0-9bab-8bd0a39db949`
  - `338cef9e-383a-40e4-866d-a348a2f4bf7c`
  - `356c2c26-b585-4442-8502-1ae86ea1bca4`
- **Unusual file names, handled as described above:**
  - a leading `/` (`/2026-05-19-10.1093_JSCR_RJW069.pdf`)
  - a literal `%20` (`2026-05-19-%2010.1038_srep46731.pdf`)
  - `pdf: `
  - 43 empty names, all on rows not marked correct
- **Name collisions:** the file names are unique within the sheet (apart from the empty ones).
- **Sci-Hub:** 8 correct rows have `Full text Source = Sci-Hub`, and 9 have a Sci-Hub link. They are skipped by default (see step 1).

## Testing

The script was tested end to end against a mock Pure API, using the real sheet and the real DSpace export. The local files and Pure records were synthetic. The test project folder copies your layout: sheets `0001-1000` and `1001-1999`, plus an `.xlsx`, `README.txt`, `Work summary Ella.docx`, a CSV without a range, and folders `0002-1000-Atmire`, `0002-1000-CJ`, `Ella-0002-1000`, `1001-1999-CJ`, `Ella-misc` and `Fulll-…-4000-4999-Atmire` (which has no sheet). All 54 checks passed. They covered:

- **Project layout:**
  - the folder-to-sheet mapping, including the `0002-1000` / `0001-1000` overlap
  - `Ella-misc` searched for every sheet
  - files in sub-folders found
  - a file that sits only in another batch's folder is *not* found
  - a folder without a sheet, and a CSV without a range, are reported
  - the `.xlsx` and `.docx` in the project folder are ignored
- **Sheet parsing:** the real sheet, and `Yes` / `TRUE ` / `y`.
- **Record matching:** by DSpace UUID; by publisher DOI with the same title; publisher DOI with another title (no match); two Pure records with one UUID (ambiguous); and a UUID missing from the export.
- **Local file checks:** a leading `/` in the name; the same file in two folders of one batch; a missing file; HTML saved as `.pdf`; and `.bin`.
- **Word conversion:** a `.docx` converted to PDF and uploaded as a second file on the same record. A `.doc` was converted in a separate dry run.
- **Row filters:** the Duplicates collection, an active embargo, repeated and Y/N-conflicting rows, and a Sci-Hub source (skipped by default, processed with `--allow-sci-hub`).
- **Skip rules:** same name and size; same size with another name, found only through the refresh because the export was stale.
- **The PUT:**
  - new files are CC BY, open access, Accepted author manuscript;
  - an existing CC BY-NC-ND publisher's-version file stays unchanged;
  - system fields are stripped;
  - uploads are sent as `application/pdf`.
- **Failures:** a failed PUT, and a wrong API key (upload fails with HTTP 401).
- **Re-running:** a second run uploads nothing and reuses the converted PDF.

The mapping was also checked separately against all 15 folder names and 6 sheet names from your screenshot.

**Not tested against a real Pure instance.** Run with `--test --dry-run` first, then `--test`, and check the result in the Pure UI.

## Known limitations

- **Existing files are sent back as in `add_pdfs_to_pure.py`.** The PUT body carries the existing electronic versions with nested `pureId`s removed, exactly as `add_pdfs_to_pure.py` does. That behaviour is copied, not changed.
- **No duplicate cleanup.** The duplicate-file cleanup and metadata repair from `add_pdfs_to_pure.py` are deliberately not included. Existing files are never touched.
- **`skipped_same_size` is a coarse test.** Two different papers of exactly the same byte size would be treated as the same file. That is what was asked for, and every such skip is logged with the existing file, so it can be reviewed.
- **The DOI back-up needs the title.** It works only for items in the DSpace export, and only reaches Pure records that have no DSpace UUID.
