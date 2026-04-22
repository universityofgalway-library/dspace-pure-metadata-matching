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
from rapidfuzz import fuzz
from dotenv import load_dotenv
from dateutil import parser as dateutil_parser
from dateutil.parser import ParserError


# --- CONFIGURATION ---

# Load environment variables from .env file
load_dotenv()

TODAY = date.today().isoformat()

# If True, external organisations are collected from external authors and applied at
# the contributor and record level.  If False, no external organisation data is
# attached anywhere (external authors are still linked via their externalPerson UUID).
COLLECT_EXTERNAL_ORGS = False

# If True, PDFs downloaded from DSpace will be saved to disk locally as well as uploaded to Pure
SAVE_PDFS_LOCALLY = False
LOCAL_PDF_SAVE_DIR = "./downloaded_dspace_pdfs"  # Only used if SAVE_PDFS_LOCALLY is True

# If True, FileElectronicVersions will be created and attached to records
ADD_FILE_ELECTRONIC_VERSIONS = False

OVERRIDE_MODE = False  # Change to True to override existing Pure data

# DSPACE_CSV = "./dspace_data/test_samples/dspace_test_sample_2026-04-20.csv"
DSPACE_CSV = "./dspace_data/all_data_test/enriched_dspace_test_all_items_with_collection_uuids_pdfs_2026-04-20.csv"
PURE_JSON = "./pure_research_outputs/pure_test_research-outputs_2026-04-21.json"
PERSON_MAPPING_JSON = "./author_matching/2026-02-26/updated_merged_all_authors_2026-02-26.json"
ORGANIZATION_MAPPING_JSON = "./pure_entities/organizations_mapping_2026-03-02.json"
OUTPUT_DIR = f"./record_matching/test_output_{TODAY}"
MATCHED_DIR = os.path.join(OUTPUT_DIR, "matched")
UNMATCHED_DIR = os.path.join(OUTPUT_DIR, "unmatched")
LOG_DIR = os.path.join(OUTPUT_DIR, "logs")
NO_AUTHOR_CSV = os.path.join(OUTPUT_DIR, f"no_author_records_{TODAY}.csv")
FAULTY_PDF_CSV = os.path.join(OUTPUT_DIR, f"faulty_pdf_records_{TODAY}.csv")

USE_TEST_ENV = True  # Set to False to use production environment

API_KEY = os.getenv("PURE_ROOT_API_KEY_TEST", "") if USE_TEST_ENV else os.getenv("PURE_ROOT_API_KEY", "")
BASE_URL = (
    "https://galway-staging.elsevierpure.com/ws/api/"
    if USE_TEST_ENV else
    "https://research.universityofgalway.ie/ws/api/"
)

DSPACE_BITSTREAM_BASE = (
    "https://galway.dspace7-test.openrepository.com/bitstreams"
    if USE_TEST_ENV else
    "https://researchrepository.universityofgalway.ie/bitstreams"
)
PURE_FILE_UPLOAD_URL = f"{BASE_URL}research-outputs/file-uploads"

DOI_REGEX = re.compile(r'^(?:https?://)?(?:doi\.org/|doi:)?(10\.\S+)$', re.IGNORECASE)
HANDLE_REGEX = re.compile(r'^(?:https?://hdl\.handle\.net/)?(10379/\S+)$', re.IGNORECASE)

PUNC = set('''—!–¿()-[]{};:'"''""‐\,<>./?@#$%^&=+|£€*_~®™©0123456789''')


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
    "6d370e14-c9b6-4749-8680-6d513e02976b",    # "National University of Ireland Galway (NUI Galway)" 
    "3b995820-9623-4914-b158-2f2a217d20ec",    # "National University of Ireland"
    "6d415501-0899-44ae-aac3-258f31cd1b03",    # "National University of Ireland"
    "0aa5ccc1-a672-42ce-b262-fd01b3c54f5c"     # "National University of Ireland"
]

SYSTEM_FIELDS_TO_EXCLUDE = {
    "createdBy",
    "createdDate",
    "modifiedBy",
    "modifiedDate",
    "portalUrl",
    "prettyUrlIdentifiers",
    "version",
    "pureId"
}

SYSTEM_FIELDS = {
    "createdBy",
    "createdDate",
    "modifiedBy",
    "modifiedDate",
    "prettyUrlIdentifiers",
    "version",
    "pureId",
    "portalUrl",
	"systemName",
	"uuid", 
	"version", 
	"previousUuids"
}

LANG_MAP = {
        "eng": "en_IE",
        "fre": "fr_FR",
        "fra": "fr_FR",
        "ger": "de_DE",
        "spa": "es_ES",
        "gle": "ga"
        # Add more as needed
    }

LICENSE_MAP = {
    "CC BY-NC-ND":       "cc_by_nc_nd",
    "CC BY":             "cc_by",
    "CC BY-SA":          "cc_by_sa",
    "CC BY-NC":          "cc_by_nc",
    "CC BY-NC-SA":       "cc_by_nc_sa",
    "Public Domain":     "public_domain",
    "All rights reserved": "all_rights_reserved",
}

if not API_KEY:
    env_var = "PURE_ROOT_API_KEY_TEST" if USE_TEST_ENV else "PURE_ROOT_API_KEY"
    print(f"⚠️ WARNING: {env_var} not found in environment variables.")
    print("   External person duplicate resolution will be skipped.")

os.makedirs(MATCHED_DIR, exist_ok=True)
os.makedirs(UNMATCHED_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)

_person_metadata_cache = {}
_external_person_metadata_cache = {}
_org_validation_cache = {}

_unmatched_contributors = []
_unmatched_funders = []
_faulty_pdf_records = []

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

def parse_date(date_string, dayfirst=True):
    """
    Parse a date string into a (year, month, day) tuple.

    Supports ISO 8601, yyyy-mm-dd, dd-mm-yyyy, yyyy/mm/dd, dd/mm/yyyy,
    year-only, and most other common formats via dateutil.

    Args:
        date_string: Raw date string from DSpace metadata.
        dayfirst:    When True, ambiguous dates like "01/02/03" are interpreted
                     as dd/mm/yy. When False (default), mm/dd or yyyy-mm-dd order
                     is assumed. Set to True if your DSpace export uses European
                     date conventions.

    Returns:
        (year, month, day) tuple. Falls back to (1970, 1, 1) on failure.
    """
    if not date_string:
        return (1970, 1, 1)

    date_string = date_string.strip()

    # Year-only: "2008", "1995"
    if date_string.isdigit() and len(date_string) == 4:
        return (int(date_string), 1, 1)

    try:
        parsed = dateutil_parser.parse(date_string, dayfirst=dayfirst)
        return (parsed.year, parsed.month, parsed.day)
    except (ParserError, ValueError, OverflowError):
        pass

    # Last resort: extract the first 4-digit year found
    import re
    year_match = re.search(r'\b(1[89]\d{2}|20\d{2})\b', date_string)
    if year_match:
        return (int(year_match.group(1)), 1, 1)

    return (1970, 1, 1)


def strip_system_fields(record):
    """Return a shallow copy of record without system fields."""
    return {
        k: v
        for k, v in record.items()
        if k not in SYSTEM_FIELDS_TO_EXCLUDE
    }


def normalize(s):
    return s.strip().lower() if s else ""


def normalize_funder_name(s):
    """Normalize funder name: lowercase, replace punctuation with spaces, collapse whitespace."""
    if not s:
        return ""
    result = "".join(" " if char in PUNC else char for char in s.lower())
    return " ".join(result.split())  # collapse multiple spaces


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


def build_title_token_index(pure_items):
    """
    Build an inverted index mapping significant title tokens → Pure records.
    Common short words (stop words) are excluded to keep candidate sets small.
    """
    STOP_WORDS = {
        "a", "an", "the", "of", "in", "on", "at", "to", "for", "and",
        "or", "but", "with", "by", "from", "is", "are", "was", "were"
    }
    index = defaultdict(set)  # token → set of indices into pure_items

    for i, item in enumerate(pure_items):
        title = item.get("title", {}).get("value", "")
        subtitle = item.get("subTitle", {}).get("value", "")
        combined = f"{title} {subtitle}".strip()
        tokens = {
            w for w in normalize(combined).split()
            if len(w) > 3 and w not in STOP_WORDS
        }
        for token in tokens:
            index[token].add(i)

    return index


def find_fuzzy_title_candidates(dspace_title, dspace_subtitle, token_index, pure_items, max_candidates=200):
    """
    Use the token index to retrieve a small candidate set before fuzzy scoring.
    Returns the subset of pure_items worth scoring.
    """
    STOP_WORDS = {
        "a", "an", "the", "of", "in", "on", "at", "to", "for", "and",
        "or", "but", "with", "by", "from", "is", "are", "was", "were"
    }
    combined = f"{dspace_title} {dspace_subtitle}".strip()
    tokens = {
        w for w in normalize(combined).split()
        if len(w) > 3 and w not in STOP_WORDS
    }

    # Count how many query tokens each Pure record shares
    hit_counts = defaultdict(int)
    for token in tokens:
        for idx in token_index.get(token, set()):
            hit_counts[idx] += 1

    if not hit_counts:
        return []

    # Take the top candidates by shared token count
    top_indices = sorted(hit_counts, key=hit_counts.__getitem__, reverse=True)[:max_candidates]
    return [pure_items[i] for i in top_indices]


def strip_subtitle_from_title(title, subtitle):
    """
    If title ends with subtitle (case-insensitive, punctuation-ignored),
    strip it from the title, including any preceding colon (and optional space).
    Returns the cleaned title string (original register/punctuation preserved).
    """
    if not title or not subtitle:
        return title

    def strip_punc(s):
        return "".join(ch for ch in s.lower() if ch not in PUNC and not ch.isspace())

    title_clean = strip_punc(title)
    sub_clean = strip_punc(subtitle)

    if not sub_clean or not title_clean.endswith(sub_clean):
        return title

    # Find how many original chars of `title` correspond to the subtitle suffix.
    # Walk backwards through title matching against sub_clean in reverse.
    sub_rev = sub_clean[::-1]
    matched = 0
    i = len(title) - 1
    for target_ch in sub_rev:
        while i >= 0:
            ch = title[i]
            i -= 1
            if ch.lower() not in PUNC and not ch.isspace():
                if ch.lower() == target_ch:
                    matched += 1
                    break
                else:
                    return title  # mismatch — safety exit
    # i now points just before the subtitle portion
    cut = i + 1  # index in original title where subtitle begins (approx)

    # Walk back over any whitespace then an optional colon (and its preceding space)
    trimmed = title[:cut].rstrip()
    if trimmed.endswith(":"):
        trimmed = trimmed[:-1].rstrip()

    return trimmed if trimmed else title


def calculate_title_similarity(dspace_title, dspace_subtitle, pure_title, pure_subtitle, threshold=0.8):
    """
    Compare titles using three strategies and return the highest similarity.

    Strategies:
      a) dc.title  vs  Pure title
      b) dc.title + dc.title.subtitle  vs  Pure title
      c) dc.title  vs  Pure title + Pure subTitle

    Returns (best_similarity: float, is_match: bool)
    """
    if not dspace_title or not pure_title:
        return (0.0, False)

    def _sim(t1, t2):
        if not t1 or not t2:
            return 0.0
        t1n, t2n = normalize(t1), normalize(t2)
        if t1n == t2n:
            return 1.0
        max_len = max(len(t1n), len(t2n))
        if max_len > 0 and abs(len(t1n) - len(t2n)) / max_len > 0.5:
            return 0.0
        return fuzz.token_set_ratio(t1n, t2n) / 100.0

    combined_dspace = f"{dspace_title} {dspace_subtitle}".strip() if dspace_subtitle else dspace_title
    combined_pure = f"{pure_title} {pure_subtitle}".strip() if pure_subtitle else pure_title

    scores = [
        _sim(dspace_title, pure_title),         
        _sim(combined_dspace, pure_title),        
        _sim(dspace_title, combined_pure),
        _sim(combined_dspace, combined_pure)       
    ]
    best = max(scores)
    return (best, best >= threshold)


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


def build_person_name_index(person_mapping):
    """Build a comprehensive index of all person name variations for O(1) lookup"""
    person_index = {}
    
    for person in person_mapping:
        # --- NEW: Pre-index this person's known paper identifiers ---
        paper_dois = set()
        paper_handles = set()
        paper_titles = set()
        for paper in person.get("papers", []):
            if doi := paper.get("doi", ""):
                paper_dois.add(normalize_doi(doi.strip().lower()))
            if handle := paper.get("handle", ""):
                paper_handles.add(normalize_handle(handle.strip().lower()))
            if title := paper.get("title", ""):
                paper_titles.add(normalize(title))
        person["_paper_dois"] = paper_dois
        person["_paper_handles"] = paper_handles
        person["_paper_titles"] = paper_titles
        # --- END NEW ---

        p_first = person.get("firstName", "")
        p_last = person.get("lastName", "")
        alt_firsts = person.get("alternativeFirstName", []) or []
        alt_lasts = person.get("alternativeLastName", []) or []
        
        all_firsts = [p_first] if p_first else []
        all_firsts.extend(alt_firsts)
        all_lasts = [p_last] if p_last else []
        all_lasts.extend(alt_lasts)
        
        for af in all_firsts:
            for al in all_lasts:
                key1 = (normalize(af), normalize(al))
                if key1 not in person_index:
                    person_index[key1] = []
                person_index[key1].append(person)
                
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


def batch_fetch_person_metadata(person_uuids, api_key, base_url, is_external=False):
    """
    Fetch metadata for multiple persons in batch.
    Returns dict mapping UUID -> field_count
    """
    if not api_key:
        return {uuid: 0 for uuid in person_uuids}
    
    results = {}
    cache = _external_person_metadata_cache if is_external else _person_metadata_cache
    endpoint = "external-persons" if is_external else "persons"
    uncached_uuids = []
    
    # Check cache first
    for uuid in person_uuids:
        if uuid in cache:
            results[uuid] = cache[uuid]
        else:
            uncached_uuids.append(uuid)
    
    # Batch fetch uncached UUIDs
    if uncached_uuids:
        for uuid in uncached_uuids:
            try:
                response = requests.get(
                    f"{base_url}{endpoint}/{uuid}",
                    headers={
                        "accept": "application/json",
                        "api-key": api_key
                    },
                    timeout=10
                )
                if response.status_code == 200:
                    person_data = response.json()
                    field_count = sum(1 for k, v in person_data.items() 
                                    if k not in ["uuid", "createdBy", "modifiedBy", "version", 
                                               "portalUrl", "prettyUrlIdentifiers", "previousUuids"] and v)
                    results[uuid] = field_count
                    cache[uuid] = field_count
                else:
                    results[uuid] = 0
                    cache[uuid] = 0
            except Exception:
                results[uuid] = 0
                cache[uuid] = 0
    
    return results


def resolve_author_duplicate(matches, paper_dois=None, paper_handles=None, paper_title=None):
    """
    Prefer Person over External Person, then by visibility (internal),
    then by metadata richness.

    NEW: If a candidate's pre-indexed paper set contains any of the current
    record's DOIs, handles, or title, that candidate scores highest
    regardless of internal/external status — it is a confirmed match.

    Args:
        matches:        List of candidate person dicts from person_index.
        paper_dois:     Set of normalised DOIs for the current DSpace record.
        paper_handles:  Set of normalised handles for the current DSpace record.
        paper_title:    Normalised title string for the current DSpace record.
    """
    if not matches:
        return None

    paper_dois = paper_dois or set()
    paper_handles = paper_handles or set()

    # Batch-fetch metadata
    internal_uuids_to_fetch = []
    external_uuids_to_fetch = []
    
    for person in matches:
        if person.get("internal", False):
            for uuid_obj in person.get("internalUUIDs", []):
                uuid_value = extract_uuid(uuid_obj)
                if uuid_value not in _person_metadata_cache:
                    internal_uuids_to_fetch.append(uuid_value)
        elif person.get("external", False):
            for uuid_value in person.get("externalUUIDs", []):
                if uuid_value not in _external_person_metadata_cache:
                    external_uuids_to_fetch.append(uuid_value)
    
    if internal_uuids_to_fetch and API_KEY:
        batch_fetch_person_metadata(internal_uuids_to_fetch, API_KEY, BASE_URL, is_external=False)
    if external_uuids_to_fetch and API_KEY:
        batch_fetch_person_metadata(external_uuids_to_fetch, API_KEY, BASE_URL, is_external=True)

    def score(person):
        person_dois    = person.get("_paper_dois", set())
        person_handles = person.get("_paper_handles", set())
        person_titles  = person.get("_paper_titles", set())

        paper_score = 0
        if paper_dois & person_dois:
            paper_score = 2          # DOI match — strongest signal
        elif paper_handles & person_handles:
            paper_score = 2          # Handle match
        elif paper_title and paper_title in person_titles:
            paper_score = 1          # Title match — weakest but still evidence

        internal = person.get("internal", False)
        external = person.get("external", False)
        type_score = 2 if internal else (1 if external else 0)

        vis_score = 0
        if internal:
            internal_uuids = person.get("internalUUIDs", [])
            if internal_uuids and isinstance(internal_uuids[0], dict):
                vis = internal_uuids[0].get("visibility", "")
                if vis in ["FREE", "CAMPUS"]:
                    vis_score = 1

        metadata_score = 0
        if internal:
            for uuid_obj in person.get("internalUUIDs", []):
                uuid_value = extract_uuid(uuid_obj)
                if not API_KEY:
                    break
                field_count = _person_metadata_cache.get(uuid_value, 0)
                if field_count > metadata_score:
                    metadata_score = field_count
        elif external:
            for uuid_value in person.get("externalUUIDs", []):
                if not API_KEY:
                    break
                field_count = _external_person_metadata_cache.get(uuid_value, 0)
                if field_count > metadata_score:
                    metadata_score = field_count

        # paper_score is the leading sort key — a confirmed paper match
        # always wins over a non-confirmed one before any other signal is considered.
        return (paper_score, type_score, vis_score, metadata_score)

    sorted_matches = sorted(matches, key=score, reverse=True)
    return sorted_matches[0]


def parse_contributors_by_role(dspace_row):
    """Parse all contributor types from DSpace and return dict by role.
    
    Handles two special cases:
    1. Same name appears in both author and editor fields — keep only one role
       based on dc.type (editor preferred for book/interactive resource/conference
       proceedings, author preferred for everything else).
    2. Metadata correction: if dc.type is NOT a book-like type but the record has
       editors and no authors, treat those editors as authors instead.
    """
    # Types where editor role takes precedence over author role
    EDITOR_PREFERRED_TYPES = {"book", "interactive resource", "conference proceedings"}
    dspace_type = dspace_row.get("dc.type", "").strip().lower()
    prefer_editor = dspace_type in EDITOR_PREFERRED_TYPES

    contributors_by_role = {}

    # Parse all roles
    authors = parse_author_names(dspace_row.get("dc.contributor.author", ""))
    editors = parse_author_names(dspace_row.get("dc.contributor.editor", ""))
    translators = parse_author_names(dspace_row.get("dc.contributor.translator", ""))
    illustrators = parse_author_names(dspace_row.get("dc.contributor.illustrator", ""))

    # Resolve author/editor overlap for the same name
    author_set = {normalize(n) for n in authors}
    editor_set = {normalize(n) for n in editors}
    overlap = author_set & editor_set

    if overlap:
        if prefer_editor:
            # Remove overlapping names from authors, keep in editors
            authors = [n for n in authors if normalize(n) not in overlap]
            print(f"  ℹ️ Duplicate author/editor names — keeping as editor for type '{dspace_type}': "
                  f"{[n for n in editors if normalize(n) in overlap]}")
        else:
            # Remove overlapping names from editors, keep in authors
            editors = [n for n in editors if normalize(n) not in overlap]
            print(f"  ℹ️ Duplicate author/editor names — keeping as author for type '{dspace_type}': "
                  f"{[n for n in authors if normalize(n) in overlap]}")

    # Metadata correction: non-book type with editors but no authors
    # → those editors are almost certainly authors mislabelled in DSpace
    if not prefer_editor and editors and not authors:
        print(f"  ⚠️ Metadata correction: dc.type='{dspace_type}' has editors but no authors "
              f"— treating editors as authors: {editors}")
        authors = editors
        editors = []

    if authors:
        contributors_by_role["author"] = authors
    if editors:
        contributors_by_role["editor"] = editors
    if translators:
        contributors_by_role["translator"] = translators
    if illustrators:
        contributors_by_role["illustrator"] = illustrators

    return contributors_by_role


def build_contributor(matched_person, role, pure_type_key, collect_external_orgs=True):
    """Build a contributor object from matched person and role.

    Args:
        matched_person: Person dict from the person mapping.
        role: Contributor role string (e.g. 'author', 'editor').
        pure_type_key: Lower-cased Pure type key used to construct role URIs.
        collect_external_orgs: When False, external organisation data is omitted
            from the built contributor even if present in the person mapping.
    """
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
        # For internal authors, prefer primaryInternalOrganization,
        # fall back to any available internal organisation
        primary_org = matched_person.get("primaryInternalOrganization")
        if not primary_org:
            internal_orgs = matched_person.get("internalOrganizations", [])
            if internal_orgs:
                primary_org = internal_orgs[0] if isinstance(internal_orgs[0], str) else internal_orgs[0].get("uuid")
                print(f"        ℹ️ No primaryInternalOrganization for {first} {last} — using fallback org: {primary_org}")

        if primary_org:
            contributor["organizations"] = [
                {"systemName": "Organization", "uuid": primary_org}
            ]
        else:
            print(f"        ⚠️ Internal person {first} {last} has no primaryInternalOrganization and no internalOrganizations in mapping")

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
        # Only attach external organisations when the feature is enabled
        if collect_external_orgs and "externalOrganizations" in matched_person and matched_person["externalOrganizations"]:
            external_orgs = matched_person["externalOrganizations"]
            
            # Filter out ignored organizations — always, even if it's the only one
            filtered_external_orgs = [
                org_uuid for org_uuid in external_orgs
                if org_uuid not in EXTERNAL_ORGS_TO_IGNORE
            ]
            
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


def process_contributors(
    contributors_by_role,
    person_index,
    dspace_row,
    pure_type_key,
    existing_contributors=None,
    pure_uuid=None,
):
    """
    Resolve DSpace contributor names to Pure person entities.

    Args:
        contributors_by_role: Dict of {role: [name, ...]} from parse_contributors_by_role.
        person_index:         Pre-built person name index.
        dspace_row:           The current DSpace CSV row.
        pure_type_key:        Lower-cased Pure type key for role URI construction.
        existing_contributors: List of existing Pure contributor dicts. When provided,
                               contributors already present (by UUID or name) are reused
                               rather than rebuilt. Pass None or [] to skip this check
                               (i.e. for new records or override mode).
        pure_uuid:            UUID of the matched Pure record, used in unmatched contributor
                              log entries. Pass None for new records.

    Returns:
        (final_contributors, unmatched_contributors)
        final_contributors:    List of resolved contributor dicts ready for Pure.
        unmatched_contributors: List of dicts describing contributors that could not be resolved.
    """
    existing_contributors = existing_contributors or []

    # Build fast lookup structures from existing contributors
    existing_by_uuid = {}
    existing_by_name = {}
    for contrib in existing_contributors:
        if not contrib:
            continue
        name = contrib.get("name", {}) or {}
        first = name.get("firstName", "") or ""
        last = name.get("lastName", "") or ""
        all_first_names = [first] if first else []
        all_last_names = [last] if last else []
        for name_entry in contrib.get("names", []):
            name_obj = name_entry.get("name", {})
            if f := name_obj.get("firstName", ""):
                all_first_names.append(f)
            if l := name_obj.get("lastName", ""):
                all_last_names.append(l)
        for pair in list(product(all_first_names, all_last_names)) + list(product(all_last_names, all_first_names)):
            key = (normalize(pair[0].strip()), normalize(pair[1].strip()))
            existing_by_name[key] = contrib
        for ref_key in ("person", "externalPerson"):
            ref = contrib.get(ref_key)
            if ref:
                uuid = ref.get("uuid")
                if uuid:
                    existing_by_uuid[uuid] = contrib

    # Pre-compute record-level identifiers for paper-evidence scoring
    record_paper_dois = set()
    record_paper_handles = set()
    record_paper_title = normalize(dspace_row.get("dc.title", "").strip())

    publisher_doi_raw = dspace_row.get("dc.identifier.doi", "").strip()
    if publisher_doi_raw:
        record_paper_dois.add(normalize_doi(publisher_doi_raw))
    for doi in extract_dois_from_uri(dspace_row.get("dc.identifier.uri", "")):
        record_paper_dois.add(normalize_doi(doi))
    for handle in extract_handles_from_uri(dspace_row.get("dc.identifier.uri", "")):
        record_paper_handles.add(normalize_handle(handle))

    final_contributors = []
    unmatched_contributors = []

    # Helper to build an unmatched entry
    handles = extract_handles_from_uri(dspace_row.get("dc.identifier.uri", ""))
    handle_for_log = handles[0] if handles else None

    for role, contributor_names in contributors_by_role.items():
        print(f"  ➤ Processing {len(contributor_names)} {role}(s)")

        for contributor_name in contributor_names:
            print(f"    ➤ Checking match for {role}: '{contributor_name}'")
            matches = find_person_match(contributor_name, person_index)

            if not matches:
                print(f"        ⚠️ No matches found — adding to unmatched")
                unmatched_contributors.append({
                    "name": contributor_name,
                    "role": role,
                    "handle": handle_for_log,
                    "title": dspace_row.get("dc.title", ""),
                    "pure_uuid": pure_uuid,
                })
                continue

            print(f"      ✅ Found {len(matches)} matches")
            matched_person = resolve_author_duplicate(
                matches,
                paper_dois=record_paper_dois,
                paper_handles=record_paper_handles,
                paper_title=record_paper_title,
            )

            if not matched_person:
                print(f"        ❌ ERROR: resolve_author_duplicate returned None for {len(matches)} matches!")
                unmatched_contributors.append({
                    "name": contributor_name,
                    "role": role,
                    "handle": handle_for_log,
                    "title": dspace_row.get("dc.title", ""),
                    "pure_uuid": pure_uuid,
                })
                continue

            has_valid_internal = matched_person.get("internal", False) and matched_person.get("internalUUIDs")
            has_valid_external = matched_person.get("external", False) and matched_person.get("externalUUIDs")

            if not has_valid_internal and not has_valid_external:
                print(f"        ⚠️ Matched person has no valid UUIDs — adding to unmatched")
                unmatched_contributors.append({
                    "name": contributor_name,
                    "role": role,
                    "handle": handle_for_log,
                    "title": dspace_row.get("dc.title", ""),
                    "pure_uuid": pure_uuid,
                })
                continue

            uuid_value = None
            if has_valid_internal:
                uuid_value = extract_uuid(matched_person.get("internalUUIDs")[0])
            elif has_valid_external:
                uuid_value = extract_uuid(matched_person.get("externalUUIDs")[0])

            first = matched_person.get("firstName", "")
            last = matched_person.get("lastName", "")
            name_key = (normalize(first), normalize(last))

            # Reuse existing contributor if present (skip when existing_contributors is empty,
            # i.e. for new records or override mode)
            if existing_contributors:
                if uuid_value in existing_by_uuid:
                    print(f"        ℹ️ Contributor already exists (by UUID), using existing: {first} {last}")
                    final_contributors.append(existing_by_uuid[uuid_value])
                    continue
                if name_key in existing_by_name:
                    print(f"        ℹ️ Contributor already exists (by name), using existing: {first} {last}")
                    final_contributors.append(existing_by_name[name_key])
                    continue

            contributor = build_contributor(
                matched_person, role, pure_type_key,
                collect_external_orgs=COLLECT_EXTERNAL_ORGS,
            )
            if contributor:
                final_contributors.append(contributor)
                print(f"        ✅ Added {role}: {first} {last}")

    return final_contributors, unmatched_contributors


def resolve_record_duplicate(records):
    """Choose record with most metadata or updated by real user"""
    if not records:
        return None

    def score(record):
        # 1. Prefer visibility FREE or CAMPUS
        vis = record.get("visibility", {}).get("key", "")
        vis_score = 1 if vis in ["FREE", "CAMPUS"] else 0

        # 2. Count filled fields at 0.5 each (excluding system ones)
        field_count = sum(0.5 for k, v in record.items() if k not in SYSTEM_FIELDS and v)

        # 3. Prefer real users — 2 if real user, 0 otherwise
        modifier = record.get("modifiedBy", "")
        real_user_score = 2 if modifier not in ["root", "atira", "sync_user", "admin", "system", ""] else 0

        # 4. Count internal contributors — 1 point each
        internal_contributor_score = sum(
            1 for c in record.get("contributors", [])
            if c and c.get("typeDiscriminator") == "InternalContributorAssociation"
        )

        return (vis_score, internal_contributor_score, field_count, real_user_score)

    sorted_records = sorted(records, key=score, reverse=True)
    return sorted_records[0]


def build_electronic_version(doi, version_type_uri, access_type="UNKNOWN",
                             license_type=None, embargo_end_date=None):
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
        "versionType": {
            "uri": version_type_uri
        }
    }

    if license_type:
        ev["licenseType"] = {
            "uri": f"/dk/atira/pure/core/document/licenses/{license_type.lower()}"
        }

    if embargo_end_date:
        ev["embargoPeriod"] = {
            "endDate": embargo_end_date
        }

    return ev


def resolve_license_uri(rights_str):
    """
    Map a dc.rights string to a Pure license URI.
    Falls back to CC BY-NC-ND if the value is absent or unrecognised.
    """
    key = LICENSE_MAP.get(rights_str.strip(), "cc_by_nc_nd") if rights_str else "cc_by_nc_nd"
    return f"/dk/atira/pure/core/document/licenses/{key}"


def resolve_embargo_and_access(dspace_row):
    """
    Derive embargo period and access type from DSpace embargo fields.

    Returns:
        embargo_date_iso (str | None): ISO date string if a future embargo exists,
                                       else None.
        embargo_active   (bool):       True if embargo_date_iso is set.
        access_uri       (str):        Pure access type URI.
        embargo_period   (dict | None): Ready-made embargoPeriod dict for Pure,
                                        or None if no active embargo.
    """
    embargo_date_str = dspace_row.get("dc.date.embargo", "").strip()
    embargo_desc     = dspace_row.get("dc.description.embargo", "").strip()

    embargo_date_iso = None
    embargo_active   = False

    if embargo_date_str:
        year, month, day = parse_date(embargo_date_str)
        candidate = f"{year:04d}-{month:02d}-{day:02d}"
        if candidate > TODAY:
            embargo_date_iso = candidate
            embargo_active   = True

    if not embargo_active and embargo_desc and embargo_desc > TODAY:
        embargo_date_iso = embargo_desc
        embargo_active   = True

    access_uri = (
        "/dk/atira/pure/core/openaccesspermission/embargoed"
        if embargo_active else
        "/dk/atira/pure/core/openaccesspermission/open"
    )
    embargo_period = {"endDate": embargo_date_iso} if embargo_active else None

    return embargo_date_iso, embargo_active, access_uri, embargo_period


def upload_pdf_electronic_version(dspace_row):
    # Respect global config — skip entirely if file EVs are disabled
    if not ADD_FILE_ELECTRONIC_VERSIONS:
        return None

    pdf_path = dspace_row.get("pdf_handle_paths", "").strip()
    if not pdf_path:
        return None

    dspace_uuid  = dspace_row.get("uuid", "").strip()
    title        = dspace_row.get("dc.title", "").strip()
    handle_str   = dspace_row.get("handle", "").strip()
    full_pdf_url = f"{DSPACE_BITSTREAM_BASE}{pdf_path}"
    file_name    = pdf_path.rstrip("/").split("/")[-1]

    print(f"  📎 Uploading PDF: {full_pdf_url}")

    try:
        src_response = requests.get(full_pdf_url, stream=True, timeout=60)
        if src_response.status_code != 200:
            msg = (f"  ❌ PDF download failed (HTTP {src_response.status_code}): "
                   f"{full_pdf_url}")
            print(msg)
            _faulty_pdf_records.append({
                "uuid":          dspace_uuid,
                "title":         title,
                "handle":        handle_str,
                "full_pdf_path": full_pdf_url,
            })
            return None

        # Optionally save PDF to disk before streaming to Pure
        if SAVE_PDFS_LOCALLY:
            os.makedirs(LOCAL_PDF_SAVE_DIR, exist_ok=True)
            local_path = os.path.join(LOCAL_PDF_SAVE_DIR, file_name)
            with open(local_path, "wb") as pdf_file:
                for chunk in src_response.iter_content(chunk_size=8192):
                    pdf_file.write(chunk)
            print(f"  💾 PDF saved locally to: {local_path}")
            # Re-open for streaming to Pure
            upload_content = open(local_path, "rb")
        else:
            upload_content = src_response.iter_content(chunk_size=8192)

        upload_response = requests.put(
            PURE_FILE_UPLOAD_URL,
            data=upload_content,
            headers={
                "accept":       "application/json",
                "api-key":      API_KEY,
                "content-type": "*/*",
            },
            timeout=120,
        )

        if SAVE_PDFS_LOCALLY and hasattr(upload_content, "close"):
            upload_content.close()

    except requests.RequestException as exc:
        print(f"  ❌ PDF upload request error: {exc}")
        _faulty_pdf_records.append({
            "uuid":          dspace_uuid,
            "title":         title,
            "handle":        handle_str,
            "full_pdf_path": full_pdf_url,
        })
        return None

    if upload_response.status_code not in (200, 201):
        print(f"  ❌ Pure file-upload failed (HTTP {upload_response.status_code}): "
              f"{upload_response.text[:200]}")
        _faulty_pdf_records.append({
            "uuid":          dspace_uuid,
            "title":         title,
            "handle":        handle_str,
            "full_pdf_path": full_pdf_url,
        })
        return None

    try:
        upload_data = upload_response.json()
    except ValueError:
        print(f"  ❌ Could not parse Pure file-upload response as JSON")
        _faulty_pdf_records.append({
            "uuid":          dspace_uuid,
            "title":         title,
            "handle":        handle_str,
            "full_pdf_path": full_pdf_url,
        })
        return None

    print(f"  ✅ PDF uploaded successfully. Key: {upload_data.get('key')}")

    license_uri                                    = resolve_license_uri(dspace_row.get("dc.rights", ""))
    _, _, access_uri, embargo_period               = resolve_embargo_and_access(dspace_row)

    file_ev = {
        "typeDiscriminator": "FileElectronicVersion",
        "accessType":  {"uri": access_uri},
        "licenseType": {"uri": license_uri},
        "file": {
            "fileName": file_name,
            "mimeType": upload_data.get("mimeType", "*/*"),
            "size":     upload_data.get("size", 0),
            "uploadedFile": {
                "digest":     upload_data.get("digest"),
                "digestType": upload_data.get("digestType"),
                "mimeType":   upload_data.get("mimeType", "*/*"),
                "size":       upload_data.get("size", 0),
                "key":        upload_data.get("key"),
            },
        },
    }

    if embargo_period:
        file_ev["embargoPeriod"] = embargo_period

    return file_ev


def build_link(url, alias="", description=""):
    return {
        "url": url,
        "alias": alias,
        "description": {"en_IE": description}
        }


def build_dspace_identifier(dspace_uuid):
    """Build a DSpace PrimaryId identifier object."""
    if not dspace_uuid:
        return None
    return {
        "typeDiscriminator": "PrimaryId",
        "idSource": "DSpace",
        "value": dspace_uuid.strip()
    }


def merge_identifiers(existing_identifiers, dspace_uuid):
    """
    Merge DSpace UUID into identifiers array as PrimaryId.
    Demotes any existing PrimaryId entries to Id.
    Returns the updated identifiers list.
    """
    if not dspace_uuid:
        return existing_identifiers

    # Demote any existing PrimaryId to Id
    updated = []
    for ident in existing_identifiers:
        if ident.get("typeDiscriminator") == "PrimaryId":
            updated.append({**ident, "typeDiscriminator": "Id"})
        else:
            updated.append(ident)

    # Add the DSpace PrimaryId (avoid duplicate if already present)
    already_present = any(
        i.get("idSource") == "DSpace" and i.get("value", "").strip() == dspace_uuid.strip()
        for i in updated
    )
    if not already_present:
        updated.insert(0, build_dspace_identifier(dspace_uuid))

    return updated


def batch_validate_organizations(org_uuids, api_key, base_url):
    """
    Validate multiple organization UUIDs in batch.
    Returns dict mapping UUID -> is_valid (bool)
    """
    if not api_key:
        return {uuid: False for uuid in org_uuids}
    
    results = {}
    uncached_uuids = []
    
    # Check cache first
    for uuid in org_uuids:
        if uuid in _org_validation_cache:
            results[uuid] = _org_validation_cache[uuid]
        else:
            uncached_uuids.append(uuid)
    
    # Batch validate uncached UUIDs
    if uncached_uuids:
        print(f"  🔍 Batch validating {len(uncached_uuids)} organizations...")
        for uuid in uncached_uuids:
            try:
                response = requests.get(
                    f"{base_url}organizations/{uuid}",
                    headers={
                        "accept": "application/json",
                        "api-key": api_key
                    },
                    timeout=10
                )
                is_valid = response.status_code == 200
                results[uuid] = is_valid
                _org_validation_cache[uuid] = is_valid
            except Exception:
                results[uuid] = False
                _org_validation_cache[uuid] = False
    
    return results


def validate_organization_as_external(org_uuid, api_key, base_url):
    """
    Check whether an org UUID exists in the Pure external-organizations endpoint.
    Uses a separate cache key prefix to avoid collision with internal org cache.
    Returns True if found, False otherwise.
    """
    cache_key = f"external::{org_uuid}"
    if cache_key in _org_validation_cache:
        return _org_validation_cache[cache_key]

    try:
        response = requests.get(
            f"{base_url}external-organizations/{org_uuid}",
            headers={
                "accept": "application/json",
                "api-key": api_key
            },
            timeout=10
        )
        result = response.status_code == 200
        _org_validation_cache[cache_key] = result
        return result
    except Exception:
        _org_validation_cache[cache_key] = False
        return False


def validate_and_fix_organizations(contributors, api_key, base_url, collect_external_orgs=False):
    """
    Validate all internal organization UUIDs for contributors against the Pure API.

    For each invalid internal org UUID:
    - If collect_external_orgs is False: omit the UUID entirely and log a warning.
    - If collect_external_orgs is True: check whether the UUID exists as an external
      organisation. If found, attach it as an externalOrganization and log the change.
      If not found, omit it entirely and log a warning.

    Returns updated contributors list.
    """
    if not api_key:
        print("  ⚠️ No API key - skipping organization validation")
        return contributors

    # Collect all unique internal org UUIDs first
    all_org_uuids = set()
    for contributor in contributors:
        if not contributor:
            continue
        for org in contributor.get("organizations", []):
            org_uuid = org.get("uuid")
            if org_uuid:
                all_org_uuids.add(org_uuid)

    # Batch validate all UUIDs against the internal organizations endpoint
    validation_results = batch_validate_organizations(list(all_org_uuids), api_key, base_url)

    updated_contributors = []

    for contributor in contributors:
        if not contributor:
            continue

        internal_orgs = contributor.get("organizations", [])
        if internal_orgs:
            valid_internal_orgs = []

            for org in internal_orgs:
                org_uuid = org.get("uuid")
                if not org_uuid:
                    continue

                if validation_results.get(org_uuid, False):
                    valid_internal_orgs.append(org)
                else:
                    if not collect_external_orgs:
                        # Omit entirely, log warning
                        print(f"    ⚠️ Invalid internal org UUID {org_uuid} not found in Pure "
                              f"— omitting (COLLECT_EXTERNAL_ORGS is False)")
                    else:
                        # Check if it exists as an external organisation
                        is_external = validate_organization_as_external(org_uuid, api_key, base_url)
                        if is_external:
                            print(f"    ℹ️ Invalid internal org UUID {org_uuid} found as external "
                                  f"organisation — adding to externalOrganizations")
                            existing_external = contributor.get("externalOrganizations", [])
                            existing_external_uuids = {o.get("uuid") for o in existing_external}
                            if org_uuid not in existing_external_uuids:
                                existing_external.append({
                                    "systemName": "ExternalOrganization",
                                    "uuid": org_uuid
                                })
                            contributor["externalOrganizations"] = existing_external
                        else:
                            print(f"    ⚠️ Invalid internal org UUID {org_uuid} not found in Pure "
                                  f"as internal or external organisation — omitting")

            if valid_internal_orgs:
                contributor["organizations"] = valid_internal_orgs
            else:
                contributor.pop("organizations", None)

        updated_contributors.append(contributor)

    return updated_contributors


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
            vis_score = 2
        elif vis == "CAMPUS":
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
            normalized = normalize_funder_name(org_name)
            if normalized not in org_index:
                org_index[normalized] = []
            org_index[normalized].append(org)
    
    return org_index


def find_funder_match(funder_name, org_index):
    """Find matching organization using pre-built index"""
    normalized_name = normalize_funder_name(funder_name)
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


def append_record_to_file(filepath, new_record):
    """
    Append new_record to a JSON array file, deduplicating by uuid.
    If the file does not exist it is created. Re-running is idempotent:
    a record with the same uuid as an existing entry replaces it.
    """
    existing = []
    if os.path.exists(filepath):
        with open(filepath, "r", encoding="utf-8") as f:
            try:
                existing = json.load(f)
            except json.JSONDecodeError:
                print(f"  ⚠️ Could not parse existing file {filepath} — starting fresh.")
                existing = []

    # Build a dict keyed by uuid for O(1) dedup; preserve insertion order
    records_by_uuid = {r.get("uuid"): r for r in existing}
    record_uuid = new_record.get("uuid")

    if record_uuid and record_uuid in records_by_uuid:
        print(f"  ℹ️ uuid {record_uuid} already in {os.path.basename(filepath)} — replacing.")

    records_by_uuid[record_uuid] = new_record

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(list(records_by_uuid.values()), f, indent=2, ensure_ascii=False)


# --- UPDATING RECORDS ---

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

    # Get existing contributors from Pure record (if any).
    # In override mode, pass an empty list so process_contributors skips deduplication entirely.
    existing_contributors = [] if override_mode else [c for c in pure_record.get("contributors", []) if c is not None]

    final_contributors, record_unmatched_contributors = process_contributors(
        contributors_by_role=contributors_by_role,
        person_index=person_index,
        dspace_row=dspace_row,
        pure_type_key=pure_type.lower(),
        existing_contributors=existing_contributors,
        pure_uuid=pure_record.get("uuid"),
    )

    if record_unmatched_contributors:
        log_entry["unmatchedContributors"] = record_unmatched_contributors
        _unmatched_contributors.extend(record_unmatched_contributors)

    # Only update contributors if we have any
    if final_contributors:
        print("  🔍 Validating organization UUIDs...")
        final_contributors = validate_and_fix_organizations(final_contributors, API_KEY, BASE_URL, collect_external_orgs=COLLECT_EXTERNAL_ORGS)
        updated_record["contributors"] = final_contributors
    
    # --- 1a. Collect ALL validated organizations from ALL contributors ---
    # Internal contributors -> "organizations" (primaryInternalOrganization)
    # External contributors -> "externalOrganizations" (only when COLLECT_EXTERNAL_ORGS is True)
    all_internal_org_uuids = []
    all_external_org_uuids = []
    seen_internal = set()
    seen_external = set()

    for contributor in (final_contributors if final_contributors else []):
        if contributor.get("typeDiscriminator") == "InternalContributorAssociation":
            for org in contributor.get("organizations", []):
                uuid = org.get("uuid")
                if uuid and uuid not in seen_internal:
                    all_internal_org_uuids.append(uuid)
                    seen_internal.add(uuid)
        elif contributor.get("typeDiscriminator") == "ExternalContributorAssociation" and COLLECT_EXTERNAL_ORGS:
            for org in contributor.get("externalOrganizations", []):
                uuid = org.get("uuid")
                if uuid and uuid not in seen_external:
                    all_external_org_uuids.append(uuid)
                    seen_external.add(uuid)

    if override_mode:
        if all_internal_org_uuids:
            updated_record["organizations"] = [
                {"systemName": "Organization", "uuid": uuid}
                for uuid in all_internal_org_uuids
            ]
        if COLLECT_EXTERNAL_ORGS:
            # Set record-level externalOrganizations, excluding ignored orgs
            record_level_external = [u for u in all_external_org_uuids if u not in EXTERNAL_ORGS_TO_IGNORE]
            updated_record["externalOrganizations"] = [
                {"systemName": "ExternalOrganization", "uuid": uuid}
                for uuid in record_level_external
            ]
    else:
        if all_internal_org_uuids:
            existing_internal = pure_record.get("organizations", [])
            existing_internal_uuids = {o.get("uuid") for o in existing_internal}
            merged_internal = list(existing_internal)
            for uuid in all_internal_org_uuids:
                if uuid not in existing_internal_uuids:
                    merged_internal.append({"systemName": "Organization", "uuid": uuid})
                    existing_internal_uuids.add(uuid)
            updated_record["organizations"] = merged_internal

        if COLLECT_EXTERNAL_ORGS:
            # Only merge non-ignored external orgs into the record level
            record_level_external = [u for u in all_external_org_uuids if u not in EXTERNAL_ORGS_TO_IGNORE]
            if record_level_external:
                existing_external = pure_record.get("externalOrganizations", [])
                existing_external_uuids = {o.get("uuid") for o in existing_external}
                merged_external = list(existing_external)
                for uuid in record_level_external:
                    if uuid not in existing_external_uuids:
                        merged_external.append({"systemName": "ExternalOrganization", "uuid": uuid})
                        existing_external_uuids.add(uuid)
                updated_record["externalOrganizations"] = merged_external

    # --- 1b. Managing Organization - Update based on override mode ---
    first_internal_org_uuid = None
    for contributor in (final_contributors if final_contributors else []):
        if contributor.get("typeDiscriminator") == "InternalContributorAssociation":
            orgs = contributor.get("organizations", [])
            if orgs:
                first_internal_org_uuid = orgs[0].get("uuid")
                if first_internal_org_uuid:
                    break

    if override_mode:
        if first_internal_org_uuid:
            updated_record["managingOrganization"] = {
                "uuid": first_internal_org_uuid,
                "systemName": "Organization"
            }
            print(f"  ✅ Override: Set managingOrganization to: {first_internal_org_uuid}")
        else:
            updated_record["managingOrganization"] = {
                "uuid": "a57f818f-e41c-443e-8bea-5183a9c54a6b",
                "systemName": "Organization"
            }
            print(f"  ✅ Override: Set managingOrganization to Library Repository (no internal authors)")
    elif not pure_record.get("managingOrganization", {}).get("uuid"):
        if first_internal_org_uuid:
            updated_record["managingOrganization"] = {
                "uuid": first_internal_org_uuid,
                "systemName": "Organization"
            }
            print(f"  ✅ Precedence: Set managingOrganization to: {first_internal_org_uuid}")
        else:
            updated_record["managingOrganization"] = {
                    "uuid": "cb47638d-8856-42a9-a3ae-2f8e8f90c7ad", # PROD
#                   "uuid": "a57f818f-e41c-443e-8bea-5183a9c54a6b", # UAT
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

    # Track unmatched funders for this record
    record_unmatched_funders = []
    
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
                    record_unmatched_funders.append({
                        "name": funder_name,
                        "handle": extract_handles_from_uri(dspace_row.get("dc.identifier.uri", ""))[0] if extract_handles_from_uri(dspace_row.get("dc.identifier.uri", "")) else None,
                        "title": dspace_row.get("dc.title", ""),
                        "pure_uuid": pure_record.get("uuid")
                    })
            else:
                print(f"      ⚠️ No match found for funder: {funder_name}")
                record_unmatched_funders.append({
                    "name": funder_name,
                    "handle": extract_handles_from_uri(dspace_row.get("dc.identifier.uri", ""))[0] if extract_handles_from_uri(dspace_row.get("dc.identifier.uri", "")) else None,
                    "title": dspace_row.get("dc.title", ""),
                    "pure_uuid": pure_record.get("uuid")
                })
        
        # Add unmatched funders to log entry and global tracker
        if record_unmatched_funders:
            log_entry["unmatchedFunders"] = record_unmatched_funders
            _unmatched_funders.extend(record_unmatched_funders)

        # If sponsorship is empty and there are unmatched funders, add them to fundingText
        if not sponsorship and record_unmatched_funders:
            unmatched_names = "; ".join(f["name"] for f in record_unmatched_funders)
            if not has_text_in_any_language(pure_record, "fundingText") or override_mode:
                updated_record["fundingText"] = {"en_IE": escape_special_chars(unmatched_names)}
                print(f"    ℹ️ No sponsorship text — added {len(record_unmatched_funders)} unmatched funder(s) to fundingText")
        
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
    repo_ev = existing_repo_evs[0] if existing_repo_evs else None

    # --- 5a. Embargo (dc.date.embargo / dc.description.embargo) > overwrite for repo version ---
    embargo_date, embargo_active, _, embargo_period = resolve_embargo_and_access(dspace_row)

    # --- 5b. Publisher DOI (dc.identifier.doi) > add if blank ---
  
    publisher_doi = dspace_row.get("dc.identifier.doi", "").strip()
    new_publisher_ev = None
    if publisher_doi:
        publisher_doi = normalize_doi(publisher_doi)
        # Check if it's actually a repository DOI
        if "10.13025" in publisher_doi:
            if publisher_doi not in existing_repo_dois:
                ev = build_electronic_version(
                    doi=publisher_doi,
                    version_type_uri="/dk/atira/pure/researchoutput/electronicversion/versiontype/authorsversion",
                    access_type="EMBARGOED" if embargo_active else "OPEN",
                    license_type="CC_BY_NC"
                )
                if embargo_period and ev:
                    ev["embargoPeriod"] = embargo_period
                if ev:
                    repo_ev = ev
        elif publisher_doi not in existing_publisher_dois:
            ev = build_electronic_version(
                doi=publisher_doi,
                version_type_uri="/dk/atira/pure/researchoutput/electronicversion/versiontype/publishersversion",
            )
            if ev:
                new_publisher_ev = ev

    if repo_ev:
        # Update existing repo version
        if embargo_period:
            repo_ev["embargoPeriod"] = embargo_period
            repo_ev["accessType"] = {"uri": "/dk/atira/pure/core/openaccesspermission/embargoed"}
        elif embargo_date:
            # Embargo date exists but is in the past — clear any stale embargo
            repo_ev.pop("embargoPeriod", None)
            repo_ev["accessType"] = {"uri": "/dk/atira/pure/core/openaccesspermission/open"}


        # Always enforce version type + license
        repo_ev["versionType"] = {
            "uri": "/dk/atira/pure/researchoutput/electronicversion/versiontype/authorsversion"
        }
        repo_ev["licenseType"] = {
            "uri": "/dk/atira/pure/core/document/licenses/cc_by_nc"
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
                        license_type="CC_BY_NC"
                    )

                    if embargo_period and ev:
                        ev["embargoPeriod"] = embargo_period

                    if ev:
                        repo_ev = ev
                        break  # only one repo DOI expected


    # --- 5d. Rights (dc.rights.uri) > overwrite for repo version ---
    rights = dspace_row.get("dc.rights", "").strip()
    if repo_ev:
        repo_ev["licenseType"] = {"uri": resolve_license_uri(rights)}

    # --- 5e. Build final electronic versions list: repository DOI first, then publisher DOIs, then others ---
    final_evs = []

    # Add repository DOI first (if exists)
    if repo_ev:
        if "doi" in repo_ev and isinstance(repo_ev["doi"], str):
            repo_ev["doi"] = normalize_doi(repo_ev["doi"])
        final_evs.append(repo_ev)

    # Add publisher DOIs second
    for ev in existing_publisher_evs:
        if "doi" in ev and isinstance(ev["doi"], str):
            ev["doi"] = normalize_doi(ev["doi"])
        final_evs.append(ev)

    if new_publisher_ev:
        if "doi" in new_publisher_ev and isinstance(new_publisher_ev["doi"], str):
            new_publisher_ev["doi"] = normalize_doi(new_publisher_ev["doi"])
        final_evs.append(new_publisher_ev)

    # Add other electronic versions last
    for ev in existing_other_evs:
        if "doi" in ev and isinstance(ev["doi"], str):
            ev["doi"] = normalize_doi(ev["doi"])
        final_evs.append(ev)

    # --- 5f. Upload PDF as FileElectronicVersion (append last) ---
    file_ev = upload_pdf_electronic_version(dspace_row)
    if file_ev:
        final_evs.append(file_ev)
        print(f"  ✅ FileElectronicVersion added: {file_ev['file']['fileName']}")

    # Only update if changed
    if final_evs != existing_evs:
        updated_record["electronicVersions"] = final_evs

    # --- 5g. Add Handles as links and remove DOI links ---
    uri_str = dspace_row.get("dc.identifier.uri", "").strip()
    existing_links = pure_record.get("links", [])

    # Separate existing links into handles and everything else (excluding DOIs)
    existing_handle_links = []
    non_handle_non_doi_links = []
    for link in existing_links:
        url = link.get("url", "")
        if "doi.org" in url:
            pass  # drop DOI links entirely
        elif "hdl.handle.net" in url:
            existing_handle_links.append(link)
        else:
            non_handle_non_doi_links.append(link)

    existing_handle_urls = [normalize_handle(l.get("url", "")) for l in existing_handle_links]
    dspace_handles = extract_handles_from_uri(uri_str) if uri_str else []
    dspace_handle_urls = [normalize_handle(h) for h in dspace_handles]

    final_handle_links = []

    if dspace_handles:
        # Find which DSpace handles match any existing Pure handle
        matching_dspace = [h for h in dspace_handles if normalize_handle(h) in existing_handle_urls]

        if len(matching_dspace) == 1:
            # Exactly one DSpace handle matches Pure — use it
            canonical_handle = matching_dspace[0]
            print(f"  ℹ️ Handle matched between DSpace and Pure: {canonical_handle}")
        elif len(matching_dspace) > 1:
            # Multiple DSpace handles match Pure — take first, warn
            canonical_handle = matching_dspace[0]
            print(f"  ⚠️ Multiple DSpace handles match Pure handles — using first: {canonical_handle}")
        else:
            # No DSpace handle matches Pure — take first DSpace handle
            canonical_handle = dspace_handles[0]
            if existing_handle_urls:
                print(f"  ℹ️ No DSpace handle matches existing Pure handles — using first DSpace handle: {canonical_handle}")

        # Check Pure side: how many existing Pure handles match any DSpace handle
        matching_pure = [l for l in existing_handle_links if normalize_handle(l.get("url", "")) in dspace_handle_urls]

        if len(matching_pure) > 1:
            # Multiple Pure handles match DSpace — keep all, flag for review
            print(f"  ⚠️ MANUAL REVIEW REQUIRED: multiple Pure handles match DSpace handles "
                  f"for record {pure_record.get('uuid')} — keeping all matching Pure handles")
            final_handle_links = matching_pure
        else:
            final_handle_links = [build_link(canonical_handle, alias="Handle", description="Repository Handle")]

    else:
        # No DSpace handles — preserve all existing Pure handles and flag for review
        if existing_handle_links:
            print(f"  ⚠️ MANUAL REVIEW REQUIRED: no handle found in DSpace URI for record "
                  f"{pure_record.get('uuid')} — preserving {len(existing_handle_links)} existing "
                  f"Pure handle(s): {[l.get('url') for l in existing_handle_links]}")
            final_handle_links = existing_handle_links
        else:
            print(f"  ℹ️ No handles found in DSpace or Pure for record {pure_record.get('uuid')}")

    # Build the final links list: handles first, then other non-DOI links
    updated_links = final_handle_links + non_handle_non_doi_links

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

    # --- 8. Title (dc.title) > fill if blank, prefer Pure data ---
    dspace_title    = dspace_row.get("dc.title", "").strip()
    dspace_subtitle = dspace_row.get("dc.title.subtitle", "").strip()
    if not dspace_subtitle:
        dspace_subtitle = dspace_row.get("dc.title.alternative", "").strip()

    pure_title    = pure_record.get("title", {}).get("value", "").strip()
    pure_subtitle = pure_record.get("subTitle", {}).get("value", "").strip()

    # Resolve the effective subtitle: prefer explicit dc.title.subtitle,
    # but fall back to Pure's existing subtitle so we can strip it from
    # the DSpace title if it is embedded there (e.g. "Title: Subtitle").
    effective_subtitle = dspace_subtitle or pure_subtitle

    if override_mode:
        if dspace_title:
            # Strip any embedded subtitle from the title string before writing,
            # using whichever subtitle we know about.
            clean_title = strip_subtitle_from_title(dspace_title, effective_subtitle)
            updated_record["title"] = {"value": escape_special_chars(clean_title)}
            if dspace_subtitle:
                updated_record["subTitle"] = {"value": escape_special_chars(dspace_subtitle)}
            else:
                updated_record["subTitle"] = {"value": ""}
    else:
        # Precedence: fill only if Pure field is blank.
        if dspace_title and not pure_title:
            # Strip any embedded subtitle (from DSpace or already in Pure)
            # before writing the title.
            clean_title = strip_subtitle_from_title(dspace_title, effective_subtitle)
            updated_record["title"] = {"value": escape_special_chars(clean_title)}

        if dspace_subtitle and not pure_subtitle:
            updated_record["subTitle"] = {"value": escape_special_chars(dspace_subtitle)}

    # --- 9. Journal Association (for ContributionToJournal/ContributionToPeriodical) ---
    type_disc = pure_record.get("typeDiscriminator", "")
    
    if type_disc in ["ContributionToJournal", "ContributionToPeriodical"]:
        journal_uuid = dspace_row.get("journal_uuid", "").strip()
        existing_journal = pure_record.get("journalAssociation", {}).get("journal", {}).get("uuid")
        
        # Add journal if we have a UUID and (no existing journal)
        if journal_uuid and (not existing_journal):
            updated_record["journalAssociation"] = {
                "journal": {
                    "systemName": "Journal",
                    "uuid": journal_uuid
                }
            }
            action = "Added"
            print(f"  ✅ {action}: Set journal association to: {journal_uuid}")
        elif not journal_uuid and not existing_journal:
            # No journal UUID in DSpace and no existing journal - this shouldn't be a journal contribution
            print(f"    ⚠️ No journal UUID found for {type_disc} - record may need type change")

    
    # --- 10. Identifiers — set DSpace UUID as PrimaryId ---
    dspace_uuid = dspace_row.get("uuid", "").strip()
    if dspace_uuid:
        existing_identifiers = pure_record.get("identifiers", [])
        updated_record["identifiers"] = merge_identifiers(existing_identifiers, dspace_uuid)
    else:
        print(f"  ⚠️ No DSpace UUID found for record: {dspace_row.get('dc.title', '')[:80]}")
    # --- 11. Set workflow step ---
    updated_record["workflow"] = {
        "step": "validated"
    }

    # Write log entry
    log_entry["success"] = success and not errors
    if errors:
        log_entry["error"] = "; ".join(errors)

    before_update_records.append(strip_system_fields(pure_record))

    return updated_record, success


# --- CREATING RECORDS ---

def create_new_record_from_dspace(dspace_row, person_index, org_index):
    """Create new Pure record from DSpace row"""
    
    record = {
        "title": {"value": ""},
        "type": {
            "uri": ""
        },
        "category": {
            "uri": "/dk/atira/pure/researchoutput/category/research"
        },
        "language": {
            "uri": "/dk/atira/pure/core/languages/en_IE"
        },
        "managingOrganization": {
            "uuid":  "cb47638d-8856-42a9-a3ae-2f8e8f90c7ad", # PROD
#            "uuid": "a57f818f-e41c-443e-8bea-5183a9c54a6b", # UAT
            "systemName": "Organization"
            }, 
        "visibility": {
            "key": "FREE"
        },
        "workflow": {
            "step": "validated"
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

    # Re-derive pure_type_key AFTER add_type_specific_fields, in case type was downgraded
    pure_type_key = get_pure_type_key(record["type"]["uri"])

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
    dspace_title    = dspace_row.get("dc.title", "").strip()
    dspace_subtitle = dspace_row.get("dc.title.subtitle", "").strip()
    if not dspace_subtitle:
        dspace_subtitle = dspace_row.get("dc.title.alternative", "").strip()

    if dspace_title:
        clean_title = strip_subtitle_from_title(dspace_title, dspace_subtitle)
        record["title"] = {"value": escape_special_chars(clean_title)}

    if dspace_subtitle:
        record["subTitle"] = {"value": escape_special_chars(dspace_subtitle)}

    # Set sponsorship
    sponsorship = dspace_row.get("dc.description.sponsorship", "").strip()
    if sponsorship:
        record["fundingText"] = {"en_IE": escape_special_chars(sponsorship)}
    
    # Set funders
    dspace_funders = parse_funders(dspace_row.get("dc.contributor.funder", ""))
    
    # Track unmatched funders
    record_unmatched_funders = []
    
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
                    record_unmatched_funders.append({
                        "name": funder_name,
                        "handle": extract_handles_from_uri(dspace_row.get("dc.identifier.uri", ""))[0] if extract_handles_from_uri(dspace_row.get("dc.identifier.uri", "")) else None,
                        "title": dspace_row.get("dc.title", ""),
                        "pure_uuid": None
                    })
            else:
                print(f"      ⚠️ No match found for funder: {funder_name}")
                record_unmatched_funders.append({
                    "name": funder_name,
                    "handle": extract_handles_from_uri(dspace_row.get("dc.identifier.uri", ""))[0] if extract_handles_from_uri(dspace_row.get("dc.identifier.uri", "")) else None,
                    "title": dspace_row.get("dc.title", ""),
                    "pure_uuid": None
                })
        
        # Add to global tracker
        if record_unmatched_funders:
            _unmatched_funders.extend(record_unmatched_funders)

        # If sponsorship is empty and there are unmatched funders, add them to fundingText
        if not sponsorship and record_unmatched_funders:
            unmatched_names = "; ".join(f["name"] for f in record_unmatched_funders)
            record["fundingText"] = {"en_IE": escape_special_chars(unmatched_names)}
            print(f"    ℹ️ No sponsorship text — added {len(record_unmatched_funders)} unmatched funder(s) to fundingText")
        
        # Add funders to record
        if funder_uuids_with_type:
            funding_details = build_funding_organizations(funder_uuids_with_type)
            record["fundingDetails"] = funding_details
            print(f"    ✅ Added {len(funder_uuids_with_type)} funders to fundingDetails")

    # Set contributors (all roles)
    contributors_by_role = parse_contributors_by_role(dspace_row)
    print(f"✅ Processing contributors: {sum(len(names) for names in contributors_by_role.values())} total")

    # Process each role and its contributors
    final_contributors, record_unmatched_contributors = process_contributors(
        contributors_by_role=contributors_by_role,
        person_index=person_index,
        dspace_row=dspace_row,
        pure_type_key=pure_type_key,
        existing_contributors=[],   # always empty for new records
        pure_uuid=None,
    )

    if record_unmatched_contributors:
        _unmatched_contributors.extend(record_unmatched_contributors)

    # Check if we have any contributors - if not, skip this record
    if not final_contributors:
        print(f"❌ No matched contributors found for record {dspace_row.get('dc.title', '')} - skipping")
        return None

    # Validate and fix organizations BEFORE assigning to record
    print("  🔍 Validating organization UUIDs...")
    final_contributors = validate_and_fix_organizations(final_contributors, API_KEY, BASE_URL, collect_external_orgs=COLLECT_EXTERNAL_ORGS)

    # Always ensure contributor info is present (post-validation list)
    record["contributors"] = final_contributors
    print(f"✅ Added {len(final_contributors)} contributors")

    # Collect ALL validated organizations from ALL contributors
    # Internal contributors -> "organizations" (primaryInternalOrganization)
    # External contributors -> "externalOrganizations" (only when COLLECT_EXTERNAL_ORGS is True)
    all_internal_org_uuids = []
    all_external_org_uuids = []
    seen_internal = set()
    seen_external = set()

    for contributor in final_contributors:
        if contributor.get("typeDiscriminator") == "InternalContributorAssociation":
            for org in contributor.get("organizations", []):
                uuid = org.get("uuid")
                if uuid and uuid not in seen_internal:
                    all_internal_org_uuids.append(uuid)
                    seen_internal.add(uuid)
        elif contributor.get("typeDiscriminator") == "ExternalContributorAssociation" and COLLECT_EXTERNAL_ORGS:
            for org in contributor.get("externalOrganizations", []):
                uuid = org.get("uuid")
                if uuid and uuid not in seen_external:
                    all_external_org_uuids.append(uuid)
                    seen_external.add(uuid)

    # Set record-level organizations from internal contributors
    if all_internal_org_uuids:
        record["organizations"] = [
            {"systemName": "Organization", "uuid": uuid}
            for uuid in all_internal_org_uuids
        ]

    # Set record-level externalOrganizations only when enabled, excluding ignored orgs
    if COLLECT_EXTERNAL_ORGS:
        record_level_external = [u for u in all_external_org_uuids if u not in EXTERNAL_ORGS_TO_IGNORE]
        if record_level_external:
            record["externalOrganizations"] = [
                {"systemName": "ExternalOrganization", "uuid": uuid}
                for uuid in record_level_external
            ]

    # Set managingOrganization from first internal contributor's primary organization
    first_internal_org_uuid = None
    for contributor in final_contributors:
        if contributor.get("typeDiscriminator") == "InternalContributorAssociation":
            orgs = contributor.get("organizations", [])
            if orgs:
                first_internal_org_uuid = orgs[0].get("uuid")
                if first_internal_org_uuid:
                    break

    if first_internal_org_uuid:
        record["managingOrganization"] = {
            "uuid": first_internal_org_uuid,
            "systemName": "Organization"
        }
        print(f"✅ Set managingOrganization to: {first_internal_org_uuid}")
    else:
        record["managingOrganization"] = {
            "uuid": "cb47638d-8856-42a9-a3ae-2f8e8f90c7ad", # PROD
#            "uuid": "a57f818f-e41c-443e-8bea-5183a9c54a6b", # UAT
            "systemName": "Organization"
        }
        print(f"✅ Set managingOrganization to Library Repository (no internal authors)")
    
    # Set DOIs and Handles - Repository DOI first, then Publisher DOI
    electronic_versions = []
    
    embargo_date, embargo_active, _, embargo_period = resolve_embargo_and_access(dspace_row)

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
                    license_type="CC_BY_NC"
                )

                if embargo_period and ev:
                    ev["embargoPeriod"] = embargo_period
                
                if ev:
                    # Apply rights if specified
                    ev["licenseType"] = {"uri": resolve_license_uri(dspace_row.get("dc.rights", ""))}
                    electronic_versions.append(ev)
                    break  # only one repo DOI expected
    
    # Second, add publisher DOI
    publisher_doi = dspace_row.get("dc.identifier.doi", "").strip()
    if publisher_doi:
        publisher_doi = normalize_doi(publisher_doi)
        if "10.13025" in publisher_doi:
            # Treat as repo DOI if not already added
            already_added = any("10.13025" in ev.get("doi", "") for ev in electronic_versions)
            if not already_added:
                access_type = "EMBARGOED" if embargo_active else "OPEN"
                ev = build_electronic_version(
                    publisher_doi,
                    "/dk/atira/pure/researchoutput/electronicversion/versiontype/authorsversion",
                    access_type=access_type,
                    license_type="CC_BY_NC"
                )
                if embargo_period and ev:
                    ev["embargoPeriod"] = embargo_period
                if ev:
                    ev["licenseType"] = {"uri": resolve_license_uri(dspace_row.get("dc.rights", ""))}
                    electronic_versions.insert(0, ev)
        else:
            ev = build_electronic_version(
                publisher_doi,
                "/dk/atira/pure/researchoutput/electronicversion/versiontype/publishersversion"
            )
            if ev:
                electronic_versions.append(ev)
    
    # Set electronic versions on record (DOIs)
    if electronic_versions:
        record["electronicVersions"] = electronic_versions

    file_ev = upload_pdf_electronic_version(dspace_row)
    if file_ev:
        record.setdefault("electronicVersions", []).append(file_ev)
        print(f"✅ FileElectronicVersion added: {file_ev['file']['fileName']}")

    # Add only the first Handle from DSpace to links to avoid duplication
    if uri_str:
        handles = extract_handles_from_uri(uri_str)
        if handles:
            record["links"] = [build_link(handles[0], alias="Handle", description="Repository Handle")]

    # Set DSpace UUID as PrimaryId identifier
    dspace_uuid = dspace_row.get("uuid", "").strip()
    if dspace_uuid:
        record["identifiers"] = [build_dspace_identifier(dspace_uuid)]
    else:
        print(f"  ⚠️ No DSpace UUID found for record: {dspace_row.get('dc.title', '')[:80]}")

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

    print("Building title token index...")
    title_token_index = build_title_token_index(pure_items)
    print(f"✅ Built title token index with {len(title_token_index)} tokens")

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
            if not url:
                continue
            if "hdl.handle.net" in url:
                pure_by_handle[normalize_handle(url)].append(item)
            elif "10.13025" in url:
                pure_by_repo_doi[normalize_doi(url)].append(item)
            elif url.startswith("https://doi.org/") or url.startswith("http://doi.org/"):
                pure_by_doi[normalize_doi(url)].append(item)
        
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

        # Filter: only process records that belong to a Publications collection
        collection_names = row.get("collection_names", "").strip()
        if not collection_names or collection_names.lower() != "publications":
            log_entry["success"] = False
            log_entry["error"] = "Skipped: not in a Publications collection"
            log_entry["handle"] = extract_handles_from_uri(row.get("dc.identifier.uri", ""))[0] if extract_handles_from_uri(row.get("dc.identifier.uri", "")) else None
            log_entries.append(log_entry)
            print(f"⚠️ Skipping record - not in Publications collection: {row.get('dc.title', '')[:50]}...")
            continue

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
            if not dspace_subtitle:
                dspace_subtitle = row.get("dc.title.alternative", "").strip()

            if dspace_title:
                combined_dspace_title = f"{dspace_title} {dspace_subtitle}".strip() if dspace_subtitle else dspace_title

                # Strategy 4a. Exact match: try all three key variants against the index
                exact_candidates = [dspace_title, combined_dspace_title]
                for candidate in dict.fromkeys(exact_candidates):  # deduplicate, preserve order
                    key = normalize(candidate)
                    if key in pure_by_title:
                        matched_records.extend(pure_by_title[key])
                        match_type = "Title (Exact)"
                        break

                # Strategy 4b. Fuzzy title match: candidates only, not all Pure records
                if not matched_records:
                    candidates = find_fuzzy_title_candidates(
                        dspace_title, dspace_subtitle, title_token_index, pure_items
                    )
                    best_match = None
                    best_similarity = 0
                    for pure_item in candidates:
                        pure_title_val = pure_item.get("title", {}).get("value", "").strip()
                        if pure_title_val:
                            pure_subtitle_val = pure_item.get("subTitle", {}).get("value", "").strip()
                            similarity, is_match = calculate_title_similarity(
                                dspace_title,
                                dspace_subtitle,
                                pure_title_val,
                                pure_subtitle_val,
                                TITLE_SIMILARITY_THRESHOLD,
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
                    type_key = get_pure_type_key(log_entry["pureType"])
                    filename = f"{type_key}_{TODAY}.json"
                    filepath = os.path.join(MATCHED_DIR, filename)
                    append_record_to_file(filepath, updated_record)
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

                    type_key = get_pure_type_key(log_entry["pureType"])
                    filename = f"{type_key}_{TODAY}.json"
                    filepath = os.path.join(UNMATCHED_DIR, filename)
                    append_record_to_file(filepath, new_record)
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

    # Write unmatched contributors CSV
    if _unmatched_contributors:
        unmatched_contributors_csv = os.path.join(OUTPUT_DIR, f"unmatched_contributors_{TODAY}.csv")
        print(f"\n📝 Writing {len(_unmatched_contributors)} unmatched contributors to CSV...")
        with open(unmatched_contributors_csv, 'w', newline='', encoding='utf-8') as f:
            fieldnames = ['name', 'role', 'handle', 'title', 'pure_uuid']
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(_unmatched_contributors)
        print(f"✅ Unmatched contributors saved to: {unmatched_contributors_csv}")

    #  unmatched funders CSV
    if _unmatched_funders:
        unmatched_funders_csv = os.path.join(OUTPUT_DIR, f"unmatched_funders_{TODAY}.csv")
        print(f"\n📝 Writing {len(_unmatched_funders)} unmatched funders to CSV...")
        with open(unmatched_funders_csv, 'w', newline='', encoding='utf-8') as f:
            fieldnames = ['name', 'handle', 'title', 'pure_uuid']
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(_unmatched_funders)
        print(f"✅ Unmatched funders saved to: {unmatched_funders_csv}")


    # Write faulty PDF records CSV
    if _faulty_pdf_records:
        print(f"\n📝 Writing {len(_faulty_pdf_records)} faulty PDF records to CSV...")
        with open(FAULTY_PDF_CSV, 'w', newline='', encoding='utf-8') as f:
            fieldnames = ['uuid', 'title', 'handle', 'full_pdf_path']
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(_faulty_pdf_records)
        print(f"✅ Faulty PDF records saved to: {FAULTY_PDF_CSV}")


    # Calculate elapsed time
    elapsed_time = time.time() - start_time
    hours = int(elapsed_time // 3600)
    minutes = int((elapsed_time % 3600) // 60)
    seconds = int(elapsed_time % 60)
    
    # Count results
    matched_count = sum(1 for e in log_entries if e['matched'])
    not_publications_count = sum(1 for e in log_entries if e.get('error') == "Skipped: not in a Publications collection")
    no_contributors_count = sum(1 for e in log_entries if e.get('error') == "No contributors found in any contributor field")
    no_matched_authors_count = sum(1 for e in log_entries if e.get('error') == "No matched contributors")
    unmatched_count = sum(1 for e in log_entries if not e['matched'] and e.get('error') not in ("No contributors found in any contributor field", "No matched contributors"))
    success_count = sum(1 for e in log_entries if e['success'])
    failed_count = sum(1 for e in log_entries if not e['success'])
    error_count = len(error_log)

    print(f"\n✅ Done! {len(log_entries)} records processed.")
    print(f"   Matched to existing Pure record: {matched_count}")
    print(f"   Unmatched (new records created): {unmatched_count}")
    print(f"   Successfully processed: {success_count}")
    print(f"   Failed (total): {failed_count}")
    print(f"     ↳ No contributors in any field: {no_contributors_count}")
    print(f"     ↳ No contributors matched to Pure persons: {no_matched_authors_count}")
    print(f"     ↳ Other errors: {error_count}")
    print(f"     ↳ Not in Publications collection: {not_publications_count}")
    print(f"   Unmatched contributors: {len(_unmatched_contributors)}")
    print(f"   Unmatched funders: {len(_unmatched_funders)}")
    print(f"   Faulty PDF records: {len(_faulty_pdf_records)}")
    print(f"   Logs saved to: {LOG_DIR}")
    print(f"\n⏱️  Total time elapsed: {hours:02d}:{minutes:02d}:{seconds:02d}")
    
    logger.close()
    sys.stdout = logger.terminal


if __name__ == "__main__":
    main()