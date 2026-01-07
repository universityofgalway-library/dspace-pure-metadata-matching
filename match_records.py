import os
import re
import sys
import csv
import json
import requests
from datetime import date
from collections import defaultdict
from itertools import product
from tqdm import tqdm
from dotenv import load_dotenv


# --- CONFIGURATION ---

# Load environment variables from .env file
load_dotenv()

# DSPACE_CSV = "./matching_test/dspace_test_sample.csv"
DSPACE_CSV = 'metadata_dspace_test_02-12-2025.csv'
PURE_JSON = "./matching_test/research_outputs/research_outputs_2025-11-20_all.json"
PERSON_MAPPING_JSON = "./matching_test/matched_authors/merged_authors_all_20251215.json"
OUTPUT_DIR = "./matching_test/output"
MATCHED_DIR = os.path.join(OUTPUT_DIR, "matched")
UNMATCHED_DIR = os.path.join(OUTPUT_DIR, "unmatched")
LOG_DIR = os.path.join(OUTPUT_DIR, "logs")
API_KEY = os.getenv("PURE_API_KEY", "")
BASE_URL = "https://galway-staging.elsevierpure.com/ws/api/"

DOI_REGEX = re.compile(r'^(?:https?://)?(?:doi\.org/|doi:)?(10\.\S+)$', re.IGNORECASE)
HANDLE_REGEX = re.compile(r'^(?:https?://hdl\.handle\.net/)?(10379/\S+)$', re.IGNORECASE)

SYSTEM_FIELDS_TO_EXCLUDE = {
    "createdBy",
    "createdDate",
    "modifiedBy",
    "modifiedDate",
    "prettyUrlIdentifiers",
    "version",
}

if not API_KEY:
    print("⚠️ WARNING: PURE_API_KEY not found in environment variables.")
    print("   External person duplicate resolution will be skipped.")

os.makedirs(MATCHED_DIR, exist_ok=True)
os.makedirs(UNMATCHED_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)

TODAY = date.today().isoformat()

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

def strip_system_fields(record):
    """Return a shallow copy of record without system fields."""
    return {
        k: v
        for k, v in record.items()
        if k not in SYSTEM_FIELDS_TO_EXCLUDE
    }

def normalize(s):
    return s.strip().lower() if s else ""

def map_language(lang):
    lang_map = {
        "eng": "en_IE",
        "fre": "fr_FR",
        "ger": "de_DE",
        "spa": "es_ES",
        "gle": "ga"
        # Add more as needed
    }
    lang_code = lang_map.get(lang.lower(), "en_IE")
    return lang_code

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

def calculate_title_similarity(title1, title2, threshold=0.8):
    """
    Calculate similarity between two titles.
    Returns tuple (similarity_score, is_match)
    
    Args:
        title1: First title string
        title2: Second title string
        threshold: Minimum similarity ratio required (0-1), default 0.9 (80%)
    
    Returns:
        Tuple of (similarity_ratio: float, is_match: bool)
    """
    if not title1 or not title2:
        return (0.0, False)
    
    # Normalize titles for comparison
    t1 = normalize(title1)
    t2 = normalize(title2)
    
    if t1 == t2:
        return (1.0, True)
    
    # Calculate simple character matching similarity
    # Using longest common subsequence approach
    longer = max(len(t1), len(t2))
    if longer == 0:
        return (0.0, False)
    
    # Calculate matching characters (simple approach: count matching positions)
    matches = sum(1 for a, b in zip(t1, t2) if a == b)
    
    # Also add bonus for common substrings
    words1 = set(t1.split())
    words2 = set(t2.split())
    common_words = len(words1 & words2)
    total_words = len(words1 | words2)
    word_similarity = common_words / total_words if total_words > 0 else 0
    
    # Combine character and word similarity (weighted average)
    char_similarity = matches / ((len(t1)+len(t2))/2)
    combined_similarity = (char_similarity * 0.5) + (word_similarity * 0.5)
    
    return (combined_similarity, combined_similarity >= threshold)

def normalize_doi(value: str) -> str:
    if not isinstance(value, str):
        return value

    v = value.strip().lower()
    match = DOI_REGEX.match(v)

    if not match:
        return value  # not a valid DOI → leave unchanged

    return f"https://doi.org/{match.group(1)}"

def normalize_handle(value: str) -> str:
    if not isinstance(value, str):
        return value

    v = value.strip().lower()
    match = HANDLE_REGEX.match(v)

    if not match:
        return value  # not a valid handle → leave unchanged

    return f"http://hdl.handle.net/{match.group(1)}"

def extract_dois_from_uri(uri_str):
    """Extract DOIs from dc.identifier.uri (semicolon-separated)"""
    if not uri_str:
        return []
    uris = [u.strip().lower() for u in uri_str.split(";") if u.strip()]
    dois = []
    for u in uris:
        # Match DOI pattern
        match = DOI_REGEX.match(u)
        if match:
            doi = f"https://doi.org/{match.group(1)}"
            dois.append(doi)
    return dois

def extract_handles_from_uri(uri_str):
    """Extract handles from dc.identifier.uri"""
    if not uri_str:
        return []
    uris = [u.strip().lower() for u in uri_str.split(";") if u.strip()]
    handles = []
    for u in uris:
        match = HANDLE_REGEX.match(u)
        if match:
            handle = f"http://hdl.handle.net/{match.group(1)}"
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
        # Get all possible first and last name variations
        p_first = person.get("firstName", "")
        p_last = person.get("lastName", "")
        alt_firsts = person.get("alternativeFirstName", []) or []
        alt_lasts = person.get("alternativeLastName", []) or []

        # Build complete lists of all possible first and last names
        all_firsts = []
        if p_first:
            all_firsts.append(p_first)
        all_firsts.extend(alt_firsts)

        all_lasts = []
        if p_last:
            all_lasts.append(p_last)
        all_lasts.extend(alt_lasts)

        # Check all combinations of (first, last) in both orders
        matched = False
        for af in all_firsts:
            for al in all_lasts:
                # Try direct match: (first, last)
                if normalize(af) == normalize(first) and normalize(al) == normalize(last):
                    matches.append(person)
                    matched = True
                    break
                # Try swapped match: (last, first)
                if normalize(af) == normalize(last) and normalize(al) == normalize(first):
                    matches.append(person)
                    matched = True
                    break
            if matched:
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
                    if not API_KEY:
                        # If no API key, skip API calls and just use first UUID
                        best_uuid = uuid_value
                        max_fields = 0
                        break
                    try:
                        response = requests.get(
                            f"{BASE_URL}persons/{uuid_value}",
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
                        # Log error but continue - use this person anyway
                        pass
                metadata_score = max_fields if best_uuid else 0
        
        elif external:
            external_uuids = person.get("externalUUIDs", [])
            if external_uuids:
                best_uuid = None
                max_fields = -1
                for uuid_value in external_uuids:
                    if not API_KEY:
                        # If no API key, skip API calls and just use first UUID
                        best_uuid = uuid_value
                        max_fields = 0
                        break
                    try:
                        response = requests.get(
                            f"{BASE_URL}external-persons/{uuid_value}",
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
                        # Log error but continue - use this person anyway
                        pass
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
        return None

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

def update_record_from_dspace(pure_record, dspace_row, person_mapping, log_entry, before_update_records):
    """
    Update pure_record with DSpace data according to precedence rules.
    Returns updated record and success flag.
    """
    success = True
    errors = []

    # Start with only the UUID - required for updates
    updated_record = {
        "uuid": pure_record.get("uuid")
    }

    pure_type = pure_record.get("typeDiscriminator", "")

    # --- 1. Authors (dc.contributor.author) > add new mapped authors from DSpace
    dspace_authors = parse_author_names(dspace_row.get("dc.contributor.author", ""))
    
    # Get existing contributors from Pure record (if any)
    existing_contributors = pure_record.get("contributors", [])

    # Create a set of existing author names (first+last) and UUIDs for fast lookup
    existing_author_keys = set()
    existing_uuids = set()
    existing_by_uuid = {}  # Map UUID to contributor object
    existing_by_name = {}  # Map (first, last) to contributor object

    for contrib in existing_contributors:
        if not contrib:  # Skip None or empty contributors
            continue

        # Extract name
        name = contrib.get("name", {}) or {}
        first = name.get("firstName", "") or ""
        last = name.get("lastName", "") or ""
        all_first_names = [first] if first else []
        all_last_names = [last] if last else []

        # Add alternatives from 'names' array
        for name_entry in contrib.get("names", []):
            name_obj = name_entry.get("name", {})
            if first := name_obj.get("firstName", ""):
                all_first_names.append(first)
            if last := name_obj.get("lastName", ""):
                all_last_names.append(last)

        # Generate (first_name, surname)
        normal_order = list(product(all_first_names, all_last_names))
        # Generate (surname, first_name)
        reverse_order = list(product(all_last_names, all_first_names))
        # Combine both
        all_combinations = normal_order + reverse_order

        for pair in all_combinations:
            name_key = (normalize(pair[0].strip()), normalize(pair[1].strip()))
            existing_author_keys.add(name_key)
            existing_by_name[name_key] = contrib

        # Extract UUID (internal or external)
        if "person" in contrib:
            person_obj = contrib["person"]
            if person_obj:
                uuid = person_obj.get("uuid")
                if uuid:
                    existing_uuids.add(uuid)
                    existing_by_uuid[uuid] = contrib
        elif "externalPerson" in contrib:
            ext_person_obj = contrib["externalPerson"]
            if ext_person_obj:
                uuid = ext_person_obj.get("uuid")
                if uuid:
                    existing_uuids.add(uuid)
                    existing_by_uuid[uuid] = contrib

    # Process DSpace authors in order and build final contributors list
    final_contributors = []
    unmatched_authors = []

    for author_name in dspace_authors:
        print(f"  ➤ Checking match for: '{author_name}'")
        matches = find_person_match(author_name, person_mapping)
        
        if matches:
            print(f"    ✅ Found {len(matches)} matches")
            matched_person = resolve_author_duplicate(matches)
            
            if not matched_person:
                print(f"      ❌ ERROR: resolve_author_duplicate returned None for {len(matches)} matches!")
                unmatched_authors.append(author_name)
                continue
                
            # Check if person has valid UUIDs
            has_valid_internal = matched_person.get("internal", False) and matched_person.get("internalUUIDs")
            has_valid_external = matched_person.get("external", False) and matched_person.get("externalUUIDs")
            
            if not has_valid_internal and not has_valid_external:
                print(f"      ⚠️ Matched person has no valid UUIDs (internal={matched_person.get('internal')}, external={matched_person.get('external')}) — adding to unmatched")
                unmatched_authors.append(author_name)
                continue
            
            # Get UUID to check if already exists
            uuid_value = None
            if has_valid_internal:
                uuid_value = extract_uuid(matched_person.get("internalUUIDs")[0])
            elif has_valid_external:
                uuid_value = extract_uuid(matched_person.get("externalUUIDs")[0])
            
            # Check if this author already exists
            first = matched_person.get("firstName", "")
            last = matched_person.get("lastName", "")
            name_key = (normalize(first), normalize(last))
            
            # If contributor already exists, use existing one (preserves DSpace order)
            if uuid_value in existing_by_uuid:
                print(f"      ℹ️ Author already exists (by UUID), using existing: {first} {last}")
                final_contributors.append(existing_by_uuid[uuid_value])
            elif name_key in existing_by_name:
                print(f"      ℹ️ Author already exists (by name), using existing: {first} {last}")
                final_contributors.append(existing_by_name[name_key])
            else:
                # Create new contributor
                if has_valid_internal:
                    contributor = {
                        "typeDiscriminator": "InternalContributorAssociation",
                        "hidden": False,
                        "name": {
                            "firstName": first,
                            "lastName": last
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
                    if "internalOrganizations" in matched_person:
                        contributor["organizations"] = [
                            {
                                "systemName": "Organization",
                                "uuid": org_uuid
                            }
                            for org_uuid in matched_person["internalOrganizations"]
                        ]
                    if "externalOrganizations" in matched_person:
                        contributor["externalOrganizations"] = [
                            {
                                "systemName": "ExternalOrganization",
                                "uuid": org_uuid
                            }
                            for org_uuid in matched_person["externalOrganizations"]
                        ]
                    final_contributors.append(contributor)

                elif has_valid_external:
                    contributor = {
                        "typeDiscriminator": "ExternalContributorAssociation",
                        "hidden": False,
                        "correspondingAuthor": False, 
                        "name": {
                            "firstName": first,
                            "lastName": last
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
                    if "externalOrganizations" in matched_person:
                        contributor["externalOrganizations"] = [
                            {
                                "systemName": "ExternalOrganization",
                                "uuid": org_uuid
                            }
                            for org_uuid in matched_person["externalOrganizations"]
                        ]
                    if "internalOrganizations" in matched_person:
                        contributor["organizations"] = [
                            {
                                "systemName": "Organization",
                                "uuid": org_uuid
                            }
                            for org_uuid in matched_person["internalOrganizations"]
                        ]
                    final_contributors.append(contributor)
        else:
            print(f"      ⚠️ No matches found — adding to unmatched")
            unmatched_authors.append(author_name)

    # Only update contributors if we have any
    if final_contributors:
        updated_record["contributors"] = final_contributors
    
    # --- 1a. Collect ALL organizations from ALL contributors (existing + new) ---
    all_internal_org_uuids = set()
    all_external_org_uuids = set()
    
    # Collect organizations from final contributors list (already in correct order)
    all_contributors_for_orgs = final_contributors
        
    for contributor in all_contributors_for_orgs:
        if not contributor:  # Skip None or empty contributors
            continue
            
        # Collect internal organizations
        if "organizations" in contributor:
            for org in contributor["organizations"]:
                org_uuid = org.get("uuid")
                if org_uuid:
                    all_internal_org_uuids.add(org_uuid)
        
        # Collect external organizations
        if "externalOrganizations" in contributor:
            for org in contributor["externalOrganizations"]:
                org_uuid = org.get("uuid")
                if org_uuid:
                    all_external_org_uuids.add(org_uuid)
    
    # Update top-level organizations with unique internal orgs
    if all_internal_org_uuids:
        updated_record["organizations"] = [
            {
                "systemName": "Organization",
                "uuid": org_uuid
            }
            for org_uuid in all_internal_org_uuids
        ]
    
    # Update top-level externalOrganizations with unique external orgs
    if all_external_org_uuids:
        updated_record["externalOrganizations"] = [
            {
                "systemName": "ExternalOrganization",
                "uuid": org_uuid
            }
            for org_uuid in all_external_org_uuids
        ]

    # --- 1b. Remove author keyword group if all DSpace authors are now matched ---
    if dspace_authors and not unmatched_authors:
        # All authors from DSpace are matched, remove the author keyword group
        existing_keyword_groups = pure_record.get("keywordGroups", [])
        if existing_keyword_groups:
            # Filter out the authors keyword group
            filtered_groups = [
                kg for kg in existing_keyword_groups
                if kg.get("logicalName") != "/dk/atira/pure/authors"
            ]
            # Only update if we actually removed something
            if len(filtered_groups) < len(existing_keyword_groups):
                if filtered_groups:
                    # Keep other keyword groups
                    updated_record["keywordGroups"] = filtered_groups
                else:
                    # Remove keywordGroups entirely if empty
                    updated_record["keywordGroups"] = []
    
    # --- 2. Funder (dc.contributor.funder) > fill if blank ---
    # TODO: Implement funder lookup and mapping


    # --- 3. Publication Date (dc.date.issued) > fill if blank, upgrade only ---
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
            updated_record["publicationStatuses"] = [{
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

    # --- 4. Sponsorship (dc.description.sponsorship) > fill if blank ---
    sponsorship = dspace_row.get("dc.description.sponsorship", "").strip()
    if sponsorship and not has_text_in_any_language(pure_record, "fundingText"):
        updated_record["fundingText"] = {"en_IE": escape_special_chars(sponsorship)}

    # --- 5. Electronic Versions (DOIs + embargo + rights + access) ---
    existing_evs = pure_record.get("electronicVersions", [])
    updated_evs = list(existing_evs)  # Start with existing versions
    existing_dois = [normalize_doi(ev.get("doi", "")) for ev in existing_evs]

    # --- 5a. Publisher DOI (dc.identifier.doi) > add if blank ---
    publisher_doi = dspace_row.get("dc.identifier.doi", "").strip()
    if publisher_doi:
        publisher_doi = normalize_doi(publisher_doi)
        # Check if already present
        if not any(publisher_doi in doi for doi in existing_dois):
            # Add as new electronic version
            ev = build_electronic_version(
                doi=publisher_doi,
                version_type_uri="/dk/atira/pure/researchoutput/electronicversion/versiontype/publishersversion",
            )
            if ev:
                updated_evs.append(ev)

    # --- 5b. Embargo (dc.date.embargo / dc.description.embargo) > overwrite for repo version ---
    embargo_date = dspace_row.get("dc.date.embargo", "").strip()
    embargo_desc = dspace_row.get("dc.description.embargo", "").strip()
    embargo_active = bool(embargo_date and embargo_date > TODAY)

    repo_ev = None
    repo_ev_index = None
    for i, ev in enumerate(updated_evs):
        doi = normalize_doi(ev.get("doi", ""))
        if doi.startswith("https://doi.org/10.13025"):
            repo_ev = ev
            repo_ev_index = i
            break

    if repo_ev:
        # Overwrite embargo if repo version exists and embargo info present
        if embargo_date or embargo_desc:
            repo_ev["embargoPeriod"] = {"endDate": embargo_date}

            if embargo_active:
                repo_ev["accessType"] = "EMBARGOED"

        # Always enforce version type + license
        repo_ev["versionType"] = {
            "uri": "/dk/atira/pure/researchoutput/electronicversion/versiontype/authorsversion"
        }
        repo_ev["license"] = {
            "type": "CC_BY_NC_ND"
        }

        updated_evs[repo_ev_index] = repo_ev

    # --- 5c. Repository DOI & Handle (dc.identifier.uri) > always add ---
    else:
        uri_str = dspace_row.get("dc.identifier.uri", "").strip()
        if uri_str:
            handles = extract_handles_from_uri(uri_str)
            dois = extract_dois_from_uri(uri_str)

            # Add repository DOI as electronic version (if starts with 10.13025)
            for doi in dois:
                if doi.startswith("https://doi.org/10.13025") and doi not in existing_dois:
                    access_type = "EMBARGOED" if embargo_active else "OPEN"

                    ev = build_electronic_version(
                        doi=doi,
                        version_type_uri="/dk/atira/pure/researchoutput/electronicversion/versiontype/authorsversion",
                        access_type=access_type,
                        license_type="CC_BY_NC_ND"
                    )

                    if embargo_active:
                        ev["embargoPeriod"] = {"endDate": embargo_date}

                    if ev:  # Check if not None
                        updated_evs.append(ev)
                        break  # only one repo DOI expected

            # Add handles to Links
            if handles:
                existing_links = pure_record.get("links", [])
                existing_link_urls = {normalize_handle(link.get("url", "")) for link in existing_links}
                
                new_links = []
                for handle in handles:
                    normalized_handle = normalize_handle(handle)
                    if normalized_handle not in existing_link_urls:
                        link = build_link(handle, alias="Handle", description="Repository Handle")
                        new_links.append(link)
                
                if new_links:
                    updated_record["links"] = existing_links + new_links

    # --- 5d. Rights (dc.rights.uri) > overwrite for repo version ---
    rights = dspace_row.get("dc.rights", "").strip()
    if rights:
        # Look for repo electronic version in updated list
        for ev in updated_evs:
            doi = normalize_doi(ev.get("doi", ""))
            if doi and doi.startswith("https://doi.org/10.13025"):
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
                ev["licenseType"] = {
                    "uri": f"/dk/atira/pure/core/document/licenses/{license_type.lower()}"
                }
                break

    # --- 5e.  Remove any handles from electronic versions and update the record ---
    updated_evs = [ev for ev in updated_evs if not ev.get("handle")]
    if updated_evs != existing_evs:  # Only add if changed
        updated_record["electronicVersions"] = updated_evs      


    # --- 6. Language (dc.language.iso) > fill if blank ---
    lang = dspace_row.get("dc.language.iso", "").strip()
    lang_code = map_language(lang)
    if lang and not pure_record.get("language", {}).get("uri", ""):
        updated_record["language"] = {
            "uri": f"/dk/atira/pure/core/languages/{lang_code}"
        }

    # --- 7. Abstract (dc.description.abstract) > fill if blank ---
    abstract = dspace_row.get("dc.description.abstract", "").strip()
    if abstract and not has_text_in_any_language(pure_record, "abstract"):
        if lang_code == "ga":
            # Workaround: set both en_IE and ga versions to same abstract yo display abstracts in Irish
            updated_record["abstract"] = {"en_IE": escape_special_chars(abstract), "ga": escape_special_chars(abstract)}
        else:
            updated_record["abstract"] = {lang_code: escape_special_chars(abstract)}

    # --- 8. Publisher (dc.publisher) > fill if blank ---
    # publisher = dspace_row.get("dc.publisher", "").strip()
    #TODO: Implement publisher lookup and mapping


    # --- 9. Title (dc.title) > fill if blank ---
    title = dspace_row.get("dc.title", "").strip()
    if title and not pure_record.get("title", {}).get("value", "").strip():
        updated_record["title"] = {"value": escape_special_chars(title)}


    # Write log entry
    log_entry["success"] = success and not errors
    if errors:
        log_entry["error"] = "; ".join(errors)

    before_update_records.append(strip_system_fields(pure_record))

    return updated_record, success


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

    # Set Pure type
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

    # Set language
    lang = dspace_row.get("dc.language.iso", "").strip()
    lang_code = map_language(lang)
    if lang:
        record["language"] = {
            "uri": f"/dk/atira/pure/core/languages/{lang_code}"
        }

    # Set abstract
    abstract = dspace_row.get("dc.description.abstract", "").strip()
    if abstract:
        if lang_code == "ga":
            # Workaround: set both en_IE and ga versions to same abstract yo display abstracts in Irish
            record["abstract"] = {"en_IE": escape_special_chars(abstract), "ga": escape_special_chars(abstract)}
        else:
            record["abstract"] = {lang_code: escape_special_chars(abstract)}

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
            # matched_person should NEVER be None if matches is non-empty
            if not matched_person:
                print(f"      ❌ ERROR: resolve_author_duplicate returned None for {len(matches)} matches!")
                unmatched_authors.append(author_name)
                continue
                
            # Check if person has valid UUIDs
            has_valid_internal = matched_person.get("internal", False) and matched_person.get("internalUUIDs")
            has_valid_external = matched_person.get("external", False) and matched_person.get("externalUUIDs")
            
            if not has_valid_internal and not has_valid_external:
                print(f"      ⚠️ Matched person has no valid UUIDs (internal={matched_person.get('internal')}, external={matched_person.get('external')}) — adding to unmatched")
                unmatched_authors.append(author_name)
                continue
                
            if has_valid_internal:
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
                    if "externalOrganizations" in matched_person:
                        contributor["externalOrganizations"] = [
                            {
                                "systemName": "ExternalOrganization",
                                "uuid": org_uuid
                            }
                            for org_uuid in matched_person["externalOrganizations"]
                        ]
                    mapped_contributors.append(contributor)
                    print(f"      ✅ Added as InternalContributor: {matched_person.get('firstName')} {matched_person.get('lastName')}")

            elif has_valid_external:
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
            print(f"      ⚠️ No matches found — adding to unmatched")  # Log
            unmatched_authors.append(author_name)

    # Check if we have any contributors - if not, skip this record
    if not mapped_contributors:
        print(f"❌ No matched contributors found for record {dspace_row.get("dc.title", "")} - skipping")
        return None  # Return None to indicate record should be skipped
    
    # Always ensure author info is present
    record["contributors"] = mapped_contributors

    print(f"✅ Added {len(mapped_contributors)} contributors")
    
    # Collect ALL organizations from ALL contributors
    all_internal_org_uuids = set()
    all_external_org_uuids = set()
    first_internal_org_uuid = None  # Track first internal contributor's first organization
    
    for i, contributor in enumerate(mapped_contributors):
        # Collect from internal organizations
        if "organizations" in contributor:
            for j, org in enumerate(contributor["organizations"]):
                org_uuid = org.get("uuid")
                if org_uuid:
                    all_internal_org_uuids.add(org_uuid)
                    # Capture first internal contributor's first organization
                    if i == 0 and j == 0 and first_internal_org_uuid is None:
                        first_internal_org_uuid = org_uuid
        
        # Collect from external organizations
        if "externalOrganizations" in contributor:
            for org in contributor["externalOrganizations"]:
                org_uuid = org.get("uuid")
                if org_uuid:
                    all_external_org_uuids.add(org_uuid)
    
    # Set top-level organizations with unique internal orgs
    if all_internal_org_uuids:
        record["organizations"] = [
            {
                "systemName": "Organization",
                "uuid": org_uuid
            }
            for org_uuid in all_internal_org_uuids
        ]
        # Add default University of Galway if not already present
        if "1becab14-37ce-4810-9b95-fc014063bcae" not in all_internal_org_uuids:
            record["organizations"].append({
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
            })
    
    # Set top-level externalOrganizations with unique external orgs
    if all_external_org_uuids:
        record["externalOrganizations"] = [
            {
                "systemName": "ExternalOrganization",
                "uuid": org_uuid
            }
            for org_uuid in all_external_org_uuids
        ]
    
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
        publisher_doi = normalize_doi(publisher_doi)
        ev = build_electronic_version(publisher_doi, "/dk/atira/pure/researchoutput/electronicversion/versiontype/publishersversion")
        record["electronicVersions"] = [ev]

    # Set repository DOI & Handle
    embargo_date = dspace_row.get("dc.date.embargo", "").strip()
    embargo_desc = dspace_row.get("dc.description.embargo", "").strip()
    embargo_active = bool(embargo_date and embargo_date > TODAY) or bool(embargo_desc and embargo_desc > TODAY)

    uri_str = dspace_row.get("dc.identifier.uri", "").strip()
    if uri_str:
        handles = extract_handles_from_uri(uri_str)
        dois = extract_dois_from_uri(uri_str)

        for doi in dois:
            doi = normalize_doi(doi)
            if doi.startswith("https://doi.org/10.13025"):
                access_type = "EMBARGOED" if embargo_active else "OPEN"

                ev = build_electronic_version(
                    doi=doi,
                    version_type_uri="/dk/atira/pure/researchoutput/electronicversion/versiontype/authorsversion",
                    access_type=access_type,
                    license_type="CC_BY_NC_ND"
                )

                if embargo_active:
                    ev["embargoPeriod"] = {"endDate": embargo_date}
                if ev:  # Check if not None
                    if "electronicVersions" not in record:
                        record["electronicVersions"] = []
                    record["electronicVersions"].append(ev)
                    break # only one repo DOI expected

        for handle in handles:
            link = build_link(handle, alias="Handle", description="Repository Handle")
            if "links" not in record:
                record["links"] = []
            record["links"].append(link)

    # Set rights
    rights = dspace_row.get("dc.rights", "").strip()
    if rights:
        # Look for repo electronic version
        repo_ev = None
        for ev in record.get("electronicVersions", []):
            doi = normalize_doi(ev.get("doi", ""))
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
    before_update_records = []

    # Group Pure records by identifiers for fast lookup
    pure_by_doi = defaultdict(list)
    pure_by_handle = defaultdict(list)
    pure_by_repo_doi = defaultdict(list)  # For repository DOIs (10.13025)
    pure_by_title = defaultdict(list)

    for item in pure_items:
        # Index by Publisher DOI (from electronic versions)
        for ev in item.get("electronicVersions", []):
            doi = ev.get("doi", "")
            if doi:
                if "10.13025" in doi:
                    pure_by_repo_doi[normalize_doi(doi)].append(item)
                # Index handles found in electronic versions (DOIs that look like handles)
                elif "hdl.handle.net" in doi:
                    pure_by_handle[normalize_handle(doi)].append(item)
                else:
                    pure_by_doi[normalize_doi(doi)].append(item)

        # Index by links (including handles from links)
        for link in item.get("links", []):
            url = link.get("url", "")
            if url and "hdl.handle.net" in url:
                pure_by_handle[normalize_handle(url)].append(item)
        
        # Index by title
        title = item.get("title", {}).get("value", "")
        if title:
            pure_by_title[normalize(title)].append(item)

    # Process each DSpace row with tqdm progress bar
    print(f"Processing {len(dspace_rows)} DSpace records...")
    TITLE_SIMILARITY_THRESHOLD = 0.9  # 90% match required for title similarity
    
    for i, row in enumerate(tqdm(dspace_rows, desc="Matching Records", unit="record")):
        log_entry = {
            "handle": None,
            "uuid": None,
            "pureType": None,
            "matched": False,
            "duplicates": False,
            "success": False,
            "error": None,
            "matches": []
        }

        # Extract handles from URI
        handles = extract_handles_from_uri(row.get("dc.identifier.uri", ""))
        if handles:
            log_entry["handle"] = handles[0]  # Use first handle

        # Extract repository DOI from dc.identifier.uri
        repo_dois = extract_dois_from_uri(row.get("dc.identifier.uri", ""))

        matched_records = []
        match_type = None  # Track which match method was used

        # 1. Try to match by Publisher DOI
        publisher_doi = row.get("dc.identifier.doi", "").strip()
        if publisher_doi:
            normalized_doi = normalize_doi(publisher_doi)
            if normalized_doi in pure_by_doi:
                matched_records.extend(pure_by_doi[normalized_doi])
                match_type = "Publisher DOI"

        # 2. Try to match by Repository DOI
        if not matched_records and repo_dois:
            for repo_doi in repo_dois:
                normalized_repo_doi = normalize_doi(repo_doi)
                if normalized_repo_doi in pure_by_repo_doi:
                    matched_records.extend(pure_by_repo_doi[normalized_repo_doi])
                    match_type = "Repository DOI"
                    break

        # 3. Try to match by Handle (from both links and electronic versions)
        if not matched_records:
            for handle in handles:
                normalized_handle = normalize_handle(handle)
                if normalized_handle in pure_by_handle:
                    matched_records.extend(pure_by_handle[normalized_handle])
                    match_type = "Handle"
                    break

        # 4. Try to match by Title Similarity (as fallback)
        if not matched_records:
            title = row.get("dc.title", "").strip()
            if title:
                normalized_title = normalize(title)
                # First try exact match
                if normalized_title in pure_by_title:
                    matched_records.extend(pure_by_title[normalized_title])
                    match_type = "Title (Exact)"
                else:
                    # Try similarity matching against all titles
                    best_match = None
                    best_similarity = 0
                    for pure_item in pure_items:
                        pure_title = pure_item.get("title", {}).get("value", "")
                        if pure_title:
                            pure_subtitle = pure_item.get("subTitle", {}).get("value", "")
                            if pure_subtitle:
                                pure_title += f": {pure_subtitle}"
                            similarity, is_match = calculate_title_similarity(
                                title, pure_title, TITLE_SIMILARITY_THRESHOLD
                            )
                            if is_match and similarity > best_similarity:
                                best_match = pure_item
                                best_similarity = similarity
                    
                    if best_match:
                        matched_records = [best_match]
                        match_type = f"Title Similarity ({best_similarity:.1%})"

        # Record all matched records in log_entry
        for matched_record in matched_records:
            log_entry["matches"].append({
                "pureUUID": matched_record.get("uuid", ""),
                "title": matched_record.get("title", {}).get("value", ""),
                "matchType": match_type
            })

        # Resolve duplicate records
        if len(matched_records) > 1:
            log_entry["duplicates"] = True
            chosen_record = resolve_record_duplicate(matched_records)
            if chosen_record:
                matched_records = [chosen_record]

        if matched_records:
            log_entry["matched"] = True
            record = matched_records[0]
            log_entry["uuid"] = record.get("uuid", "")
            log_entry["pureType"] = record.get("type", {}).get("uri", "")
            log_entry["matchType"] = match_type

            try:
                updated_record, success = update_record_from_dspace(record, row, person_mapping, log_entry, before_update_records)
                log_entry["success"] = success
                if success:
                    # Save to matched folder
                    type_key = get_pure_type_key(log_entry["pureType"])
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
                    log_entry["pureType"] = new_record.get("type", {}).get("uri", "")

                    # Save to unmatched folder
                    type_key = get_pure_type_key(log_entry["pureType"])
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

    # log_csv_path = os.path.join(LOG_DIR, f"status_log_{TODAY}.csv")
    # with open(log_csv_path, 'w', newline='', encoding='utf-8') as f:
    #     writer = csv.DictWriter(f, fieldnames=log_entries[0].keys())
    #     writer.writeheader()
    #     writer.writerows(log_entries)

    error_log_path = os.path.join(LOG_DIR, f"error_log_{TODAY}.log")
    with open(error_log_path, 'w', encoding='utf-8') as f:
        for err in error_log:
            f.write(err + "\n")

    before_update_path = os.path.join(LOG_DIR, f"matched_records_before_updates_{TODAY}.json")
    with open(before_update_path, "w", encoding="utf-8") as f:
        json.dump(before_update_records, f, indent=2, ensure_ascii=False)

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