# Scripts for DSpace-Pure Metadata Integration

| Script | Docs | Description |
|---|---|---|
| [create_sample.py](scripts/create_sample.py) | [create_sample.md](docs/create_sample.md) | |
| [delete_records.py](scripts/delete_records.py) | [delete_records.md](scripts/delete_records.md) | Deletes records from Pure by uuid. |
| [enrich_author_json.py](scripts/enrich_author_json.py) | [enrich_author_json.md](docs/enrich_author_json.md) | Injects UUIDs from a `pure_uploader.py` log into an authors JSON file. For each author who is neither internal nor external, it looks for an exact name match in an uploader log and, on a hit, sets `external: true` and appends the UUID to `externalUUIDs`. |
| [enrich_csv.py](scripts/enrich_csv.py) | [enrich_csv.md](docs/enrich_csv.md) | Enriches a target DSpace CSV file with journal and publisher data. |
| [extract_dspace_authors.py](scripts/extract_dspace_authors.py) | [extract_dspace_authors.md](docs/extract_dspace_authors.md) | Extracts author names and their associated publications from DSpace CSV exports. |
| [extract_pure_organizations.py](scripts/extract_pure_organizations.py) | [extract_pure_organizations.md](docs/extract_pure_organizations.md) | |
| [get_ids.py](scripts/get_ids.py) | [get_ids.md](docs/get_ids.md) | |
| [get_pure_data.py](scripts/get_pure_data.py) | [get_pure_data.md](docs/get_pure_data.md) | |
| [match_authors.py](scripts/match_authors.py) | [match_authors.md](docs/match_authors.md) | Matches author data from DSpace and Pure, using robust name matching, unique identifiers (ORCID, ScopusID), and publication information. |
| [match_records.py](scripts/match_records.py) | [match_records.md](docs/match_records.md) | Matches records from DSpace and Pure and creates two sets of JSON files for upload to Pure: records to update and new records to create. |
| [patch_records.py](scripts/patch_records.py) | [patch_records.md](docs/patch_records.md) | A unified command-line tool for cleaning and patching Pure research output JSON records. |
| [prepare_uploads.py](scripts/prepare_uploads.py) | [prepare_uploads.md](docs/prepare_uploads.md) | A command-line tool for preparing entity records to upload to Pure (authors, funders, publishers, journals). |
| [pure_uploader.py](scripts/pure_uploader.py) | [pure_uploader.md](docs/pure_uploader.md) | Uploads JSON records to Pure via its REST API. Supports creating new records and updating existing ones, across multiple data types, in either the staging (UAT) or production environment. Provides detailed logging. |
    
