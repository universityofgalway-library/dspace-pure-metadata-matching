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

TODAY = date.today().isoformat()

OVERRIDE_MODE = False  # Change to True to override existing Pure data

DSPACE_CSV = "./dspace_data/test_samples/dspace_test_sample_2026-02-12.csv"
PURE_JSON = "./pure_research_outputs/pure_test_research-outputs_2026-02-03.json"
PERSON_MAPPING_JSON = "./author_matching/2026-02-12/test_authors_all_2026-02-12.json"
ORGANIZATION_MAPPING_JSON = "./pure_entities/organizations_mapping_2026-01-21.json"
OUTPUT_DIR = f"./record_matching/test_output_{TODAY}"
MATCHED_DIR = os.path.join(OUTPUT_DIR, "matched")
UNMATCHED_DIR = os.path.join(OUTPUT_DIR, "unmatched")
LOG_DIR = os.path.join(OUTPUT_DIR, "logs")
NO_AUTHOR_CSV = os.path.join(OUTPUT_DIR, f"no_author_records_{TODAY}.csv")
API_KEY = os.getenv("PURE_ROOT_API_KEY", "")
BASE_URL = "https://galway-staging.elsevierpure.com/ws/api/"

DOI_REGEX = re.compile(r'^(?:https?://)?(?:doi\.org/|doi:)?(10\.\S+)$', re.IGNORECASE)
HANDLE_REGEX = re.compile(r'^(?:https?://hdl\.handle\.net/)?(10379/\S+)$', re.IGNORECASE)

EXTERNAL_ORGS_TO_IGNORE = [
    "c3dd2704-6c2e-4b9c-861d-6c9959c9a612",    # "University of Galway" 
    "4f1dc9e7-a654-4b84-8704-efeab9d69875",    # "University of Galway" 
    "688759fc-d6e2-41a2-aef7-49fb5d228634",    # "Univbersity of Galway" 
    "8f6fd722-2dc6-4cd1-8568-e232088b8f24",    # "NUI Galway" 
    "d43008f7-0efa-41ce-9a28-c4aba2a335c5",    # "NUI Galway" 
    "d40f2787-74f3-4b63-8151-89abc1919538",    # "NUI Galway" 
    "67e06257-a759-43e2-877e-ad1a7846e711",    # "National University of Ireland Galway " 
    "5c0ba446-1322-4287-9bfb-cdfe607c606e",    # "National University of Ireland Galway" 
    "18c76cc4-daaf-49c4-9867-7b0837b4a95b",    # "National University of Ireland ¡V Galway" 
    "132c1680-5865-48ae-89c8-bd278d99832b",    # "National University of Ireland – Galway" 
    "5c091814-92d4-4b6f-b50e-816725f105f8",    # "National University of Ireland, Galway " 
    "cdc9d89f-b737-47ef-8cce-88fb619d1438",    # "National University of Ireland, Galway" 
    "3d1d93ed-6e42-4cd0-af67-100c6d87a1a1",    # "National University of Ireland Galway." 
    "3c5b13f5-1f04-494b-be48-c67eacd43dcf",    # "National university of Ireland Galway, Ireland " 
    "0f02b3d9-dbf1-4970-9927-876ec82f895e",    # "National University of Ireland Galway, Ireland" 
    "bccbb32b-8a4b-471f-a2ba-3a836479a0e7",    # "National University of Ireland Galway (Ireland)" 
    "ed37f922-87e1-49d3-a30a-ac63d0322a87",    # "National University of Ireland, Galway." 
    "684d8f18-0a1b-47cc-88b6-4fc50e8cc1cd",    # "National University of Ireland, Galway, Ireland" 
    "05dd5c35-3f2a-4c17-b45e-b3ee2dffffed",    # "National University of Ireland, Galway, Galway, Ireland" 
    "63da70e1-005a-45a6-a4d5-1cb161b5b72e",    # "National University of Ireland, Galway\t" 
    "9e8c03cb-cfc3-4a91-aeb9-f65dd03dc42d",    # "National University of Ireland, Galway / UCG" 
    "9ab586e4-be82-418b-9056-444f2b71faa0",    # "National University of Ireland, Galway (NUIG)" 
    "44cc3e64-03d8-43c5-81fc-d712f335642b",    # "National University of Ireland, Galway (formerly University College Galway)" 
    "3c493970-03e5-4670-b223-facf3a94dc2e",    # "National University of Ireland, Galway  " 
    "689a3221-88fd-4d2e-8c20-74aeb22eb5ec",    # "National University of Ireland-Galway" 
    "f026cf31-52e3-4aa3-a609-54a50ddd962b",    # "National University of Ireland—Galway" 
    "0dc1af88-f709-4304-8a44-ad3178e1edb2",    # "National University of Ireland Galway College" 
    "78363204-c24b-4e1c-a3e0-1e80614c1978",    # "National University of Ireland‐Galway" 
    "6d370e14-c9b6-4749-8680-6d513e02976b"     # "National University of Ireland Galway (NUI Galway)" 
]

SYSTEM_FIELDS_TO_EXCLUDE = {
    "createdBy",
    "createdDate",
    "modifiedBy",
    "modifiedDate",
    "prettyUrlIdentifiers",
    "version",
}

LANG_MAP = {
        "eng": "en_IE",
        "fre": "fr_FR",
        "ger": "de_DE",
        "spa": "es_ES",
        "gle": "ga"
        # Add more as needed
    }

if not API_KEY:
    print("⚠️ WARNING: PURE_API_KEY not found in environment variables.")
    print("   External person duplicate resolution will be skipped.")

os.makedirs(MATCHED_DIR, exist_ok=True)
os.makedirs(UNMATCHED_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)

# --- TYPE MAPPING ---
dspace_pure_subtype_map = {
    "journal article": "/dk/atira/pure/researchoutput/researchoutputtypes/contributiontojournal/article",
    "review article": "/dk/atira/pure/researchoutput/researchoutputtypes/contributiontojournal/systematicreview",
    "review": "/dk/atira/pure/researchoutput/researchoutputtypes/contributiontojournal/systematicreview",
    # "doctoral thesis": "/dk/atira/pure/researchoutput/researchoutputtypes/thesis/doc",
    # "master thesis": "/dk/atira/pure/researchoutput/researchoutputtypes/thesis/master",
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

_person_metadata_cache = {}
_external_person_metadata_cache = {}

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

def map_language(lang, lang_map=LANG_MAP):

    lang_code = lang_map.get(lang.lower(), "en_IE")
    return lang_code

def has_text_in_any_language(obj, key, languages=LANG_MAP.values()):
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
        "Memorandum",
        "NonTextual"
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
        # Try to get journal UUID from DSpace row
        journal_uuid = dspace_row.get("journal_uuid", "").strip()
        
        if journal_uuid:
            record["journalAssociation"] = {
                "journal": {
                    "systemName": "Journal",
                    "uuid": journal_uuid
                }
            }
        else:
            # record["journalAssociation"] = {
            #         "journal": {
            #             "systemName": "Journal",
            #             "uuid": "f0da45fc-fec1-42f5-80a9-c1446ccce300"  # Placeholder UUID for TEST JOURNAL (UAT)
            #             }
            #     }   
                 
            # No journal UUID found - change to OtherContribution
            print(f"    ⚠️ No journal UUID found for {type_disc} - changing to OtherContribution")
            record["typeDiscriminator"] = "OtherContribution"
            record["type"]["uri"] = "/dk/atira/pure/researchoutput/researchoutputtypes/othercontribution/other"
            record["peerReview"] = False
            return record
        
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

def parse_contributors_by_role(dspace_row):
    """Parse all contributor types from DSpace and return dict by role"""
    contributors_by_role = {}
    
    # Parse authors
    authors = parse_author_names(dspace_row.get("dc.contributor.author", ""))
    if authors:
        contributors_by_role["author"] = authors
    
    # Parse editors
    editors = parse_author_names(dspace_row.get("dc.contributor.editor", ""))
    if editors:
        contributors_by_role["editor"] = editors
    
    # Parse translators
    translators = parse_author_names(dspace_row.get("dc.contributor.translator", ""))
    if translators:
        contributors_by_role["translator"] = translators
    
    # Parse illustrators
    illustrators = parse_author_names(dspace_row.get("dc.contributor.illustrator", ""))
    if illustrators:
        contributors_by_role["illustrator"] = illustrators
    
    return contributors_by_role

def build_contributor(matched_person, role, pure_type_key):
    """Build a contributor object from matched person and role"""
    first = matched_person.get("firstName", "")
    last = matched_person.get("lastName", "")
    
    has_valid_internal = matched_person.get("internal", False) and matched_person.get("internalUUIDs")
    has_valid_external = matched_person.get("external", False) and matched_person.get("externalUUIDs")
    
    if not has_valid_internal and not has_valid_external:
        return None
    
    # Map role to URI - use pure_type_key for dynamic type
    role_uri_map = {
        "author": f"/dk/atira/pure/researchoutput/roles/{pure_type_key.lower()}/author",
        "editor": f"/dk/atira/pure/researchoutput/roles/{pure_type_key.lower()}/editor",
        "translator": f"/dk/atira/pure/researchoutput/roles/{pure_type_key.lower()}/translator",
        "illustrator": f"/dk/atira/pure/researchoutput/roles/{pure_type_key.lower()}/illustrator"
    }
    
    role_term_map = {
        "author": "Author",
        "editor": "Editor",
        "translator": "Translator",
        "illustrator": "Illustrator"
    }
    
    uuid_value = None
    if has_valid_internal:
        uuid_value = extract_uuid(matched_person.get("internalUUIDs")[0])
        contributor = {
            "typeDiscriminator": "InternalContributorAssociation",
            "name": {
                "firstName": first,
                "lastName": last
            },
            "role": {
                "uri": role_uri_map.get(role, role_uri_map["author"]),
                "term": {"en_IE": role_term_map.get(role, "Author")}
            },
            "person": {
                "systemName": "Person",
                "uuid": uuid_value
            }
        }
        # For internal authors, ONLY use primaryOrganisationAssociation
        if "primaryOrganisationAssociation" in matched_person and matched_person["primaryOrganisationAssociation"]:
            contributor["organizations"] = [
                {
                    "systemName": "Organization",
                    "uuid": matched_person["primaryOrganisationAssociation"]
                }
            ]

        return contributor
    
    elif has_valid_external:
        uuid_value = extract_uuid(matched_person.get("externalUUIDs")[0])
        contributor = {
            "typeDiscriminator": "ExternalContributorAssociation",
            "name": {
                "firstName": first,
                "lastName": last
            },
            "role": {
                "uri": role_uri_map.get(role, role_uri_map["author"]),
                "term": {"en_IE": role_term_map.get(role, "Author")}
            },
            "externalPerson": {
                "systemName": "ExternalPerson",
                "uuid": uuid_value
            }
        }
        # For external authors, filter out EXTERNAL_ORGS_TO_IGNORE unless it's the only one
        if "externalOrganizations" in matched_person and matched_person["externalOrganizations"]:
            external_orgs = matched_person["externalOrganizations"]
            
            # Filter out ignored organizations
            filtered_external_orgs = [
                org_uuid for org_uuid in external_orgs
                if org_uuid not in EXTERNAL_ORGS_TO_IGNORE
            ]
            
            # If all orgs are in ignore list, keep the first one from the original list
            if not filtered_external_orgs and external_orgs:
                filtered_external_orgs = [external_orgs[0]]
            
            # Add to contributor if we have any orgs
            if filtered_external_orgs:
                contributor["externalOrganizations"] = [
                    {
                        "systemName": "ExternalOrganization",
                        "uuid": org_uuid
                    }
                    for org_uuid in filtered_external_orgs
                ]
        
        return contributor
    
    return None

def build_person_name_index(person_mapping):
    """Build a comprehensive index of all person name variations for O(1) lookup"""
    person_index = {}
    
    for person in person_mapping:
        p_first = person.get("firstName", "")
        p_last = person.get("lastName", "")
        alt_firsts = person.get("alternativeFirstName", []) or []
        alt_lasts = person.get("alternativeLastName", []) or []
        
        # Build complete lists
        all_firsts = [p_first] if p_first else []
        all_firsts.extend(alt_firsts)
        all_lasts = [p_last] if p_last else []
        all_lasts.extend(alt_lasts)
        
        # Index all combinations
        for af in all_firsts:
            for al in all_lasts:
                # Normal order: (first, last)
                key1 = (normalize(af), normalize(al))
                if key1 not in person_index:
                    person_index[key1] = []
                person_index[key1].append(person)
                
                # Swapped order: (last, first)
                key2 = (normalize(al), normalize(af))
                if key2 not in person_index:
                    person_index[key2] = []
                person_index[key2].append(person)
    
    return person_index

def find_person_match(person_name, person_index):
    """Find matching person using pre-built index - O(1) lookup"""
    first, last = "", ""
    if "," in person_name:
        parts = [p.strip() for p in person_name.split(",", 1)]
        last = parts[0]
        first = parts[1] if len(parts) > 1 else ""
    else:
        parts = person_name.split()
        if len(parts) >= 2:
            first = " ".join(parts[:-1])
            last = parts[-1]
        else:
            first = person_name
            last = ""
    
    key = (normalize(first), normalize(last))
    return person_index.get(key, [])


def resolve_author_duplicate(matches):
    """Prefer Person over External Person, then by visibility (internal), then by metadata richness (both internal and external)"""
    if not matches:
        return None

    def score(person):
        internal = person.get("internal", False)
        external = person.get("external", False)
        type_score = 2 if internal else (1 if external else 0)

        vis_score = 0
        if internal:
            internal_uuids = person.get("internalUUIDs", [])
            if internal_uuids and isinstance(internal_uuids, list) and len(internal_uuids) > 0:
                if isinstance(internal_uuids[0], dict):
                    vis = internal_uuids[0].get("visibility", "")
                    if vis in ["FREE", "CAMPUS"]:
                        vis_score = 2
                    elif vis in ["BACKEND", "CONFIDENTIAL"]:
                        vis_score = 1

        metadata_score = 0
        
        if internal:
            internal_uuids = person.get("internalUUIDs", [])
            if internal_uuids:
                best_uuid = None
                max_fields = -1
                for uuid_obj in internal_uuids:
                    uuid_value = extract_uuid(uuid_obj)
                    if not API_KEY:
                        best_uuid = uuid_value
                        max_fields = 0
                        break
                    
                    # Check cache first
                    if uuid_value in _person_metadata_cache:
                        field_count = _person_metadata_cache[uuid_value]
                        if field_count > max_fields:
                            max_fields = field_count
                            best_uuid = uuid_value
                        continue
                    
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
                            field_count = sum(1 for k, v in internal_person.items() if k not in ["uuid", "createdBy", "modifiedBy", "version", "portalUrl", "prettyUrlIdentifiers", "previousUuids"] and v)
                            _person_metadata_cache[uuid_value] = field_count
                            if field_count > max_fields:
                                max_fields = field_count
                                best_uuid = uuid_value 
                    except Exception as e:
                        pass
                metadata_score = max_fields if best_uuid else 0
        
        elif external:
            external_uuids = person.get("externalUUIDs", [])
            if external_uuids:
                best_uuid = None
                max_fields = -1
                for uuid_value in external_uuids:
                    if not API_KEY:
                        best_uuid = uuid_value
                        max_fields = 0
                        break
                    
                    # Check cache first
                    if uuid_value in _external_person_metadata_cache:
                        field_count = _external_person_metadata_cache[uuid_value]
                        if field_count > max_fields:
                            max_fields = field_count
                            best_uuid = uuid_value
                        continue
                    
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
                            field_count = sum(1 for k, v in external_person.items() if k not in ["uuid", "createdBy", "modifiedBy", "version", "portalUrl", "prettyUrlIdentifiers", "previousUuids"] and v)
                            _external_person_metadata_cache[uuid_value] = field_count
                            if field_count > max_fields:
                                max_fields = field_count
                                best_uuid = uuid_value
                    except Exception as e:
                        pass
                metadata_score = max_fields if best_uuid else 0

        return (type_score, vis_score, metadata_score)

    sorted_matches = sorted(matches, key=score, reverse=True)
    return sorted_matches[0]


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


def validate_organization(org_uuid, api_key, base_url):
    """
    Validate if an organization UUID exists in Pure.
    Returns True if found, False otherwise.
    """
    try:
        response = requests.get(
            f"{base_url}organizations/{org_uuid}",
            headers={
                "accept": "application/json",
                "api-key": api_key
            },
            timeout=10
        )
        return response.status_code == 200
    except Exception:
        return False


def validate_and_fix_organizations(contributors, api_key, base_url):
    """
    Validate all internal organization UUIDs for contributors.
    Move invalid internal orgs to externalOrganizations.
    Returns updated contributors list.
    """
    if not api_key:
        print("  ⚠️ No API key - skipping organization validation")
        return contributors
    
    updated_contributors = []
    
    for contributor in contributors:
        if not contributor:
            continue
        
        # Check internal organizations
        internal_orgs = contributor.get("organizations", [])
        if internal_orgs:
            valid_internal_orgs = []
            invalid_orgs = []
            
            for org in internal_orgs:
                org_uuid = org.get("uuid")
                if org_uuid:
                    if validate_organization(org_uuid, api_key, base_url):
                        valid_internal_orgs.append(org)
                    else:
                        print(f"    ⚠️ Invalid internal org UUID {org_uuid} - moving to external")
                        invalid_orgs.append({
                            "systemName": "ExternalOrganization",
                            "uuid": org_uuid
                        })
            
            # Update organizations
            if valid_internal_orgs:
                contributor["organizations"] = valid_internal_orgs
            else:
                # Remove organizations key if all were invalid
                contributor.pop("organizations", None)
            
            # Add invalid orgs to externalOrganizations
            if invalid_orgs:
                existing_external = contributor.get("externalOrganizations", [])
                # Avoid duplicates
                existing_external_uuids = {org.get("uuid") for org in existing_external}
                for invalid_org in invalid_orgs:
                    if invalid_org["uuid"] not in existing_external_uuids:
                        existing_external.append(invalid_org)
                contributor["externalOrganizations"] = existing_external
        
        updated_contributors.append(contributor)
    
    return updated_contributors


def collect_validated_organizations(contributors):
    """
    Collect all validated organizations from contributors.
    Returns tuple: (internal_org_uuids, external_org_uuids)
    """
    internal_org_uuids = set()
    external_org_uuids = set()
    
    for contributor in contributors:
        if not contributor:
            continue
        
        # Collect internal organizations
        if "organizations" in contributor:
            for org in contributor["organizations"]:
                org_uuid = org.get("uuid")
                if org_uuid:
                    internal_org_uuids.add(org_uuid)
        
        # Collect external organizations
        if "externalOrganizations" in contributor:
            for org in contributor["externalOrganizations"]:
                org_uuid = org.get("uuid")
                if org_uuid:
                    external_org_uuids.add(org_uuid)
    
    return list(internal_org_uuids), list(external_org_uuids)

def resolve_funder_duplicate(matches, api_key, base_url):
    """
    Resolve duplicate organization matches for funders.
    Prefer: 1) internal > external, 2) FREE > CAMPUS > others, 
    3) most complete record, 4) first match
    """
    if not matches:
        return None
    
    def score(org):
        internal = org.get("internal", False)
        external = org.get("external", False)
        
        # 1. Prefer internal
        type_score = 2 if internal else (1 if external else 0)
        
        # 2. Prefer visibility
        vis = org.get("visibility", "")
        if vis == "FREE":
            vis_score = 3
        elif vis == "CAMPUS":
            vis_score = 2
        elif vis in ["BACKEND", "CONFIDENTIAL"]:
            vis_score = 1
        else:
            vis_score = 0
        
        return (type_score, vis_score)
    
    sorted_matches = sorted(matches, key=score, reverse=True)
    return sorted_matches[0]


def parse_funders(funder_str):
    """Parse semicolon-separated funder names from DSpace"""
    if not funder_str:
        return []
    return [f.strip() for f in funder_str.split(";") if f.strip()]


def build_organization_name_index(organization_mapping):
    """Build index for O(1) organization name lookup"""
    org_index = {}
    
    for org in organization_mapping:
        org_names = org.get("name", [])
        for org_name in org_names:
            normalized = normalize(org_name)
            if normalized not in org_index:
                org_index[normalized] = []
            org_index[normalized].append(org)
    
    return org_index

def find_funder_match(funder_name, org_index):
    """Find matching organization using pre-built index"""
    normalized_name = normalize(funder_name)
    return org_index.get(normalized_name, [])


def build_funding_organizations(funder_uuids_with_type):
    """
    Build fundingDetails array from list of (uuid, is_internal) tuples.
    Returns list of funding detail objects (one per funder).
    """
    funding_details = []
    
    for uuid, is_internal in funder_uuids_with_type:
        if is_internal:
            funding_details.append({
                "fundingOrganizations": [
                    {
                        "organizationRef": {
                            "systemName": "Organization",
                            "uuid": uuid
                        }
                    }
                ]
            })
        else:
            funding_details.append({
                "fundingOrganizations": [
                    {
                        "externalOrganizationRef": {
                            "systemName": "ExternalOrganization",
                            "uuid": uuid
                        }
                    }
                ]
            })
    
    return funding_details

def parse_date(date_string):
    """
    Parse date string with multiple formats and separators.
    Handles: yyyy-mm-dd, dd-mm-yyyy, yyyy/mm/dd, dd/mm/yyyy
    Returns: (year, month, day) tuple
    """
    if not date_string:
        return (1970, 1, 1)
    
    # Try different separators
    parts = None
    separator = None
    for sep in ['-', '/']:
        if sep in date_string:
            parts = date_string.split(sep)
            separator = sep
            break
    
    if not parts or len(parts) < 3:
        # Handle year-only format
        if date_string.isdigit() and len(date_string) == 4:
            return (int(date_string), 1, 1)
        return (1970, 1, 1)
    
    # Extract numeric parts
    try:
        part1 = int(parts[0]) if parts[0].isdigit() else 1970
        part2 = int(parts[1]) if parts[1].isdigit() else 1
        part3 = int(parts[2]) if parts[2].isdigit() else 1
    except (ValueError, IndexError):
        return (1970, 1, 1)
    
    # Determine format based on first part
    # If first part > 31, it's likely yyyy-mm-dd
    # If first part <= 31 and third part > 31, it's likely dd-mm-yyyy
    if part1 > 31:
        # yyyy-mm-dd or yyyy/mm/dd
        year = part1
        month = part2 if 1 <= part2 <= 12 else 1
        day = part3 if 1 <= part3 <= 31 else 1
    elif part3 > 31:
        # dd-mm-yyyy or dd/mm/yyyy
        day = part1 if 1 <= part1 <= 31 else 1
        month = part2 if 1 <= part2 <= 12 else 1
        year = part3
    else:
        # Ambiguous - default to yyyy-mm-dd if first part could be a year
        if part1 > 12:
            # Likely dd-mm-yyyy (day > 12)
            day = part1 if 1 <= part1 <= 31 else 1
            month = part2 if 1 <= part2 <= 12 else 1
            year = part3
        else:
            # Default to yyyy-mm-dd
            year = part1
            month = part2 if 1 <= part2 <= 12 else 1
            day = part3 if 1 <= part3 <= 31 else 1
    
    return (year, month, day)


def update_record_from_dspace(pure_record, dspace_row, person_index, org_index, log_entry, before_update_records, override_mode=False):
    """
    Update pure_record with DSpace data according to precedence rules.
    Returns updated record and success flag.
    
    Args:
        override_mode: If True, override all fields. If False, follow precedence rules.
    """
    success = True
    errors = []

    # Start with only the UUID - required for updates
    updated_record = {
        "uuid": pure_record.get("uuid")
    }

    pure_type = pure_record.get("typeDiscriminator", "")

    # --- 1. Contributors (authors, editors, translators, illustrators) > add new mapped contributors from DSpace
    contributors_by_role = parse_contributors_by_role(dspace_row)
    
    # Get existing contributors from Pure record (if any)
    existing_contributors = pure_record.get("contributors", [])

    # If override mode is on, ignore existing contributors
    if override_mode:
        existing_contributors = []
        existing_author_keys = set()
        existing_uuids = set()
        existing_by_uuid = {}
        existing_by_name = {}
    else:
        # Create a set of existing contributor keys for fast lookup
        existing_author_keys = set()
        existing_uuids = set()
        existing_by_uuid = {}
        existing_by_name = {}

        for contrib in existing_contributors:
            if not contrib:
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

            # Generate all name combinations
            normal_order = list(product(all_first_names, all_last_names))
            reverse_order = list(product(all_last_names, all_first_names))
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

    # Process DSpace contributors by role and build final contributors list
    final_contributors = []
    unmatched_contributors = []

    for role, contributor_names in contributors_by_role.items():
        print(f"  ➤ Processing {len(contributor_names)} {role}(s)")
        
        for contributor_name in contributor_names:
            print(f"    ➤ Checking match for {role}: '{contributor_name}'")
            matches = find_person_match(contributor_name, person_index)
            
            if matches:
                print(f"      ✅ Found {len(matches)} matches")
                matched_person = resolve_author_duplicate(matches)
                
                if not matched_person:
                    print(f"        ❌ ERROR: resolve_author_duplicate returned None for {len(matches)} matches!")
                    unmatched_contributors.append((role, contributor_name))
                    continue
                
                # Get UUID to check if already exists
                has_valid_internal = matched_person.get("internal", False) and matched_person.get("internalUUIDs")
                has_valid_external = matched_person.get("external", False) and matched_person.get("externalUUIDs")
                
                if not has_valid_internal and not has_valid_external:
                    print(f"        ⚠️ Matched person has no valid UUIDs — adding to unmatched")
                    unmatched_contributors.append((role, contributor_name))
                    continue
                
                uuid_value = None
                if has_valid_internal:
                    uuid_value = extract_uuid(matched_person.get("internalUUIDs")[0])
                elif has_valid_external:
                    uuid_value = extract_uuid(matched_person.get("externalUUIDs")[0])
                
                # Check if this contributor already exists (only if not in override mode)
                first = matched_person.get("firstName", "")
                last = matched_person.get("lastName", "")
                name_key = (normalize(first), normalize(last))
                
                # If override mode is on, always create new contributor
                # If contributor already exists and not in override mode, use existing one
                if not override_mode and uuid_value in existing_by_uuid:
                    print(f"        ℹ️ Contributor already exists (by UUID), using existing: {first} {last}")
                    final_contributors.append(existing_by_uuid[uuid_value])
                elif not override_mode and name_key in existing_by_name:
                    print(f"        ℹ️ Contributor already exists (by name), using existing: {first} {last}")
                    final_contributors.append(existing_by_name[name_key])
                else:
                    # Create new contributor
                    contributor = build_contributor(matched_person, role, pure_type.lower())
                    if contributor:
                        final_contributors.append(contributor)
                        action = "Overriding" if override_mode else "Added new"
                        print(f"        ✅ {action} {role}: {first} {last}")
            else:
                print(f"        ⚠️ No matches found — adding to unmatched")
                unmatched_contributors.append((role, contributor_name))

    # Only update contributors if we have any
    if final_contributors:
        # Validate and fix organizations
        print("  🔍 Validating organization UUIDs...")
        final_contributors = validate_and_fix_organizations(final_contributors, API_KEY, BASE_URL)
        updated_record["contributors"] = final_contributors
    
    # --- 1a. Collect ALL validated organizations from ALL contributors ---
    all_internal_org_uuids, all_external_org_uuids = collect_validated_organizations(
        final_contributors if final_contributors else []
    )
    
    # In override mode, replace organizations entirely
    # In precedence mode, only add if not already present
    if override_mode:
        # Update top-level organizations with unique validated internal orgs
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
    else:
        # Precedence mode: only update if not already present
        if all_internal_org_uuids and not pure_record.get("organizations"):
            updated_record["organizations"] = [
                {
                    "systemName": "Organization",
                    "uuid": org_uuid
                }
                for org_uuid in all_internal_org_uuids
            ]
        
        if all_external_org_uuids and not pure_record.get("externalOrganizations"):
            updated_record["externalOrganizations"] = [
                {
                    "systemName": "ExternalOrganization",
                    "uuid": org_uuid
                }
                for org_uuid in all_external_org_uuids
            ]

    # --- 1b. Managing Organization - Update based on override mode ---
    # Find first internal organization from internal contributors only
    first_internal_org_uuid = None
    for contributor in (final_contributors if final_contributors else []):
        # Only check internal contributors
        if contributor.get("typeDiscriminator") == "InternalContributorAssociation":
            if "organizations" in contributor and contributor["organizations"]:
                first_internal_org_uuid = contributor["organizations"][0].get("uuid")
                if first_internal_org_uuid:
                    break
    
    if override_mode:
        # In override mode, always update managing organization
        if first_internal_org_uuid:
            updated_record["managingOrganization"] = {
                "uuid": first_internal_org_uuid,
                "systemName": "Organization"
            }
            print(f"  ✅ Override: Set managingOrganization to: {first_internal_org_uuid}")
        else:
            # No internal authors - set to Library Repository
            updated_record["managingOrganization"] = {
                "uuid": "a57f818f-e41c-443e-8bea-5183a9c54a6b",
                "systemName": "Organization"
            }
            print(f"  ✅ Override: Set managingOrganization to Library Repository (no internal authors)")
    elif not pure_record.get("managingOrganization", {}).get("uuid"):
        # In precedence mode, only set if not already present
        if first_internal_org_uuid:
            updated_record["managingOrganization"] = {
                "uuid": first_internal_org_uuid,
                "systemName": "Organization"
            }
            print(f"  ✅ Precedence: Set managingOrganization to: {first_internal_org_uuid}")
        else:
            # No internal authors - set to Library Repository
            updated_record["managingOrganization"] = {
                "uuid": "a57f818f-e41c-443e-8bea-5183a9c54a6b",
                "systemName": "Organization"
            }
            print(f"  ✅ Precedence: Set managingOrganization to Library Repository (no internal authors)")

    # --- 1c. Remove author keyword group if all DSpace authors are now matched ---
    if final_contributors:
        existing_keyword_groups = pure_record.get("keywordGroups", [])
        if existing_keyword_groups:
            # Filter out the authors keyword group
            print("  🗑️ Removing authors keyword group if present...")
            filtered_groups = [
                kg for kg in existing_keyword_groups
                if kg.get("logicalName") != "/dk/atira/pure/authors"
            ]
            if filtered_groups:
                # Keep other keyword groups
                updated_record["keywordGroups"] = filtered_groups
            else:
                # Remove keywordGroups entirely if empty
                updated_record["keywordGroups"] = []
    

    # --- 2. Publication Date (dc.date.issued) > fill if blank, upgrade only ---
    issued = dspace_row.get("dc.date.issued", "").strip()
    if issued:
        year, month, day = parse_date(issued)
        
        # Only set if not already set OR if override mode is on
        pub_status = pure_record.get("publicationStatuses", [])
        if not pub_status or override_mode:
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

    # --- 3. Funding Details (sponsorship + funders) ---

    # --- 3a. Sponsorship (dc.description.sponsorship) > fill if blank ---
    sponsorship = dspace_row.get("dc.description.sponsorship", "").strip()
    if sponsorship and (not has_text_in_any_language(pure_record, "fundingText") or override_mode):
        updated_record["fundingText"] = {"en_IE": escape_special_chars(sponsorship)}

    # --- 3b. Funder (dc.contributor.funder) > fill if blank, add new funders, don't overwrite ---
    dspace_funders = parse_funders(dspace_row.get("dc.contributor.funder", ""))
    
    if dspace_funders and len(dspace_funders) > 0:
        print(f"  ➤ Processing {len(dspace_funders)} funders: {dspace_funders}")
        
        # Get existing funding details
        existing_funding_details = pure_record.get("fundingDetails", [])
        
        # If override mode is on, ignore existing funders
        if override_mode:
            existing_funder_uuids = set()
        else:
            # Collect existing funder UUIDs to avoid duplicates
            existing_funder_uuids = set()
            for funding_detail in existing_funding_details:
                for funding_org in funding_detail.get("fundingOrganizations", []):
                    if "organizationRef" in funding_org:
                        existing_funder_uuids.add(funding_org["organizationRef"]["uuid"])
                    elif "externalOrganizationRef" in funding_org:
                        existing_funder_uuids.add(funding_org["externalOrganizationRef"]["uuid"])
        
        # Process new funders
        new_funder_uuids_with_type = []  # List of (uuid, is_internal) tuples
        
        for funder_name in dspace_funders:
            print(f"    ➤ Looking up funder: '{funder_name}'")
            matches = find_funder_match(funder_name, org_index)
            
            if matches:
                print(f"      ✅ Found {len(matches)} matches")
                matched_org = resolve_funder_duplicate(matches, API_KEY, BASE_URL)
                
                if matched_org:
                    uuid = matched_org.get("uuid")
                    is_internal = matched_org.get("internal", False)
                    
                    # Check if already exists (only if not in override mode)
                    if override_mode or uuid not in existing_funder_uuids:
                        new_funder_uuids_with_type.append((uuid, is_internal))
                        existing_funder_uuids.add(uuid)  # Prevent duplicates within new funders
                        action = "Overriding" if override_mode else "Added"
                        print(f"      ✅ {action} funder: {funder_name} (UUID: {uuid}, Internal: {is_internal})")
                    else:
                        print(f"      ℹ️ Funder already exists: {funder_name}")
            else:
                print(f"      ⚠️ No match found for funder: {funder_name}")
        
        # Add new funders to funding details
        if new_funder_uuids_with_type:
            new_funding_details = build_funding_organizations(new_funder_uuids_with_type)
            
            if override_mode:
                # In override mode, replace all funding details
                updated_record["fundingDetails"] = new_funding_details
                print(f"    ✅ Replaced fundingDetails with {len(new_funder_uuids_with_type)} new funders")
            elif existing_funding_details:
                # Append new funding details to existing list
                existing_funding_details.extend(new_funding_details)
                updated_record["fundingDetails"] = existing_funding_details
                print(f"    ✅ Added {len(new_funder_uuids_with_type)} new funders to fundingDetails")
            else:
                # Create new funding details list
                updated_record["fundingDetails"] = new_funding_details
                print(f"    ✅ Added {len(new_funder_uuids_with_type)} new funders to fundingDetails")

    # --- 4. Electronic Versions (DOIs + embargo + rights + access) ---
    existing_evs = pure_record.get("electronicVersions", [])

    # Separate existing EVs into repository and publisher DOIs
    existing_repo_evs = []
    existing_publisher_evs = []
    existing_other_evs = []
    
    for ev in existing_evs:
        doi = normalize_doi(ev.get("doi", ""))
        if "hdl.handle.net" not in doi:
            if doi.startswith("https://doi.org/10.13025"):
                existing_repo_evs.append(ev)
            elif doi and doi.startswith("https://doi.org/"):
                existing_publisher_evs.append(ev)
            else:
                existing_other_evs.append(ev)
    
    existing_repo_dois = [normalize_doi(ev.get("doi", "")) for ev in existing_repo_evs]
    existing_publisher_dois = [normalize_doi(ev.get("doi", "")) for ev in existing_publisher_evs]

    # --- 5a. Publisher DOI (dc.identifier.doi) > add if blank ---
    publisher_doi = dspace_row.get("dc.identifier.doi", "").strip()
    new_publisher_ev = None
    if publisher_doi:
        publisher_doi = normalize_doi(publisher_doi)
        # Check if already present
        if publisher_doi not in existing_publisher_dois:
            # Add as new electronic version
            ev = build_electronic_version(
                doi=publisher_doi,
                version_type_uri="/dk/atira/pure/researchoutput/electronicversion/versiontype/publishersversion",
            )
            if ev:
                new_publisher_ev = ev

    # --- 5b. Embargo (dc.date.embargo / dc.description.embargo) > overwrite for repo version ---
    embargo_date_str = dspace_row.get("dc.date.embargo", "").strip()
    embargo_desc = dspace_row.get("dc.description.embargo", "").strip()
    
    # Parse embargo date using same function as publication date
    embargo_date = None
    if embargo_date_str:
        year, month, day = parse_date(embargo_date_str)
        embargo_date = f"{year:04d}-{month:02d}-{day:02d}"
    
    embargo_active = bool(embargo_date and embargo_date > TODAY)

    repo_ev = existing_repo_evs[0] if existing_repo_evs else None

    if repo_ev:
        # Update existing repo version
        if embargo_date or embargo_desc:
            repo_ev["embargoPeriod"] = {"endDate": embargo_date}
            if embargo_active:
                repo_ev["accessType"] = {"uri": "/dk/atira/pure/core/openaccesspermission/embargoed"}

        # Always enforce version type + license
        repo_ev["versionType"] = {
            "uri": "/dk/atira/pure/researchoutput/electronicversion/versiontype/authorsversion"
        }
        repo_ev["licenseType"] = {
            "uri": "/dk/atira/pure/core/document/licenses/cc_by_nc_nd"
        }

    # --- 5c. Repository DOI & Handle (dc.identifier.uri) > always add ---
    else:
        uri_str = dspace_row.get("dc.identifier.uri", "").strip()
        if uri_str:
            dois = extract_dois_from_uri(uri_str)

            # Add repository DOI as electronic version (if starts with 10.13025)
            for doi in dois:
                if doi.startswith("https://doi.org/10.13025") and doi not in existing_repo_dois:
                    access_type = "EMBARGOED" if embargo_active else "OPEN"

                    ev = build_electronic_version(
                        doi=doi,
                        version_type_uri="/dk/atira/pure/researchoutput/electronicversion/versiontype/authorsversion",
                        access_type=access_type,
                        license_type="CC_BY_NC_ND"
                    )

                    if embargo_active and ev:
                        ev["embargoPeriod"] = {"endDate": embargo_date}

                    if ev:
                        repo_ev = ev
                        break  # only one repo DOI expected


    # --- 5d. Rights (dc.rights.uri) > overwrite for repo version ---
    rights = dspace_row.get("dc.rights", "").strip()
    if rights and repo_ev:
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

    # --- 5e. Build final electronic versions list: repository DOI first, then publisher DOIs, then others ---
    final_evs = []
    
    # Add repository DOI first (if exists)
    if repo_ev:
        # Strip https://doi.org/ prefix from DOI
        if "doi" in repo_ev and repo_ev["doi"].startswith("https://doi.org/"):
            repo_ev["doi"] = repo_ev["doi"].replace("https://doi.org/", "")
        final_evs.append(repo_ev)
    
    # Add publisher DOIs second
    for ev in existing_publisher_evs:
        # Strip https://doi.org/ prefix from DOI
        if "doi" in ev and ev["doi"].startswith("https://doi.org/"):
            ev["doi"] = ev["doi"].replace("https://doi.org/", "")
        final_evs.append(ev)
    
    if new_publisher_ev:
        # Strip https://doi.org/ prefix from DOI
        if "doi" in new_publisher_ev and new_publisher_ev["doi"].startswith("https://doi.org/"):
            new_publisher_ev["doi"] = new_publisher_ev["doi"].replace("https://doi.org/", "")
        final_evs.append(new_publisher_ev)
    
    # Add other electronic versions last
    for ev in existing_other_evs:
        # Strip https://doi.org/ prefix from DOI if present
        if "doi" in ev and isinstance(ev["doi"], str) and ev["doi"].startswith("https://doi.org/"):
            ev["doi"] = ev["doi"].replace("https://doi.org/", "")
        final_evs.append(ev)

    # Only update if changed
    if final_evs != existing_evs:
        updated_record["electronicVersions"] = final_evs

    # --- 5f. Add Handles as links and remove DOI links ---
    uri_str = dspace_row.get("dc.identifier.uri", "").strip()
    existing_links = pure_record.get("links", [])

    # Filter out DOI links from existing links
    filtered_links = []
    for link in existing_links:
        url = link.get("url", "")
        # Keep the link if it's NOT a DOI link
        if not "doi.org" in url:
            filtered_links.append(link)
    
    # Add new handle links
    if uri_str:
        handles = extract_handles_from_uri(uri_str)
        if handles:
            existing_handle_urls = {normalize_handle(link.get("url", "")) for link in filtered_links}
            new_handle_links = []
            for handle in handles:
                normalized_handle = normalize_handle(handle)
                if normalized_handle not in existing_handle_urls:
                    link = build_link(handle, alias="Handle", description="Repository Handle")
                    new_handle_links.append(link)

    if new_handle_links:
        new_handle_links.extend(filtered_links)
        updated_links = new_handle_links
    else:
        updated_links = filtered_links
    
    # Update links only if changed
    if updated_links != existing_links:
        updated_record["links"] = updated_links

    # --- 6. Language (dc.language.iso) > fill if blank ---
    lang = dspace_row.get("dc.language.iso", "").strip()
    lang_code = map_language(lang)
    if lang and (not pure_record.get("language", {}).get("uri", "") or override_mode):
        updated_record["language"] = {
            "uri": f"/dk/atira/pure/core/languages/{lang_code}"
        }

    # --- 7. Abstract (dc.description.abstract) > fill if blank ---
    abstract = dspace_row.get("dc.description.abstract", "").strip()
    if abstract and (not has_text_in_any_language(pure_record, "abstract") or override_mode):
        if lang_code == "ga":
            # Workaround: set both en_IE and ga versions to same abstract yo display abstracts in Irish
            updated_record["abstract"] = {"en_IE": escape_special_chars(abstract), "ga": escape_special_chars(abstract)}
        else:
            updated_record["abstract"] = {lang_code: escape_special_chars(abstract)}

    # --- 8. Title (dc.title) > fill if blank ---
    dspace_title = dspace_row.get("dc.title", "").strip()
    dspace_subtitle = dspace_row.get("dc.title.subtitle", "").strip()
    
    # Combine title and subtitle if both exist
    combined_dspace_title = dspace_title
    if dspace_subtitle:
        combined_dspace_title = f"{dspace_title} {dspace_subtitle}"
    
    # Check if Pure record has any title content
    pure_title = pure_record.get("title", {}).get("value", "").strip()
    pure_subtitle = pure_record.get("subTitle", {}).get("value", "").strip()
    
    # Only update if Pure has no meaningful title OR if override mode is on
    if combined_dspace_title and (not pure_title or override_mode):
        updated_record["title"] = {"value": escape_special_chars(dspace_title)}
        if dspace_subtitle:
            updated_record["subTitle"] = {"value": escape_special_chars(dspace_subtitle)}

    # --- 9. Journal Association (for ContributionToJournal/ContributionToPeriodical) ---
    type_disc = pure_record.get("typeDiscriminator", "")
    
    if type_disc in ["ContributionToJournal", "ContributionToPeriodical"]:
        journal_uuid = dspace_row.get("journal_uuid", "").strip()
        existing_journal = pure_record.get("journalAssociation", {}).get("journal", {}).get("uuid")
        
        # Add journal if we have a UUID and (no existing journal OR override mode is on)
        if journal_uuid and (not existing_journal or override_mode):
            updated_record["journalAssociation"] = {
                "journal": {
                    "systemName": "Journal",
                    "uuid": journal_uuid
                }
            }
            action = "Override" if override_mode else "Added"
            print(f"  ✅ {action}: Set journal association to: {journal_uuid}")
        elif not journal_uuid and not existing_journal:
            # No journal UUID in DSpace and no existing journal - this shouldn't be a journal contribution
            print(f"    ⚠️ No journal UUID found for {type_disc} - record may need type change")

    # --- 10. Set workflow step ---
    updated_record["workflow"] = {
        "step": "approved"
    }

    # Write log entry
    log_entry["success"] = success and not errors
    if errors:
        log_entry["error"] = "; ".join(errors)

    before_update_records.append(strip_system_fields(pure_record))

    return updated_record, success


def create_new_record_from_dspace(dspace_row, person_index, org_index):
    """Create new Pure record from DSpace row"""
    # Escape special characters in title first
    escaped_title = escape_special_chars(dspace_row.get("dc.title", "").strip())
    
    record = {
        "title": {"value": escaped_title},
        "type": {
            "uri": ""
        },
        "category": {
            "uri": "/dk/atira/pure/researchoutput/category/research"
        },
        "language": {
            "uri": "/dk/atira/pure/core/languages/en_IE"
        },
        "electronicVersions": [],
        "links": [],
        "managingOrganization": {
            "uuid": "a57f818f-e41c-443e-8bea-5183a9c54a6b", # Default: Library Repository
            "systemName": "Organization"
            }, 
        "visibility": {
            "key": "FREE"
        },
        "workflow": {
            "step": "approved"
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
        year, month, day = parse_date(issued)
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

    # Set title and subtitle
    dspace_title = dspace_row.get("dc.title", "").strip()
    dspace_subtitle = dspace_row.get("dc.title.subtitle", "").strip()
    
    if dspace_title:
        record["title"] = {"value": escape_special_chars(dspace_title)}
    
    if dspace_subtitle:
        record["subTitle"] = {"value": escape_special_chars(dspace_subtitle)}

    # Set sponsorship
    sponsorship = dspace_row.get("dc.description.sponsorship", "").strip()
    if sponsorship:
        record["fundingText"] = {"en_IE": escape_special_chars(sponsorship)}
    
    # Set funders
    dspace_funders = parse_funders(dspace_row.get("dc.contributor.funder", ""))
    
    if dspace_funders and len(dspace_funders) > 0:
        print(f"  ➤ Processing {len(dspace_funders)} funders: {dspace_funders}")
        
        funder_uuids_with_type = []  # List of (uuid, is_internal) tuples
        
        for funder_name in dspace_funders:
            print(f"    ➤ Looking up funder: '{funder_name}'")
            matches = find_funder_match(funder_name, org_index)
            
            if matches:
                print(f"      ✅ Found {len(matches)} matches")
                matched_org = resolve_funder_duplicate(matches, API_KEY, BASE_URL)
                
                if matched_org:
                    uuid = matched_org.get("uuid")
                    is_internal = matched_org.get("internal", False)
                    funder_uuids_with_type.append((uuid, is_internal))
                    print(f"      ✅ Added funder: {funder_name} (UUID: {uuid}, Internal: {is_internal})")
            else:
                print(f"      ⚠️ No match found for funder: {funder_name}")
        
        # Add funders to record
        if funder_uuids_with_type:
            funding_details = build_funding_organizations(funder_uuids_with_type)
            record["fundingDetails"] = funding_details
            print(f"    ✅ Added {len(funder_uuids_with_type)} funders to fundingDetails")

    # Set contributors (all roles)
    contributors_by_role = parse_contributors_by_role(dspace_row)
    print(f"✅ Processing contributors: {sum(len(names) for names in contributors_by_role.values())} total")

    mapped_contributors = []
    unmatched_contributors = []

    for role, contributor_names in contributors_by_role.items():
        print(f"  ➤ Processing {len(contributor_names)} {role}(s)")
        
        for contributor_name in contributor_names:
            print(f"    ➤ Checking match for {role}: '{contributor_name}'")
            matches = find_person_match(contributor_name, person_index)
            
            if matches:
                print(f"      ✅ Found {len(matches)} matches")
                matched_person = resolve_author_duplicate(matches)
                
                if not matched_person:
                    print(f"        ❌ ERROR: resolve_author_duplicate returned None!")
                    unmatched_contributors.append((role, contributor_name))
                    continue
                
                # Check if person has valid UUIDs
                has_valid_internal = matched_person.get("internal", False) and matched_person.get("internalUUIDs")
                has_valid_external = matched_person.get("external", False) and matched_person.get("externalUUIDs")
                
                if not has_valid_internal and not has_valid_external:
                    print(f"        ⚠️ Matched person has no valid UUIDs — adding to unmatched")
                    unmatched_contributors.append((role, contributor_name))
                    continue
                
                # Build contributor using helper function
                contributor = build_contributor(matched_person, role, pure_type_key)
                if contributor:
                    mapped_contributors.append(contributor)
                    print(f"        ✅ Added {role}: {matched_person.get('firstName')} {matched_person.get('lastName')}")
            else:
                print(f"        ⚠️ No matches found — adding to unmatched")
                unmatched_contributors.append((role, contributor_name))

    # Check if we have any contributors - if not, skip this record
    if not mapped_contributors:
        print(f"❌ No matched contributors found for record {dspace_row.get('dc.title', '')} - skipping")
        return None
    
    # Always ensure contributor info is present
    record["contributors"] = mapped_contributors

    print(f"✅ Added {len(mapped_contributors)} contributors")
    
    # Validate and fix organizations
    print("  🔍 Validating organization UUIDs...")
    mapped_contributors = validate_and_fix_organizations(mapped_contributors, API_KEY, BASE_URL)
    
    # Collect ALL validated organizations from ALL contributors
    all_internal_org_uuids, all_external_org_uuids = collect_validated_organizations(mapped_contributors)
    
    # Track first internal contributor's primary organization for managingOrganization
    first_internal_org_uuid = None
    for contributor in mapped_contributors:
        # Only check internal contributors
        if contributor.get("typeDiscriminator") == "InternalContributorAssociation":
            if "organizations" in contributor and contributor["organizations"]:
                first_internal_org_uuid = contributor["organizations"][0].get("uuid")
                if first_internal_org_uuid:
                    break
    
    # Set top-level organizations with unique validated internal orgs
    if all_internal_org_uuids:
        record["organizations"] = [
            {
                "systemName": "Organization",
                "uuid": org_uuid
            }
            for org_uuid in all_internal_org_uuids
        ]
    
    # Set top-level externalOrganizations with unique external orgs
    if all_external_org_uuids:
        record["externalOrganizations"] = [
            {
                "systemName": "ExternalOrganization",
                "uuid": org_uuid
            }
            for org_uuid in all_external_org_uuids
        ]
    
    # Set managingOrganization from first internal contributor's primary organization
    if first_internal_org_uuid:
        record["managingOrganization"] = {
            "uuid": first_internal_org_uuid,
            "systemName": "Organization"
        }
        print(f"✅ Set managingOrganization to: {first_internal_org_uuid}")
    else:
        # No internal authors - set to Library Repository
        record["managingOrganization"] = {
            "uuid": "a57f818f-e41c-443e-8bea-5183a9c54a6b",
            "systemName": "Organization"
        }
        print(f"✅ Set managingOrganization to Library Repository (no internal authors)")
    
    # Set DOIs and Handles - Repository DOI first, then Publisher DOI
    electronic_versions = []
    
    # First, add repository DOI if exists
    embargo_date_str = dspace_row.get("dc.date.embargo", "").strip()
    embargo_desc = dspace_row.get("dc.description.embargo", "").strip()
    
    # Parse embargo date using same function as publication date
    embargo_date = None
    if embargo_date_str:
        year, month, day = parse_date(embargo_date_str)
        embargo_date = f"{year:04d}-{month:02d}-{day:02d}"
    
    embargo_active = bool(embargo_date and embargo_date > TODAY) or bool(embargo_desc and embargo_desc > TODAY)

    uri_str = dspace_row.get("dc.identifier.uri", "").strip()
    if uri_str:
        dois = extract_dois_from_uri(uri_str)
        
        # Add repository DOI first
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

                if embargo_active and ev and embargo_date:
                    ev["embargoPeriod"] = {"endDate": embargo_date}
                
                if ev:
                    # Apply rights if specified
                    rights = dspace_row.get("dc.rights", "").strip()
                    if rights:
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
                    
                    electronic_versions.append(ev)
                    break  # only one repo DOI expected
    
    # Second, add publisher DOI
    publisher_doi = dspace_row.get("dc.identifier.doi", "").strip()
    if publisher_doi:
        publisher_doi = normalize_doi(publisher_doi)
        ev = build_electronic_version(
            publisher_doi, 
            "/dk/atira/pure/researchoutput/electronicversion/versiontype/publishersversion"
        )
        if ev:
            electronic_versions.append(ev)
    
    # Set electronic versions on record
    if electronic_versions:
        record["electronicVersions"] = electronic_versions

    # Add handles to links
    if uri_str:
        handles = extract_handles_from_uri(uri_str)
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
    import time
    start_time = time.time()
    
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

    print("Loading Organization Mapping...")
    with open(ORGANIZATION_MAPPING_JSON, 'r', encoding='utf-8') as f:
        organization_mapping = json.load(f)
    print(f"✅ Loaded {len(organization_mapping)} organization records from {ORGANIZATION_MAPPING_JSON}")

    # Build indices
    print("\n🔨 Building lookup indices...")
    person_index = build_person_name_index(person_mapping)
    print(f"✅ Built person name index with {len(person_index)} entries")
    
    org_index = build_organization_name_index(organization_mapping)
    print(f"✅ Built organization name index with {len(org_index)} entries")

    # Prepare logs
    log_entries = []
    error_log = []
    before_update_records = []
    no_author_records = [] 

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
        
        # Index by combined title (title + subtitle)
        title = item.get("title", {}).get("value", "").strip()
        subtitle = item.get("subTitle", {}).get("value", "").strip()
        if title:
            # Index by title alone
            pure_by_title[normalize(title)].append(item)
            # Also index by combined title if subtitle exists
            if subtitle:
                combined_title = f"{title} {subtitle}"
                pure_by_title[normalize(combined_title)].append(item)

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

        # Check if ALL contributor fields are empty
        has_any_contributors = any([
            row.get("dc.contributor.author", "").strip(),
            row.get("dc.contributor.editor", "").strip(),
            row.get("dc.contributor.translator", "").strip(),
            row.get("dc.contributor.illustrator", "").strip()
        ])

        if not has_any_contributors:
            log_entry["success"] = False
            log_entry["error"] = "No contributors found in any contributor field"
            log_entry["handle"] = extract_handles_from_uri(row.get("dc.identifier.uri", ""))[0] if extract_handles_from_uri(row.get("dc.identifier.uri", "")) else None
            log_entries.append(log_entry)
            print(f"⚠️ Skipping record - no contributors found: {row.get('dc.title', '')[:50]}...")
            continue

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
            dspace_title = row.get("dc.title", "").strip()
            dspace_subtitle = row.get("dc.title.subtitle", "").strip()
            
            # Combine DSpace title and subtitle
            combined_dspace_title = dspace_title
            if dspace_subtitle:
                combined_dspace_title = f"{dspace_title} {dspace_subtitle}"
            
            if combined_dspace_title:
                normalized_title = normalize(combined_dspace_title)
                # First try exact match
                if normalized_title in pure_by_title:
                    matched_records.extend(pure_by_title[normalized_title])
                    match_type = "Title (Exact)"
                else:
                    # Try similarity matching against all titles
                    best_match = None
                    best_similarity = 0
                    for pure_item in pure_items:
                        pure_title = pure_item.get("title", {}).get("value", "").strip()
                        if pure_title:
                            pure_subtitle = pure_item.get("subTitle", {}).get("value", "").strip()
                            # Combine Pure title and subtitle
                            combined_pure_title = pure_title
                            if pure_subtitle:
                                combined_pure_title = f"{pure_title} {pure_subtitle}"
                            
                            similarity, is_match = calculate_title_similarity(
                                combined_dspace_title, combined_pure_title, TITLE_SIMILARITY_THRESHOLD
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
                updated_record, success = update_record_from_dspace(record, row, person_index, org_index, log_entry, before_update_records, override_mode=OVERRIDE_MODE)
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
                new_record = create_new_record_from_dspace(row, person_index, org_index)
                
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

    if len(error_log) > 0:
        error_log_path = os.path.join(LOG_DIR, f"error_log_{TODAY}.log")
        with open(error_log_path, 'w', encoding='utf-8') as f:
            for err in error_log:
                f.write(err + "\n")

    if len(before_update_records) > 0:
        before_update_path = os.path.join(OUTPUT_DIR, f"matched_records_before_updates_{TODAY}.json")
        with open(before_update_path, "w", encoding="utf-8") as f:
            json.dump(before_update_records, f, indent=2, ensure_ascii=False)

    # Write no-author records CSV
    if no_author_records:
        print(f"\n📝 Writing {len(no_author_records)} records with no matched authors to CSV...")
        with open(NO_AUTHOR_CSV, 'w', newline='', encoding='utf-8') as f:
            if no_author_records:
                # Get fieldnames from first record
                fieldnames = no_author_records[0].keys()
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(no_author_records)
        print(f"✅ No-author records saved to: {NO_AUTHOR_CSV}")

    # Calculate elapsed time
    elapsed_time = time.time() - start_time
    hours = int(elapsed_time // 3600)
    minutes = int((elapsed_time % 3600) // 60)
    seconds = int(elapsed_time % 60)
    
    # Count results
    matched_count = sum(1 for e in log_entries if e['matched'])
    unmatched_count = sum(1 for e in log_entries if not e['matched'] and e.get('error') != "No contributors found in any contributor field")
    success_count = sum(1 for e in log_entries if e['success'])
    no_contributors_count = sum(1 for e in log_entries if e.get('error') == "No contributors found in any contributor field")
    error_count = len(error_log)
    
    print(f"\n✅ Done! {len(log_entries)} records processed.")
    print(f"   Matched: {matched_count}")
    print(f"   Unmatched: {unmatched_count}")
    print(f"   Success: {success_count}")
    print(f"   No contributors: {no_contributors_count}")
    print(f"   No matched authors: {len(no_author_records)}")
    print(f"   Errors: {error_count}")
    print(f"   Logs saved to: {LOG_DIR}")
    print(f"\n⏱️  Total time elapsed: {hours:02d}:{minutes:02d}:{seconds:02d}")
    
    logger.close()
    sys.stdout = logger.terminal


if __name__ == "__main__":
    main()