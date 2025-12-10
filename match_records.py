import csv
import json
import os
import re
import sys
import requests
from datetime import datetime
from collections import defaultdict
from tqdm import tqdm
from dotenv import load_dotenv


# --- CONFIGURATION ---

# Load environment variables from .env file
load_dotenv()

DSPACE_CSV = "./matching_test/dspace_test_sample.csv"
PURE_JSON = "./matching_test/research_outputs/research_outputs_2025-11-20_all.json"
PERSON_MAPPING_JSON = "./matching_test/matched_authors/test_authors_all.json"
OUTPUT_DIR = "./matching_test/test_output"
MATCHED_DIR = os.path.join(OUTPUT_DIR, "matched")
UNMATCHED_DIR = os.path.join(OUTPUT_DIR, "unmatched")
LOG_DIR = os.path.join(OUTPUT_DIR, "logs")
API_KEY = os.getenv("PURE_API_KEY", "")

if not API_KEY:
    print("⚠️ WARNING: PURE_API_KEY not found in environment variables.")
    print("   External person duplicate resolution will be skipped.")

os.makedirs(MATCHED_DIR, exist_ok=True)
os.makedirs(UNMATCHED_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)

TODAY = datetime.now().strftime("%Y-%m-%d")

# --- TYPE MAPPING ---
dspace_pure_subtype_map = {
    "journal article": "/dk/atira/pure/researchoutput/researchoutputtypes/contributiontojournal/article",
    "review article": "/dk/atira/pure/researchoutput/researchoutputtypes/contributiontojournal/systematicreview",
    "review": "/dk/atira/pure/researchoutput/researchoutputtypes/contributiontojournal/systematicreview",
    "doctoral thesis": "/dk/atira/pure/researchoutput/researchoutputtypes/thesis/doc",
    "master thesis": "/dk/atira/pure/researchoutput/researchoutputtypes/thesis/master",
    "conference paper": "/dk/atira/pure/researchoutput/researchoutputtypes/contributiontoconference/paper",
    "conference output": "/dk/atira/pure/researchoutput/researchoutputtypes/contributiontoconference/other",
    "conference poster": "/dk/atira/pure/researchoutput/researchoutputtypes/contributiontoconference/poster",
    "book part": "/dk/atira/pure/researchoutput/researchoutputtypes/contributiontobookanthology/chapter",
    "book": "/dk/atira/pure/researchoutput/researchoutputtypes/bookanthology/book",
    "report": "/dk/atira/pure/researchoutput/researchoutputtypes/bookanthology/commissioned",
    "conference proceedings": "/dk/atira/pure/researchoutput/researchoutputtypes/bookanthology/book",
    "working paper": "/dk/atira/pure/researchoutput/researchoutputtypes/workingpaper/workingpaper",
    "video": "/dk/atira/pure/researchoutput/researchoutputtypes/nontextual/audiovisual_material",
    "interactive resource": "/dk/atira/pure/researchoutput/researchoutputtypes/nontextual/web_publication",
    "newspaper article": "/dk/atira/pure/researchoutput/researchoutputtypes/contributiontoperiodical/article",
    "book review": "/dk/atira/pure/researchoutput/researchoutputtypes/contributiontoperiodical/book",
    "other": "/dk/atira/pure/researchoutput/researchoutputtypes/othercontribution/other",
    "data management plan": "/dk/atira/pure/researchoutput/researchoutputtypes/othercontribution/other",
}

# --- LOGGER SETUP --- #

class LoggerOutput:
    """Write to both stdout and a file"""
    def __init__(self, file_path):
        self.terminal = sys.stdout
        self.log = open(file_path, 'w', encoding='utf-8')
        
    def write(self, message):
        self.terminal.write(message)
        self.log.write(message)
        self.log.flush()  # Ensure immediate write
        
    def flush(self):
        self.terminal.flush()
        self.log.flush()
        
    def close(self):
        self.log.close()
        

# --- HELPER FUNCTIONS ---

def normalize(s):
    return s.strip().lower() if s else ""

def has_text_in_any_language(obj, key, languages=["en_GB", "en_IE", "en_US"]):
    """Check if object has non-empty text in any of the given languages"""
    return any(obj.get(key, {}).get(lang, "").strip() for lang in languages)

def escape_special_chars(text):
    """Replace special characters with HTML entity codes"""
    if not text:
        return text
    
    # Mapping of reserved HTML characters
    replacements = {
        '<': '&lt;',
        '>': '&gt;',
        '&': '&amp;'
    }
    
    result = text
    # Replace & first to avoid double-encoding other entities
    if '&' in result and not result.startswith('&'):
        result = result.replace('&', '&amp;')
    
    # Replace other characters
    for char, entity in replacements.items():
        if char != '&':  # Skip & since we already handled it
            result = result.replace(char, entity)
    
    return result

def extract_uuid(uuid_entry):
    return uuid_entry["uuid"] if isinstance(uuid_entry, dict) else uuid_entry

def extract_dois_from_uri(uri_str):
    """Extract DOIs from dc.identifier.uri (semicolon-separated)"""
    if not uri_str:
        return []
    uris = [u.strip() for u in uri_str.split(";") if u.strip()]
    dois = []
    for u in uris:
        # Match DOI pattern
        match = re.search(r'(?:https?://doi\.org/|doi:)([^\s<>"{}|^`\\[\]]+)', u, re.IGNORECASE)
        if match:
            doi = match.group(1).strip()
            if not doi.startswith("10."):
                continue
            dois.append(doi)
    return dois

def extract_handles_from_uri(uri_str):
    """Extract handles from dc.identifier.uri"""
    if not uri_str:
        return []
    uris = [u.strip() for u in uri_str.split(";") if u.strip()]
    handles = []
    for u in uris:
        match = re.search(r'(?:https?://hdl\.handle\.net/|handle:)([^\s<>"{}|^`\\[\]]+)', u, re.IGNORECASE)
        if match:
            handle = match.group(1).strip()
            handles.append(handle)
    return handles

def get_pure_type_key(pure_type_uri):
    """Returns last but one element: e.g., 'contributiontojournal'"""
    if not pure_type_uri:
        return "unknown"
    parts = pure_type_uri.split("/")
    if len(parts) >= 3:
        return parts[-2]
    return "unknown"

def type_requires_peer_review(type_discriminator):
    """Check if a research output type requires the peerReview field"""
    types_without_peer_review = {
        "WorkingPaper",
        "ContributionToPeriodical",
        "Thesis",
        "Memorandum"
    }
    return type_discriminator not in types_without_peer_review

def get_default_peer_review_status(type_discriminator):
    """Get default peer review status for types that require it"""
    # Types typically peer-reviewed
    typically_peer_reviewed = {
        "ContributionToJournal",
        "ContributionToBookAnthology",
        "BookAnthology"
    }
    return type_discriminator in typically_peer_reviewed

def add_type_specific_fields(record, dspace_row):
    """Add type-specific required fields based on typeDiscriminator"""
    type_disc = record["typeDiscriminator"]
    
    # Add peerReview if required for this type
    if type_requires_peer_review(type_disc):
        record["peerReview"] = get_default_peer_review_status(type_disc)
        
    if type_disc == "ContributionToJournal" or type_disc == "ContributionToPeriodical":
        record["journalAssociation"] = {
                "journal": {
                    "systemName": "Journal",
                    "uuid": "f0da45fc-fec1-42f5-80a9-c1446ccce300"  # Placeholder UUID for TEST JOURNAL (UAT)
                    }
        }
        
    if type_disc == "ContributionToBookAnthology":
        record["hostPublicationTitle"] = {
            "value": "-"
        }
    
    return record

def parse_author_names(author_str):
    """Parse semicolon-separated author names from DSpace"""
    if not author_str:
        return []
    return [a.strip() for a in author_str.split(";") if a.strip()] 

def find_person_match(person_name, person_mapping):
    """Find matching person by name (primary + alternatives)"""
    first, last = "", ""
    if "," in person_name:
        parts = [p.strip() for p in person_name.split(",", 1)]
        last = parts[0]
        first = parts[1] if len(parts) > 1 else ""
    else:
        # Assume "First Last"
        parts = person_name.split()
        if len(parts) >= 2:
            first = " ".join(parts[:-1])
            last = parts[-1]
        else:
            first = person_name
            last = ""

    matches = []
    for person in person_mapping:
        # Check primary name, try both orders: (first, last) and (last, first)
        p_first = person.get("firstName", "")
        p_last = person.get("lastName", "")

        # Try direct match: DSpace "Last, First" > Pure "First Last"
        if normalize(p_first) == normalize(first) and normalize(p_last) == normalize(last):
            matches.append(person)
            continue

        # Try swapped match: DSpace "Last, First" > Pure "Last, First" (if Pure has it reversed)
        if normalize(p_first) == normalize(last) and normalize(p_last) == normalize(first):
            matches.append(person)
            continue

        # Check alternative names
        alt_firsts = person.get("alternativeFirstName", [])
        alt_lasts = person.get("alternativeLastName", [])
        for af in alt_firsts:
            for al in alt_lasts:
                if normalize(af) == normalize(first) and normalize(al) == normalize(last):
                    matches.append(person)
                    break
                # Also try swapped
                if normalize(af) == normalize(last) and normalize(al) == normalize(first):
                    matches.append(person)
                    break

    return matches

import requests

def resolve_author_duplicate(matches):
    """Prefer Person over External Person, then by visibility (internal), then by metadata richness (both internal and external)"""
    if not matches:
        return None

    # Sort by: internal > external, then by visibility (for internal), then by metadata richness (for both)
    def score(person):
        internal = person.get("internal", False)
        external = person.get("external", False)
        # Prefer internal
        type_score = 2 if internal else (1 if external else 0)

        # For internal: prefer FREE or CAMPUS
        vis_score = 0
        if internal:
            internal_uuids = person.get("internalUUIDs", [])
            if internal_uuids and isinstance(internal_uuids, list) and len(internal_uuids) > 0:
                # Handle dict format with visibility
                if isinstance(internal_uuids[0], dict):
                    vis = internal_uuids[0].get("visibility", "")
                    if vis in ["FREE", "CAMPUS"]:
                        vis_score = 2
                    elif vis in ["BACKEND", "CONFIDENTIAL"]:
                        vis_score = 1

        # For both internal and external: fetch full record for each UUID and pick the most complete
        metadata_score = 0
        
        if internal:
            internal_uuids = person.get("internalUUIDs", [])
            if internal_uuids:
                best_uuid = None
                max_fields = -1
                for uuid_obj in internal_uuids:
                    uuid_value = extract_uuid(uuid_obj)
                    try:
                        response = requests.get(
                            f"https://galway-staging.elsevierpure.com/ws/api/persons/{uuid_value}",
                            headers={
                                "accept": "application/json",
                                "api-key": API_KEY  
                            },
                            timeout=10
                        )
                        if response.status_code == 200:
                            internal_person = response.json()
                            # Count non-empty fields (excluding system ones)
                            field_count = sum(1 for k, v in internal_person.items() if k not in ["uuid", "createdBy", "modifiedBy", "version", "portalUrl", "prettyUrlIdentifiers", "previousUuids"] and v)
                            if field_count > max_fields:
                                max_fields = field_count
                                best_uuid = uuid_value 
                    except Exception as e:
                        # Log error but continue
                        print(f"⚠️ Failed to fetch internal person {uuid_value}: {e}")
                # Don't modify the person object; just score it
                metadata_score = max_fields if best_uuid else 0
        
        elif external:
            external_uuids = person.get("externalUUIDs", [])
            if external_uuids:
                best_uuid = None
                max_fields = -1
                for uuid_value in external_uuids:
                    try:
                        response = requests.get(
                            f"https://galway-staging.elsevierpure.com/ws/api/external-persons/{uuid_value}",
                            headers={
                                "accept": "application/json",
                                "api-key": API_KEY  
                            },
                            timeout=10
                        )
                        if response.status_code == 200:
                            external_person = response.json()
                            # Count non-empty fields (excluding system ones)
                            field_count = sum(1 for k, v in external_person.items() if k not in ["uuid", "createdBy", "modifiedBy", "version", "portalUrl", "prettyUrlIdentifiers", "previousUuids"] and v)
                            if field_count > max_fields:
                                max_fields = field_count
                                best_uuid = uuid_value
                    except Exception as e:
                        # Log error but continue
                        print(f"⚠️ Failed to fetch external person {uuid_value}: {e}")
                # Don't modify the person object; just score it
                metadata_score = max_fields if best_uuid else 0

        return (type_score, vis_score, metadata_score)

    sorted_matches = sorted(matches, key=score, reverse=True)
    return sorted_matches[0]  # Return best match

def resolve_record_duplicate(records):
    """Choose record with most metadata or updated by real user"""
    if not records:
        return None

    def score(record):
        # 1. Prefer visibility FREE or CAMPUS
        vis = record.get("visibility", {}).get("key", "")
        # No reason to give any score to CONFIDENTIAL or BACKEND records
        vis_score = 1 if vis in ["FREE", "CAMPUS"] else 0

        # 2. Count filled fields (excluding system ones)
        field_count = sum(1 for k, v in record.items() if k not in ["uuid", "createdBy", "modifiedBy", "version", "portalUrl", "prettyUrlIdentifiers", "previousUuids"])

        # 3. Prefer real users
        modifier = record.get("modifiedBy", "")
        is_real_user = modifier not in ["root", "atira", "sync_user", "admin", "system", ""]

        return (vis_score, field_count, is_real_user)

    sorted_records = sorted(records, key=score, reverse=True)
    return sorted_records[0]

def build_electronic_version(doi, version_type_uri, access_type="UNKNOWN",
                             license_type="UNSPECIFIED", embargo_end_date=None):
    """
    Build electronic version object ONLY when a DOI exists.
    If no DOI is supplied, return None (Pure must not receive an electronicVersion entry).
    """
    if not doi:
        return None  # <-- NEW: skip creating electronicVersion entirely

    ev = {
        "typeDiscriminator": "DoiElectronicVersion",
        "doi": doi,
        "accessType": {
            "uri": f"/dk/atira/pure/core/openaccesspermission/{access_type.lower()}"
        },
        "licenseType": {
            "uri": f"/dk/atira/pure/core/document/licenses/{license_type.lower()}"
        },
        "versionType": {
            "uri": version_type_uri
        }
    }

    if embargo_end_date:
        ev["embargoPeriod"] = {
            "endDate": embargo_end_date
        }

    return ev


def build_link(url, alias="", description=""):
    return {
        "url": url,
        "alias": alias,
        "description": {"en_IE": description}
        }

def update_record_from_dspace(pure_record, dspace_row, person_mapping, log_entry):
    """
    Update pure_record with DSpace data according to precedence rules.
    Returns updated record and success flag.
    """
    success = True
    errors = []

    pure_type = pure_record.get("typeDiscriminator", "")

    # --- 1. Authors (dc.contributor.author) > overwrite with mapped list from DSpace, but don't overwrite existing authors
    dspace_authors = parse_author_names(dspace_row.get("dc.contributor.author", ""))
    mapped_contributors = []
    unmatched_authors = []

    # Get existing contributors from Pure record (if any)
    existing_contributors = pure_record.get("contributors", [])

    # Create a set of existing author names (first+last) and UUIDs for fast lookup
    existing_author_keys = set()
    existing_uuids = set()

    for contrib in existing_contributors:
        if not contrib:  # Skip None or empty contributors
            continue

        # Extract name
        name = contrib.get("name", {}) or {}
        first = name.get("firstName", "") or ""
        last = name.get("lastName", "") or ""
        if first.strip() and last.strip():
            existing_author_keys.add((normalize(first), normalize(last)))

        # Extract UUID (internal or external)
        if "person" in contrib:
            person_obj = contrib["person"]
            if person_obj:
                uuid = person_obj.get("uuid")
                if uuid:
                    existing_uuids.add(uuid)
        elif "externalPerson" in contrib:
            ext_person_obj = contrib["externalPerson"]
            if ext_person_obj:
                uuid = ext_person_obj.get("uuid")
                if uuid:
                    existing_uuids.add(uuid)

    for author_name in dspace_authors:
        matches = find_person_match(author_name, person_mapping)
        if matches:
            matched_person = resolve_author_duplicate(matches)
            if matched_person:
                # Check if this author already exists in Pure record (by name or UUID)
                first = matched_person.get("firstName", "")
                last = matched_person.get("lastName", "")
                uuid_value = None

                name_key = (normalize(first), normalize(last))
                if name_key in existing_author_keys or (uuid_value and uuid_value in existing_uuids):
                    # If author already exists, skip adding/updating
                    continue

                # Create contributor object
                if matched_person.get("internal", False) and matched_person.get("internalUUIDs"):
                    internal_uuids = matched_person.get("internalUUIDs")
                    uuid_value = extract_uuid(internal_uuids[0])
                    contributor = {
                        "typeDiscriminator": "InternalContributorAssociation",
                        "hidden": False,
                        "correspondingAuthor": False, 
                        "name": {
                            "firstName": matched_person.get("firstName", ""),
                            "lastName": matched_person.get("lastName", "")
                        },
                        "role": {
                            "uri": f"/dk/atira/pure/researchoutput/roles/{pure_type.lower()}/author",
                            "term": {"en_IE": "Author"}
                        },
                        "person": {
                            "systemName": "Person",
                            "uuid": uuid_value
                        }
                    }
                    # Add internal organizations if available
                    if "internalOrganizations" in matched_person:
                        contributor["organizations"] = [
                            {
                                "systemName": "Organization",
                                "uuid": org_uuid
                            }
                            for org_uuid in matched_person["internalOrganizations"]
                        ]
                    mapped_contributors.append(contributor)

                elif matched_person.get("external", False) and matched_person.get("externalUUIDs"):
                    external_uuids = matched_person.get("externalUUIDs")
                    uuid_value = extract_uuid(external_uuids[0])

                    contributor = {
                        "typeDiscriminator": "ExternalContributorAssociation",
                        "hidden": False,
                        "correspondingAuthor": False, 
                        "name": {
                            "firstName": matched_person.get("firstName", ""),
                            "lastName": matched_person.get("lastName", "")
                        },
                        "role": {
                            "uri": f"/dk/atira/pure/researchoutput/roles/{pure_type.lower()}/author",
                            "term": {"en_IE": "Author"}
                        },
                        "externalPerson": {
                            "systemName": "ExternalPerson",
                            "uuid": uuid_value
                        }
                    }
                    # Add external organizations if available
                    if "externalOrganizations" in matched_person:
                        contributor["externalOrganizations"] = [
                            {
                                "systemName": "ExternalOrganization",
                                "uuid": org_uuid
                            }
                            for org_uuid in matched_person["externalOrganizations"]
                        ]
                    mapped_contributors.append(contributor)
            else:
                unmatched_authors.append(author_name)
        else:
            unmatched_authors.append(author_name)

    # Overwrite contributors only if we have any mapped
    if mapped_contributors:
        pure_record["contributors"] = mapped_contributors

    # --- 2. Funder (dc.contributor.funder) > fill if blank ---
    # TODO: Implement funder lookup and mapping

    # --- 3. Embargo (dc.date.embargo / dc.description.embargo) > overwrite for repo version ---
    embargo_date = dspace_row.get("dc.date.embargo", "").strip()
    embargo_desc = dspace_row.get("dc.description.embargo", "").strip()
    if embargo_date or embargo_desc:
        # Find repository electronic version (DOI starts with https://doi.org/10.13025)
        repo_ev = None
        for ev in pure_record.get("electronicVersions", []):
            doi = ev.get("doi", "")
            if doi and doi.startswith("https://doi.org/10.13025"):
                repo_ev = ev
                break
        if repo_ev:
            repo_ev["embargoPeriod"] = {
                "endDate": embargo_date
            }
        else:
            # Add new electronic version for repository
            ev = build_electronic_version(
                doi=f"https://doi.org/10.13025/{pure_record.get('uuid')}",
                version_type_uri="/dk/atira/pure/researchoutput/electronicversion/versiontype/authorsversion",
                access_type="EMBARGOED",
                license_type="CC_BY_NC_ND",
                embargo_end_date=embargo_date
            )
            if "electronicVersions" not in pure_record:
                pure_record["electronicVersions"] = []
            pure_record["electronicVersions"].append(ev)

    # --- 4. Publication Date (dc.date.issued) > fill if blank, upgrade only ---
    issued = dspace_row.get("dc.date.issued", "").strip()
    if issued:
        # Parse year/month/day
        parts = issued.split("-")
        year = int(parts[0]) if len(parts) >= 1 and parts[0].isdigit() else 0
        month = int(parts[1]) if len(parts) >= 2 and parts[1].isdigit() else 1
        day = int(parts[2]) if len(parts) >= 3 and parts[2].isdigit() else 1

        # Only set if not already set or if year conflicts
        pub_status = pure_record.get("publicationStatuses", [])
        if not pub_status:
            pure_record["publicationStatuses"] = [{
                "publicationStatus": {
                    "uri": "/dk/atira/pure/researchoutput/status/published",
                    "term": {"en_IE": "Published"}
                },
                "publicationDate": {
                    "year": year,
                    "month": month,
                    "day": day
                }
            }]
        else:
            # Check if year differs
            existing_year = pub_status[0].get("publicationDate", {}).get("year", 0)
            if existing_year != 0 and existing_year != year:
                errors.append(f"Publication year conflict: Pure={existing_year}, DSpace={year}")

    # --- 5. Abstract (dc.description.abstract) > fill if blank ---
    abstract = dspace_row.get("dc.description.abstract", "").strip()
    if abstract and not has_text_in_any_language(pure_record, "abstract"):
        pure_record["abstract"] = {"en_IE": escape_special_chars(abstract)}

    # --- 6. Sponsorship (dc.description.sponsorship) > fill if blank ---
    sponsorship = dspace_row.get("dc.description.sponsorship", "").strip()
    if sponsorship and not has_text_in_any_language(pure_record, "fundingText"):
        pure_record["fundingText"] = {"en_IE": escape_special_chars(sponsorship)}

    # --- 7. Publisher DOI (dc.identifier.doi) > add if blank ---
    publisher_doi = dspace_row.get("dc.identifier.doi", "").strip()
    if publisher_doi:
        # Check if already present
        existing_dois = [ev.get("doi", "") for ev in pure_record.get("electronicVersions", [])]
        if not any(publisher_doi in doi for doi in existing_dois):
            # Add as new electronic version
            ev = build_electronic_version(
                doi=publisher_doi,
                version_type_uri="/dk/atira/pure/researchoutput/electronicversion/versiontype/publishersversion",
            )
            if "electronicVersions" not in pure_record:
                pure_record["electronicVersions"] = []
            pure_record["electronicVersions"].append(ev)

    # --- 8. Repository DOI & Handle (dc.identifier.uri) > always add ---
    uri_str = dspace_row.get("dc.identifier.uri", "").strip()
    if uri_str:
        handles = extract_handles_from_uri(uri_str)
        dois = extract_dois_from_uri(uri_str)

        # Add repository DOI as electronic version (if starts with 10.13025)
        for doi in dois:
            if doi.startswith("10.13025"):
                ev = build_electronic_version(
                    doi=f"https://doi.org/{doi}",
                    version_type_uri="/dk/atira/pure/researchoutput/electronicversion/versiontype/authorsversion",
                    access_type="OPEN",
                    license_type="CC_BY_NC_ND"
                )
                if "electronicVersions" not in pure_record:
                    pure_record["electronicVersions"] = []
                pure_record["electronicVersions"].append(ev)

        # Add handles to Links
        for handle in handles:
            link = build_link(f"http://hdl.handle.net/{handle}", alias="Handle", description="Repository Handle")
            if "links" not in pure_record:
                pure_record["links"] = []
            pure_record["links"].append(link)

    # --- 9. Language (dc.language.iso) > fill if blank ---
    lang = dspace_row.get("dc.language.iso", "").strip()
    if lang and not pure_record.get("language", {}).get("uri", ""):
        lang_map = {
            "eng": "en_GB",
            "fre": "fr_FR",
            "ger": "de_DE",
            "spa": "es_ES",
            "gle": "ga_IE"
            # Add more as needed
        }
        lang_code = lang_map.get(lang.lower(), "en_GB")
        pure_record["language"] = {
            "uri": f"/dk/atira/pure/core/languages/{lang_code}"
        }

    # --- 10. Publisher (dc.publisher) > fill if blank ---
    # publisher = dspace_row.get("dc.publisher", "").strip()
    #TODO: Implement publisher lookup and mapping

    # --- 11. Rights (dc.rights.uri) > overwrite for repo version ---
    rights = dspace_row.get("dc.rights", "").strip()
    if rights:
        # Look for repo electronic version
        repo_ev = None
        for ev in pure_record.get("electronicVersions", []):
            doi = ev.get("doi", "")
            if doi and doi.startswith("https://doi.org/10.13025"):
                repo_ev = ev
                break
        if repo_ev:
            # Map rights to license type
            license_map = {
                "CC BY-NC-ND": "CC_BY_NC_ND",
                "CC BY": "CC_BY",
                "CC BY-SA": "CC_BY_SA",
                "CC BY-NC": "CC_BY_NC",
                "CC BY-NC-SA": "CC_BY_NC_SA",
                "Public Domain": "PUBLIC_DOMAIN",
                "All rights reserved": "ALL_RIGHTS_RESERVED"
            }
            license_type = license_map.get(rights, "CC_BY_NC_ND")
            repo_ev["licenseType"] = {
                "uri": f"/dk/atira/pure/core/document/licenses/{license_type.lower()}"
            }

    # --- 12. Title (dc.title) > fill if blank ---
    title = dspace_row.get("dc.title", "").strip()
    if title and not pure_record.get("title", {}).get("value", "").strip():
        pure_record["title"] = {"value": escape_special_chars(title)}

    log_entry["success"] = success and not errors
    if errors:
        log_entry["error"] = "; ".join(errors)
    return pure_record, success

def create_new_record_from_dspace(dspace_row, person_mapping):
    """Create new Pure record from DSpace row"""
    # Escape special characters in title first
    escaped_title = escape_special_chars(dspace_row.get("dc.title", "").strip())
    
    record = {
        "version": None,  # Will be set by API
        "title": {"value": escaped_title},
        "type": {
            "uri": ""
        },
        "category": {
            "uri": "/dk/atira/pure/researchoutput/category/research"
        },
        "language": {
            "uri": "/dk/atira/pure/core/languages/en_GB"
        },
        "electronicVersions": [],
        "links": [],
        "organizations": [
                {
                "systemName": "Organization",
                "uuid": "1becab14-37ce-4810-9b95-fc014063bcae",
                "name": {
                        "en_IE": "University of Galway"
                        },
                "type": {
                        "uri": "/dk/atira/pure/organisation/organisationtypes/organisation/university",
                        "term": {
                            "en_IE": "University"
                            }
                        }
                }
        ],
        "managingOrganization": {
            "uuid": "1becab14-37ce-4810-9b95-fc014063bcae",
            "systemName": "Organization"
            }, 
        "visibility": {
            "key": "FREE"
        },
        "workflow": {
            "step": "forApproval"
        },
        "typeDiscriminator": "OtherContribution"
    }

    # Determine valid typeDiscriminator from Pure type URI
    pure_type_map = {
        "contributiontojournal": "ContributionToJournal",
        "contributiontoconference": "ContributionToConference",
        "contributiontobookanthology": "ContributionToBookAnthology",
        "bookanthology": "BookAnthology",
        "workingpaper": "WorkingPaper",
        "nontextual": "NonTextual",
        "contributiontoperiodical": "ContributionToPeriodical",
        "thesis": "Thesis",
        "othercontribution": "OtherContribution",
        "patent": "Patent",
        "memorandum": "Memorandum"
    }

    # Set Pure subtype based on dc.type
    dspace_type = dspace_row.get("dc.type", "").strip().lower()
    pure_type_uri = dspace_pure_subtype_map.get(dspace_type, "/dk/atira/pure/researchoutput/researchoutputtypes/othercontribution/other")
    record["type"]["uri"] = pure_type_uri

    # Set Pute type
    pure_type_key = get_pure_type_key(pure_type_uri)
    record["typeDiscriminator"] = pure_type_map.get(pure_type_key, "OtherContribution")

    # Add type-specific required fields
    record = add_type_specific_fields(record, dspace_row)

    # Set publication date
    issued = dspace_row.get("dc.date.issued", "").strip()
    if issued:
        parts = issued.split("-")
        year = int(parts[0]) if len(parts) >= 1 and parts[0].isdigit() else 0
        month = int(parts[1]) if len(parts) >= 2 and parts[1].isdigit() else 1
        day = int(parts[2]) if len(parts) >= 3 and parts[2].isdigit() else 1
        record["publicationStatuses"] = [{
            "publicationStatus": {
                "uri": "/dk/atira/pure/researchoutput/status/published"
            },
            "publicationDate": {
                "year": year,
                "month": month,
                "day": day
            }
        }]

    # Set abstract
    abstract = dspace_row.get("dc.description.abstract", "").strip()
    if abstract:
        record["abstract"] = {"en_IE": escape_special_chars(abstract)}

    # Set language
    lang = dspace_row.get("dc.language.iso", "").strip()
    if lang:
        lang_map = {
            "eng": "en_GB",
            "fre": "fr_FR",
            "ger": "de_DE",
            "spa": "es_ES",
            "gle": "ga_IE"
        }
        lang_code = lang_map.get(lang.lower(), "en_GB")
        record["language"] = {
            "uri": f"/dk/atira/pure/core/languages/{lang_code}"
        }

    # Set title
    title = dspace_row.get("dc.title", "").strip()
    if title:
        record["title"] = {"value": escape_special_chars(title)}

    # Set contributors
    dspace_authors = parse_author_names(dspace_row.get("dc.contributor.author", ""))
    print(f"✅ Processing authors: {dspace_authors}")  # Log authors being processed

    mapped_contributors = []
    unmatched_authors = []

    for author_name in dspace_authors:
        print(f"  ➤ Checking match for: '{author_name}'")  # Log each author
        matches = find_person_match(author_name, person_mapping)
        if matches:
            print(f"    ✅ Found {len(matches)} matches")  # Log matches found
            matched_person = resolve_author_duplicate(matches)
            if matched_person:
                if matched_person.get("internal", False) and matched_person.get("internalUUIDs"):
                    internal_uuids = matched_person.get("internalUUIDs")
                    uuid_value = extract_uuid(internal_uuids[0])

                    contributor = {
                        "typeDiscriminator": "InternalContributorAssociation",
                        "hidden": False,
                        "correspondingAuthor": False,
                        "name": {
                            "firstName": matched_person.get("firstName", ""),
                            "lastName": matched_person.get("lastName", "")
                        },
                        "role": {
                            "uri": f"/dk/atira/pure/researchoutput/roles/{pure_type_key.lower()}/author",
                            "term": {"en_IE": "Author"}
                        },
                        "person": {
                            "systemName": "Person",
                            "uuid": uuid_value
                        }
                    }
                    if "internalOrganizations" in matched_person:
                        contributor["organizations"] = [
                            {
                                "systemName": "Organization",
                                "uuid": org_uuid
                            }
                            for org_uuid in matched_person["internalOrganizations"]
                        ]
                    mapped_contributors.append(contributor)
                    print(f"      ✅ Added as InternalContributor: {matched_person.get('firstName')} {matched_person.get('lastName')}")

                elif matched_person.get("external", False) and matched_person.get("externalUUIDs"):
                    external_uuids = matched_person.get("externalUUIDs")
                    uuid_value = extract_uuid(external_uuids[0])

                    contributor = {
                        "typeDiscriminator": "ExternalContributorAssociation",
                        "hidden": False,
                        "correspondingAuthor": False,
                        "name": {
                            "firstName": matched_person.get("firstName", ""),
                            "lastName": matched_person.get("lastName", "")
                        },
                        "role": {
                            "uri": f"/dk/atira/pure/researchoutput/roles/{pure_type_key.lower()}/author",
                            "term": {"en_IE": "Author"}
                        },
                        "externalPerson": {
                            "systemName": "ExternalPerson",
                            "uuid": uuid_value
                        }
                    }
                    if "externalOrganizations" in matched_person:
                        contributor["externalOrganizations"] = [
                            {
                                "systemName": "ExternalOrganization",
                                "uuid": org_uuid
                            }
                            for org_uuid in matched_person["externalOrganizations"]
                        ]
                    mapped_contributors.append(contributor)
                    print(f"      ✅ Added as ExternalContributor: {matched_person.get('firstName')} {matched_person.get('lastName')}")
            else:
                print(f"      ⚠️ No valid match after duplicate resolution — adding to unmatched")  # Log
                unmatched_authors.append(author_name)
        else:
            print(f"      ⚠️ No matches found — adding to unmatched")  # Log
            unmatched_authors.append(author_name)

    # Check if we have any contributors - if not, skip this record
    if not mapped_contributors:
        print(f"❌ No matched contributors found for this record - skipping")
        return None  # Return None to indicate record should be skipped
    
    # Always ensure author info is present
    record["contributors"] = mapped_contributors

    print(f"✅ Added {len(mapped_contributors)} contributors")
    
    # Collect all organizations from contributors and add to top-level organizations
    all_org_uuids = set()
    first_internal_org_uuid = None  # Track first internal contributor's first organization
    
    for i, contributor in enumerate(mapped_contributors):
        # Collect from internal organizations
        if "organizations" in contributor:
            for j, org in enumerate(contributor["organizations"]):
                org_uuid = org.get("uuid")
                if org_uuid:
                    all_org_uuids.add(org_uuid)
                    # Capture first internal contributor's first organization
                    if i == 0 and j == 0 and first_internal_org_uuid is None:
                        first_internal_org_uuid = org_uuid
        # Collect from external organizations
        if "externalOrganizations" in contributor:
            for org in contributor["externalOrganizations"]:
                org_uuid = org.get("uuid")
                if org_uuid:
                    all_org_uuids.add(org_uuid)
    
    # Add collected organizations to top-level organizations (avoid duplicates)
    if all_org_uuids:
        existing_org_uuids = {org.get("uuid") for org in record.get("organizations", []) if org.get("uuid")}
        for org_uuid in all_org_uuids:
            if org_uuid not in existing_org_uuids:
                record["organizations"].append({
                    "systemName": "Organization",
                    "uuid": org_uuid
                })
    
    # Set managingOrganization from first internal contributor's first organization
    if first_internal_org_uuid:
        record["managingOrganization"] = {
            "uuid": first_internal_org_uuid,
            "systemName": "Organization"
        }
        print(f"✅ Set managingOrganization to: {first_internal_org_uuid}")

    
    # Only add unmatched authors as keywordGroups if there are actually unmatched authors
    if unmatched_authors:
        # Add unmatched authors as keywordGroups 
        keyword_group = {
            "typeDiscriminator": "FullKeywordGroup",
            "logicalName": "/dk/atira/pure/authors",
            "name": {
                "en_IE": "Authors (Note for portal: view the doc link for the full list of authors)"
            },
            "keywordContainers": [
                {
                    "structuredKeyword": {
                        "uri": "/dk/atira/pure/authors/authors"
                    },
                    "freeKeywords": [
                        {
                            "locale": "en_IE",
                            "freeKeywords": unmatched_authors  
                        }
                    ]
                }
            ]
        }
        record["keywordGroups"] = [keyword_group]
        print(f"✅ Added {len(unmatched_authors)} unmatched authors to keywordGroups")    


    # Set DOI
    publisher_doi = dspace_row.get("dc.identifier.doi", "").strip()
    if publisher_doi:
        ev = build_electronic_version(publisher_doi, "/dk/atira/pure/researchoutput/electronicversion/versiontype/publishersversion")
        record["electronicVersions"] = [ev]

    # Set repository DOI & Handle
    uri_str = dspace_row.get("dc.identifier.uri", "").strip()
    if uri_str:
        handles = extract_handles_from_uri(uri_str)
        dois = extract_dois_from_uri(uri_str)

        for doi in dois:
            if doi.startswith("10.13025"):
                ev = build_electronic_version(
                    doi=f"https://doi.org/{doi}",
                    version_type_uri="/dk/atira/pure/researchoutput/electronicversion/versiontype/authorsversion",
                    access_type="OPEN",
                    license_type="CC_BY_NC_ND"
                )
                if ev:  # Check if not None
                    if "electronicVersions" not in record:
                        record["electronicVersions"] = []
                    record["electronicVersions"].append(ev)
        for handle in handles:
            link = build_link(f"http://hdl.handle.net/{handle}", alias="Handle", description="Repository Handle")
            if "links" not in record:
                record["links"] = []
            record["links"].append(link)

    # Set rights
    rights = dspace_row.get("dc.rights", "").strip()
    if rights:
        # Look for repo electronic version
        repo_ev = None
        for ev in record.get("electronicVersions", []):
            doi = ev.get("doi", "")
            if doi and doi.startswith("https://doi.org/10.13025"):
                repo_ev = ev
                break
        if repo_ev:
            license_map = {
                "CC BY-NC-ND": "CC_BY_NC_ND",
                "CC BY": "CC_BY",
                "CC BY-SA": "CC_BY_SA",
                "CC BY-NC": "CC_BY_NC",
                "CC BY-NC-SA": "CC_BY_NC_SA",
                "Public Domain": "PUBLIC_DOMAIN",
                "All rights reserved": "ALL_RIGHTS_RESERVED"
            }
            license_type = license_map.get(rights, "CC_BY_NC_ND")
            repo_ev["licenseType"] = {
                "uri": f"/dk/atira/pure/core/document/licenses/{license_type.lower()}"
            }

    return record

# --- MAIN FUNCTION ---

def main():
    
    processing_log_path = os.path.join(LOG_DIR, f"processing_log_{TODAY}.log")
    logger = LoggerOutput(processing_log_path)
    sys.stdout = logger

    # Load data
    print("Loading DSpace CSV...")
    dspace_rows = []
    with open(DSPACE_CSV, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            dspace_rows.append(row)
    print(f"✅ Loaded {len(dspace_rows)} records from {DSPACE_CSV}")

    print("Loading Pure JSON...")
    with open(PURE_JSON, 'r', encoding='utf-8') as f:
        pure_items = json.load(f)
    print(f"✅ Loaded {len(pure_items)} records from {PURE_JSON}")

    print("Loading Person Mapping...")
    with open(PERSON_MAPPING_JSON, 'r', encoding='utf-8') as f:
        person_mapping = json.load(f)
    print(f"✅ Loaded {len(person_mapping)} person records from {PERSON_MAPPING_JSON}")

    # Prepare logs
    log_entries = []
    error_log = []

    # Group Pure records by identifiers for fast lookup
    pure_by_doi = defaultdict(list)
    pure_by_handle = defaultdict(list)
    pure_by_title = defaultdict(list)

    for item in pure_items:
        # Index by DOI
        for ev in item.get("electronicVersions", []):
            doi = ev.get("doi", "")
            if doi:
                pure_by_doi[normalize(doi)].append(item)
        # Index by links
        for link in item.get("links", []):
            url = link.get("url", "")
            if url:
                pure_by_handle[normalize(url)].append(item)
        # Index by title
        title = item.get("title", {}).get("value", "")
        if title:
            pure_by_title[normalize(title)].append(item)

    # Process each DSpace row with tqdm progress bar
    print(f"Processing {len(dspace_rows)} DSpace records...")
    for i, row in enumerate(tqdm(dspace_rows, desc="Matching Records", unit="record")):
        log_entry = {
            "handle": None,
            "uuid": None,
            "pure_type": None,
            "matched": False,
            "duplicates": False,
            "success": False,
            "error": None
        }

        # Extract handles from URI
        handles = extract_handles_from_uri(row.get("dc.identifier.uri", ""))
        if handles:
            log_entry["handle"] = f"http://hdl.handle.net/{handles[0]}"  # Use first handle

        # Try to match by DOI
        matched_records = []
        publisher_doi = row.get("dc.identifier.doi", "").strip()
        if publisher_doi:
            normalized_doi = normalize(publisher_doi)
            if normalized_doi in pure_by_doi:
                matched_records.extend(pure_by_doi[normalized_doi])

        # Try to match by Handle
        if not matched_records:
            for handle in handles:
                normalized_handle = normalize(handle)
                if normalized_handle in pure_by_handle:
                    matched_records.extend(pure_by_handle[normalized_handle])

        # Try to match by Title
        if not matched_records:
            title = row.get("dc.title", "").strip()
            if title:
                normalized_title = normalize(title)
                if normalized_title in pure_by_title:
                    matched_records.extend(pure_by_title[normalized_title])

        # Resolve duplicate records
        if len(matched_records) > 1:
            log_entry["duplicates"] = True
            chosen_record = resolve_record_duplicate(matched_records)
            if chosen_record:
                matched_records = [chosen_record]

        # If matched, update record
        if matched_records:
            log_entry["matched"] = True
            record = matched_records[0]
            log_entry["uuid"] = record.get("uuid", "")
            log_entry["pure_type"] = record.get("type", {}).get("uri", "")

            try:
                updated_record, success = update_record_from_dspace(record, row, person_mapping, log_entry)
                log_entry["success"] = success
                if success:
                    # Save to matched folder
                    type_key = get_pure_type_key(log_entry["pure_type"])
                    filename = f"{type_key}_{TODAY}.json"
                    filepath = os.path.join(MATCHED_DIR, filename)
                    existing = []
                    if os.path.exists(filepath):
                        with open(filepath, 'r', encoding='utf-8') as f:
                            existing = json.load(f)
                    existing.append(updated_record)
                    with open(filepath, 'w', encoding='utf-8') as f:
                        json.dump(existing, f, indent=2, ensure_ascii=False)
            except Exception as e:
                log_entry["success"] = False
                log_entry["error"] = str(e)
                # Log full traceback to error.log
                import traceback
                error_log.append(f"Error updating record {log_entry['uuid']}: {e}\n{traceback.format_exc()}")
        else:
            # Create new record — this is an UNMATCHED RESEARCH OUTPUT
            try:
                new_record = create_new_record_from_dspace(row, person_mapping)
                
                # Skip record if no contributors were matched
                if new_record is None:
                    log_entry["success"] = False
                    log_entry["error"] = "No matched contributors"
                else:
                    log_entry["success"] = True
                    log_entry["pure_type"] = new_record.get("type", {}).get("uri", "")

                    # Save to unmatched folder
                    type_key = get_pure_type_key(log_entry["pure_type"])
                    filename = f"{type_key}_{TODAY}.json"
                    filepath = os.path.join(UNMATCHED_DIR, filename)
                    existing = []
                    if os.path.exists(filepath):
                        with open(filepath, 'r', encoding='utf-8') as f:
                            existing = json.load(f)
                    existing.append(new_record)
                    with open(filepath, 'w', encoding='utf-8') as f:
                        json.dump(existing, f, indent=2, ensure_ascii=False)
            except Exception as e:
                log_entry["success"] = False
                log_entry["error"] = str(e)
                # Log full traceback to error.log
                import traceback
                error_log.append(f"Error creating record: {e}\n{traceback.format_exc()}")

        log_entries.append(log_entry)

    # Write logs
    log_json_path = os.path.join(LOG_DIR, f"status_log_{TODAY}.json")
    with open(log_json_path, 'w', encoding='utf-8') as f:
        json.dump(log_entries, f, indent=2, ensure_ascii=False)

    log_csv_path = os.path.join(LOG_DIR, f"status_log_{TODAY}.csv")
    with open(log_csv_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=log_entries[0].keys())
        writer.writeheader()
        writer.writerows(log_entries)

    error_log_path = os.path.join(LOG_DIR, f"error_log_{TODAY}.log")
    with open(error_log_path, 'w', encoding='utf-8') as f:
        for err in error_log:
            f.write(err + "\n")

    print(f"\n✅ Done! {len(log_entries)} records processed.")
    print(f"   Matched: {sum(1 for e in log_entries if e['matched'])}")
    print(f"   Unmatched: {sum(1 for e in log_entries if not e['matched'])}")
    print(f"   Success: {sum(1 for e in log_entries if e['success'])}")
    print(f"   Errors: {len(error_log)}")
    print(f"   Logs saved to: {LOG_DIR}")
    
    logger.close()
    sys.stdout = logger.terminal


if __name__ == "__main__":
    main()