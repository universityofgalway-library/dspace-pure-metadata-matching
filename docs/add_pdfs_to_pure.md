# add_pdfs_to_pure.py

Downloads PDFs from a DSpace repository, uploads them to Pure's file-upload endpoint, and immediately attaches them to the matching Pure research output as a `FileElectronicVersion`. It can also detect and resolve duplicate `FileElectronicVersion`s already present on a record. No fields outside `electronicVersions` are modified.

---

## Requirements

```
requests
python-dotenv
tqdm
python-dateutil
```

Install with:
```bash
pip install requests python-dotenv tqdm python-dateutil
```

---

## .env file

```env
PURE_ROOT_API_KEY_TEST=your_uat_key_here
PURE_ROOT_API_KEY=your_production_key_here
```

---

## Inputs

| Argument | Description |
|---|---|
| `--dspace-csv` | Path to the enriched DSpace CSV export. Must contain the columns listed below. |
| `--pure-json` | Path to a Pure research-outputs JSON export (list of record dicts). |

**Required DSpace CSV columns:**

| Column | Description |
|---|---|
| `collection_names` | Collection membership string. Only rows containing `Publications` are processed. |
| `pdf_handle_paths` | Semicolon-separated handle-based paths to PDFs, e.g. `/10379/4728/1/file.pdf` or `/10379/4728/1/cover.pdf ; /10379/4728/2/fulltext.pdf`. Rows without this value are skipped. All paths are processed. |
| `pdf_links` | Semicolon-separated direct download URLs for the PDFs, e.g. `https://…/bitstreams/uuid/content`. Positionally aligned with `pdf_handle_paths`. Used as the actual download source. |
| `handle` | The item handle, e.g. `http://hdl.handle.net/10379/4728`. Used as the primary handle source for matching and for the file references output. |
| `uuid` | DSpace item UUID. Used as the first matching strategy. |
| `dc.identifier.uri` | Semicolon-separated URIs (handles and DOIs). Used as fallback for matching when `handle` is empty. |
| `dc.identifier.doi` | Publisher DOI. Used as the second matching strategy. |
| `dc.rights` | Rights/license string (e.g. `CC BY`). Mapped to a Pure license URI. |
| `dc.date.embargo` | Embargo end date. Determines access type (`open` or `embargoed`). |
| `dc.description.embargo` | Alternative embargo end date field. |
| `dc.title` | Record title. Used for logging only. |

---

## Options

| Flag | Default | Description |
|---|---|---|
| `--test` / `--no-test` | `--test` | Use UAT (`--test`) or Production (`--no-test`) API. |
| `--source` | `dspace` | Where to get PDFs from: `dspace` (download from DSpace) or `local` (read from disk). |
| `--pdf-dir` | `./dspace_pdfs` | If `--source dspace`: directory used to stage PDFs temporarily during upload (or permanently when `--save-locally` is set). If `--source local`: directory to read PDFs from. Must exist when using `--source local`. |
| `--save-locally` | `False` | Only applicable when `--source dspace`. Keep downloaded PDFs on disk after upload instead of deleting them. Each file is written to `--pdf-dir` before being uploaded. If a file with the same name already exists locally it is reused rather than re-downloaded. |
| `--log-dir` | `./pdf_upload_logs` | Directory where all log files are written. |
| `--skip-existing` / `--no-skip-existing` | `--skip-existing` | Skip individual files that already exist in Pure as a `FileElectronicVersion` with the **same filename and size**. Filename comparison is performed after Pure-style normalization (non-alphanumeric characters other than hyphens, underscores, dots, and spaces are replaced with underscores). Applied per file when a record has multiple PDFs. If filename matches but size differs or cannot be determined, the file is uploaded and appended alongside the existing one. |
| `--dry-run` | `False` | Match records and report what would be done without making any API calls. |

---

## Usage

**Standard run against UAT (download from DSpace):**
```bash
python add_pdfs_to_pure.py \
  --dspace-csv ./dspace_data/export.csv \
  --pure-json  ./pure_research_outputs/outputs.json
```

**Production run, saving PDFs locally:**
```bash
python add_pdfs_to_pure.py \
  --dspace-csv ./dspace_data/export.csv \
  --pure-json  ./pure_research_outputs/outputs.json \
  --no-test \
  --save-locally \
  --pdf-dir ./downloaded_pdfs
```

**Upload from local directory instead of DSpace:**
```bash
python add_pdfs_to_pure.py \
  --dspace-csv ./dspace_data/export.csv \
  --pure-json  ./pure_research_outputs/outputs.json \
  --source local \
  --pdf-dir ./downloaded_pdfs
```

**Dry run to check matches before uploading:**
```bash
python add_pdfs_to_pure.py \
  --dspace-csv ./dspace_data/export.csv \
  --pure-json  ./pure_research_outputs/outputs.json \
  --dry-run
```

---

## How it works

For each DSpace row that has a `pdf_handle_paths` value, the script:

1. **Filters** to rows in a Publications collection (i.e. `collection_names` contains `Publications`) with a `pdf_handle_paths` value.
2. **Matches** the row to a Pure record using (in priority order): DSpace UUID → Publisher DOI → Repository DOI → Handle. The `handle` column is checked first for handle matching; `dc.identifier.uri` is used as a fallback. Lookup is O(1) via a pre-built index.
3. **Parses all PDF paths** from `pdf_handle_paths` (semicolon-separated). Each path is processed independently through the steps below.
4. **Checks for duplicates** (if `--skip-existing`): skips a file only if a `FileElectronicVersion` with the **same filename and size** already exists in Pure. Filename comparison is performed after Pure-style normalization so that characters substituted by Pure on ingest (e.g. commas) do not prevent a match. Size and name are read from the nested `file` object inside the `FileElectronicVersion` block (`file.fileName` and `file.size`). When a match is found, the script also checks whether the existing FileEV's metadata is up to date — specifically `licenseType`, `accessType`, `versionType`, `visibleOnPortalDate`, and `embargoPeriod`. If any field is missing or differs from the DSpace-derived values, the Pure record is PUTted with the corrected metadata without re-uploading the file. If size differs or is unknown, the file is uploaded and appended alongside the existing version.
5. **Resolves duplicate `FileElectronicVersion`s already on the record** (if `--skip-existing`, whenever step 4 finds an existing match): before PUTting, the script scans the *entire* record's `electronicVersions` for any other groups of `FileElectronicVersion`s that share the same `fileName` and `size` — not just the file currently being processed — and collapses each group down to a single entry. This piggybacks on the same PUT triggered by step 4, so no extra API calls are made; a record's duplicates are only cleaned up when at least one of its files is actually processed in the current run (a record with no matching DSpace row in this run's CSV is left untouched, even if it has duplicates).

   For each duplicate group, one entry is kept and the rest are removed, chosen in this priority order:
   1. **Most complete metadata** — the entry with the most of `licenseType`, `accessType`, `versionType`, `visibleOnPortalDate`, `embargoPeriod`, and `title` populated wins.
   2. **Uploaded by a real Pure user** — if metadata completeness ties, an entry whose `creator` is not a known system/import account wins. System/import accounts are: `root`, `atira`, `sync_user`, `admin`, `system`, and empty/missing (matched case-insensitively).
   3. **Fallback** — if still tied, the entry appearing first in the record's original `electronicVersions` order is kept.

   Entries are matched and replaced by their own identity (not by filename), so a record can safely hold several distinct files that happen to share a filename without one being mistaken for another during the update.
6. **Gets the PDF** — either downloads it from the direct bitstream URL in `pdf_links` (`--source dspace`) or reads it from `--pdf-dir` (`--source local`). When using `--source dspace`, the file is always written to `--pdf-dir` first and then read back from disk for the Pure upload; if `--save-locally` is not set the local copy is deleted after a successful upload. If a local copy already exists it is reused without re-downloading. When using `--source local`, filenames are matched using the URL-decoded form; if not found, the script also tries the URL-encoded variant as a fallback.
7. **Sanitizes filenames for disk** (Windows only): characters illegal in Windows filenames (`\ / : * ? " < > |`) are replaced with underscores in the on-disk copy. The original decoded filename is always used as the `fileName` stored in Pure and in all log outputs, so it matches the DSpace original exactly.
8. **Uploads** each PDF to Pure's temporary file-upload endpoint.
9. **PUTs** the Pure record immediately with the new `FileElectronicVersion` appended to `electronicVersions`.
10. **GETs** the Pure record after each successful PUT to retrieve the `fileId` and `fileName` assigned by Pure to the newly linked file. These are written to the `pdf_matched_records` CSV.

Steps 8 and 9 happen back-to-back for each file to stay well within Pure's **2-hour temporary file expiry window**. System/read-only fields (`pureId`, `createdBy`, `modifiedDate`, etc.) are stripped from the record before the PUT — including the `pureId` on each nested object, so existing and new `electronicVersions`/`file` associations are matched by their own object identity within the script, not resubmitted with a stale association ID.

Additionally, all Publications rows **without** a `pdf_handle_paths` value are matched against Pure and written to a separate `no_pdf_matched_records` CSV for reference.

---

## Logging

All log files are written to `--log-dir` and timestamped with the run start time.

| File | Contents |
|---|---|
| `run_<timestamp>.log` | Full console output for the entire run. |
| `results_<timestamp>.json` | One entry per processed DSpace row with all fields, status, and error detail. |
| `success_<timestamp>.csv` | Rows where the PDF was uploaded and the PUT succeeded. |
| `failed_<timestamp>.csv` | Rows where the PDF upload or PUT failed. |
| `skipped_<timestamp>.csv` | Rows with no Pure match, or already having a matching `FileElectronicVersion`. |
| `pdf_matched_records_<timestamp>.csv` | All DSpace rows matched to a Pure record that had PDFs, written continuously as processing proceeds. Each row is flushed to disk immediately, so the file is complete even if the run is interrupted. |
| `no_pdf_matched_records_<timestamp>.csv` | All DSpace rows in the Publications collection that had no `pdf_handle_paths` value but were successfully matched to a Pure record. Written before PDF processing begins. |

### pdf_matched_records CSV

| Column | Example | Description |
|---|---|---|
| `dspace_uuid` | `3f2a1b...` | DSpace item UUID. |
| `pure_uuid` | `a1b2c3...` | Pure record UUID. |
| `pure_id` | `12345678` | Pure internal numeric ID (`pureId`), captured before system fields are stripped. |
| `title` | `My paper title` | Research output title taken from the `dc.title` column of the DSpace CSV. |
| `dspace_file_id` | `/10379/4728/1/file.pdf` | The handle-based file path from `pdf_handle_paths` for this specific file. For rows where all files were skipped or failed as a batch, this contains the full semicolon-separated `pdf_handle_paths` value. |
| `pure_file_id` | `MDAxOTAxYjI5` | The `fileId` assigned by Pure to the linked file, retrieved via GET after a successful PUT. Empty if the PUT failed or if the GET could not find the file. For skipped files (already existed), taken from the existing `FileElectronicVersion` in the in-memory Pure record without an extra API call. |
| `pure_file_pure_id` | `98765` | Pure's internal numeric `pureId` for the file object, retrieved alongside `pure_file_id`. |
| `pure_file_name` | `my_paper.pdf` | The `fileName` as stored in Pure's `FileElectronicVersion.file` object. Corresponds to `pure_file_id`. |
| `handle` | `https://hdl.handle.net/10379/4728` | Item handle, prefixed with `https://hdl.handle.net/` if not already a full URL. |

### no_pdf_matched_records CSV

| Column | Example | Description |
|---|---|---|
| `dspace_uuid` | `3f2a1b...` | DSpace item UUID. |
| `pure_uuid` | `a1b2c3...` | Pure record UUID. |
| `pure_id` | `12345678` | Pure internal numeric ID (`pureId`). |
| `title` | `My paper title` | Research output title taken from the `dc.title` column of the DSpace CSV. |
| `handle` | `https://hdl.handle.net/10379/4728` | Item handle. |

### Status values

| Status | Meaning |
|---|---|
| `success` | All PDFs uploaded and Pure record updated. |
| `partial_success` | At least one PDF uploaded successfully, but one or more failed. |
| `metadata_updated` | File already existed with the same filename and size. Covers two (possibly combined) changes made via the same PUT: one or more metadata fields (license, access type, version type, visible on portal date, or embargo period) were missing or out of date and have been corrected, and/or duplicate `FileElectronicVersion`s were found on the record and collapsed down to one per group (see "Resolves duplicate FileElectronicVersions" in How it works). The `detail` field in `results_<timestamp>.json` distinguishes which of the two occurred. |
| `no_match` | No Pure record could be matched to this DSpace row. |
| `skipped_existing_fev` | All files for this row already exist in Pure with the same filename and size, and metadata is up to date. |
| `pdf_upload_failed` | All PDF uploads failed (not found locally, or download/upload error). |
| `put_failed` | File uploaded but the subsequent PUT to Pure failed. |
| `dry_run` | Dry-run mode — no action taken. |

---

## Notes

- The script only modifies `electronicVersions`. All other fields on the Pure record are preserved as-is.
- Re-runs are safe when `--skip-existing` is on — files with an identical filename and size already in Pure will be skipped on a per-file basis.
- Duplicate `FileElectronicVersion` resolution (see step 5 in How it works) only runs when `--skip-existing` is on, and only for records where at least one file in the current run's DSpace CSV already matches an existing entry. It will not proactively clean up duplicates on records that have no corresponding row being processed in that run.
- The run summary (printed at the end and in `run_<timestamp>.log`) includes a `Duplicate FileEVs removed` count, separate from `Only file metadata updated`.
- If a PUT fails after a successful upload, the uploaded file will be orphaned in Pure and deleted automatically after 2 hours. The failed row is written to `failed_<timestamp>.csv` for manual follow-up.
- The Pure JSON input does not need to be regenerated between runs; the script reads it once at startup.
- Filenames saved to disk and sent to Pure are always URL-decoded. The original encoded paths from `pdf_handle_paths` are preserved as-is in `dspace_file_id` in all log outputs.
- On Windows, characters illegal in filenames (`\ / : * ? " < > |`) are silently replaced with underscores in the on-disk copy only. The original filename is preserved in Pure and in all logs. A warning is printed when sanitization occurs.