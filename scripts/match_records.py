import os
import re
import sys
import csv
import json
import unicodedata
import html
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

OVERRIDE_MODE = False  # Change to True to override existing Pure data

# A Pure record carries exactly ONE DSpace identity: one DSpace UUID (external
# ID), one repository Handle and one repository DOI, all of the same DSpace
# item. When the Pure record being updated is already linked to a DIFFERENT
# DSpace item than the CSV row:
#   "dspace" -> the CSV row's DSpace UUID is written, with that item's Handle
#               and repository DOI; the other DSpace identity is removed
#   "pure"   -> Pure's existing DSpace UUID is kept, with that item's Handle
#               and repository DOI (taken from the CSV when the item is in it)

DSPACE_UUID_PREFERENCE = "pure"  # "dspace" or "pure"

# DSpace item types that are never uploaded to Pure (dc.type, lower case).
EXCLUDED_DSPACE_TYPES = {"dataset", "doctoral thesis", "master thesis"}

# DSPACE_CSV = "./dspace_data/prod_samples/enriched_dspace_prod_items_2026-10-05_subset.csv"
DSPACE_CSV = "./dspace_data/all_data_prod/enriched_dspace_prod_items_2026-10-05.csv"
PURE_JSON = "./pure_research_outputs/pure_prod_research-outputs_2026-10-06.json"
PERSON_MAPPING_JSON = "./author_matching/prod_2026-10-05/updated_merged_prod_all_authors_enriched_2026-10-05.json"
ORGANIZATION_MAPPING_JSON = "./pure_entities/prod_organizations_mapping_2026-10-06.json"
PUBLISHER_MAPPING_JSON = "./pure_entities/pure_prod_publishers_2026-10-06.json"
JOURNAL_MAPPING_JSON = "./pure_entities/pure_prod_journals_2026-10-06.json"
OUTPUT_DIR = f"./record_matching/prod_output_{TODAY}"
MATCHED_DIR = os.path.join(OUTPUT_DIR, "matched")
UNMATCHED_DIR = os.path.join(OUTPUT_DIR, "unmatched")
LOG_DIR = os.path.join(OUTPUT_DIR, "logs")
NO_AUTHOR_CSV = os.path.join(OUTPUT_DIR, f"no_author_records_{TODAY}.csv")

USE_TEST_ENV = False  # Set to True to use UAT/staging environment
USE_TEMP_ENV = False  # Set to True to use TEMP environment (uses same API key as Production)

ORG_CONFIG_PATH = (
    "./scripts/test_orgs_config.json"
    if USE_TEST_ENV else
    "./scripts/prod_orgs_config.json"
)

try:
    with open(ORG_CONFIG_PATH, 'r', encoding='utf-8-sig') as f:
        _org_config = json.load(f)
except FileNotFoundError:
    print(f"❌ ERROR: Organisation config file not found: {ORG_CONFIG_PATH}")
    print(f"   Please create this file before running the script.")
    print(f"   Expected keys: LIBRARY_REPOSITORY, CENTRAL_UNIVERSITY, EXTERNAL_ORGS_TO_IGNORE")
    sys.exit(1)
except json.JSONDecodeError as e:
    print(f"❌ ERROR: Organisation config file is not valid JSON: {ORG_CONFIG_PATH}")
    print(f"   JSON error: {e}")
    sys.exit(1)

EXTERNAL_ORGS_TO_IGNORE = set(_org_config["EXTERNAL_ORGS_TO_IGNORE"])
CENTRAL_UNIVERSITY_ORGS = set(_org_config["CENTRAL_UNIVERSITY"])
LIBRARY_REPOSITORY_UUID = _org_config["LIBRARY_REPOSITORY"]

API_KEY = os.getenv("PURE_ROOT_API_KEY_TEST", "") if USE_TEST_ENV else os.getenv("PURE_ROOT_API_KEY", "")
BASE_URL = (
    "https://galway-staging.elsevierpure.com/ws/api/"
    if USE_TEST_ENV else
    "https://research.universityofgalway.ie/ws/api/"
)

# Accepts: bare "10.xxx/..", doi.org or dx.doi.org URLs (http/https, and the
# "https:/" single-slash typo), and "doi:", "DOI:", "DOI " or ":" prefixes.
DOI_REGEX = re.compile(r'^(?:https?:/{1,2})?(?:(?:dx\.)?doi\.org/|doi\s*:?\s*|:)?(10\.\S+)$', re.IGNORECASE)
# Accepts hdl.handle.net, handle.net and www.handle.net hosts, with or without
# an http(s):// scheme, in any case, and ignores trailing slashes.
HANDLE_REGEX = re.compile(r'^(?:(?:https?://)?(?:www\.|hdl\.)?handle\.net/)?(10379/\S+?)/*$', re.IGNORECASE)

PUNC = set('''—!–¿()-[]{};:'"''""‐\,<>./?@#$%^&=+|£€*_~®™©0123456789''')


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
    "deu": "de_DE",
    "spa": "es_ES",
    # Celtic
    "gle": "ga_IE",  # Irish
    "wel": "cy_GB",  # Welsh
    "cym": "cy_GB",  # Welsh (alternative code)
    "bre": "br_FR",  # Breton
    "cor": "kw_GB",  # Cornish
    "gla": "gd_GB",  # Scottish Gaelic
    "sga": "ga",     # Old Irish
    "mga": "ga",     # Middle Irish
    # Germanic
    "dut": "nl_NL",
    "nld": "nl_NL",
    "por": "pt_PT",
    "ita": "it_IT",
    "swe": "sv_SE",
    "nor": "nb_NO",
    "nob": "nb_NO",
    "nno": "nn_NO",
    "dan": "da_DK",
    "fin": "fi_FI",
    "isl": "is_IS",
    # Slavic
    "rus": "ru_RU",
    "pol": "pl_PL",
    "ces": "cs_CZ",
    "cze": "cs_CZ",
    "slk": "sk_SK",
    "slo": "sk_SK",
    "hrv": "hr_HR",
    "srp": "sr_RS",
    "bul": "bg_BG",
    "ukr": "uk_UA",
    "bel": "be_BY",
    "slv": "sl_SI",
    "mkd": "mk_MK",
    # Asian
    "zho": "zh_CN",
    "chi": "zh_CN",
    "jpn": "ja_JP",
    "kor": "ko_KR",
    "hin": "hi_IN",
    "ara": "ar_SA",
    "tur": "tr_TR",
    "vie": "vi_VN",
    "tha": "th_TH",
    "ind": "id_ID",
    "msa": "ms_MY",
    "may": "ms_MY",
    "fas": "fa_IR",
    "per": "fa_IR",
    "heb": "he_IL",
    "urd": "ur_PK",
    "ben": "bn_BD",
    # Other European
    "cat": "ca_ES",
    "eus": "eu_ES",
    "glg": "gl_ES",
    "ron": "ro_RO",
    "rum": "ro_RO",
    "hun": "hu_HU",
    "ell": "el_GR",
    "gre": "el_GR",
    "lad": "lad",
    "lat": "la",
    "lav": "lv_LV",
    "lit": "lt_LT",
    "est": "et_EE",
    "afr": "af_ZA",
    "sqi": "sq_AL",
    "alb": "sq_AL",
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
_unmatched_publishers = []
_unmatched_journals = []
_dspace_uuid_mismatches = []
_publisher_doi_conflicts = []

# Lookups over the whole DSpace CSV (filled in main): DSpace items by UUID,
# and which DSpace item each repository Handle / repository DOI belongs to.
_DSPACE_ITEMS = {"by_uuid": {}, "uuid_by_handle": {}, "uuid_by_repo_doi": {}}

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


def strip_nested_pure_ids(obj):
    """Recursively remove every "pureId" key, at any nesting level."""
    if isinstance(obj, dict):
        return {k: strip_nested_pure_ids(v) for k, v in obj.items() if k != "pureId"}
    if isinstance(obj, list):
        return [strip_nested_pure_ids(item) for item in obj]
    return obj


def strip_system_fields(record):
    """Return a shallow copy of record without system fields."""
    return {
        k: v
        for k, v in record.items()
        if k not in SYSTEM_FIELDS_TO_EXCLUDE
    }


def fix_apostrophe(s):
    """Replace curly/curved apostrophe with a straight one."""
    return s.replace("\u2019", "'") if s else s


# Characters used as an apostrophe in person names across the three name
# sources (DSpace, Pure, and the deduplicated authors JSON). All of them are
# treated as equivalent to the straight apostrophe (U+0027) when matching
# contributor names -- e.g. O'Malley / O’Malley / OʼMalley are the same name.
APOSTROPHE_VARIANTS = (
    "\u2019",  # ’ right single quotation mark
    "\u2018",  # ‘ left single quotation mark
    "\u02bc",  # ʼ modifier letter apostrophe
    "\u2032",  # ′ prime
    "\u00b4",  # ´ acute accent
)
_APOSTROPHE_TRANSLATION = str.maketrans({c: "'" for c in APOSTROPHE_VARIANTS})


def normalize_person_name(s):
    """
    Comparison key for a person-name part (first or last name): every
    apostrophe variant in APOSTROPHE_VARIANTS becomes a straight apostrophe,
    then the usual normalize() (strip + lowercase) is applied.

    Used ONLY to build/look up matching keys. Names written to Pure are never
    changed by this function.
    """
    if not s:
        return ""
    return normalize(s.translate(_APOSTROPHE_TRANSLATION))


def clean_dspace_filename(filename: str) -> str:
    """
    Some DSpace-exported filenames in the CSV have been HTML-entity-encoded
    (e.g. an accented/ligature character rendered as "&#769;" or "&#64258;")
    and then percent-encoded on top of that, leaving literal text like
    "&#769;" embedded in the filename instead of the intended character
    (e.g. "Me&#769;liacin.pdf" instead of "Méliacin.pdf").

    Decodes any HTML character references, then normalizes with NFKC so a
    decoded combining mark merges into the preceding base letter and
    compatibility characters like the "fl" ligature fold back to plain
    letters — matching the plain-Unicode form Pure stores.

    Note: this doesn't fix cases where DSpace's filename itself contains a
    genuinely different character rather than an encoding artifact.
    """
    if not filename:
        return filename
    unescaped = html.unescape(filename)
    return unicodedata.normalize("NFKC", unescaped)


def normalize(s):
    return s.strip().lower() if s else ""


def normalize_for_comparison(s):
    """Lowercase, replace punctuation with spaces, collapse whitespace."""
    if not s:
        return ""
    result = "".join(" " if char in PUNC else char for char in s.lower())
    return " ".join(result.split())


def sanitize_abstract(abstract: str) -> str:
    """Return empty string if abstract is a placeholder, otherwise return as-is."""
    if not abstract:
        return ""
    if abstract.strip().lower() == "[no abstract available]":
        return ""
    return abstract


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
            w for w in normalize_for_comparison(combined).split()
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
        w for w in normalize_for_comparison(combined).split()
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
        t1n, t2n = normalize_for_comparison(t1), normalize_for_comparison(t2)
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

    # Trailing full stops are citation punctuation, not part of the DOI
    # (e.g. "10.1080/09503153.2017.1339786." -> "...1339786").
    return f"https://doi.org/{match.group(1).rstrip('.')}"


def normalize_handle(value: str) -> str:
    if not isinstance(value, str):
        return value

    v = value.strip().lower()
    match = HANDLE_REGEX.match(v)

    if not match:
        return value  # not a valid handle → leave unchanged

    return f"http://hdl.handle.net/{match.group(1)}"


def is_handle_url(value) -> bool:
    """
    True if value is a Handle URL:
      - any hdl.handle.net URL, in any case (as before, now case-insensitive);
      - handle.net / www.handle.net URLs that normalize_handle recognises.
    A bare "10379/..." without a handle.net host is not treated as a URL here,
    exactly as before.
    """
    if not isinstance(value, str):
        return False
    lowered = value.lower()
    if "hdl.handle.net" in lowered:
        return True
    if "handle.net" not in lowered:
        return False
    return str(normalize_handle(value)).startswith("http://hdl.handle.net/")


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
            doi = f"https://doi.org/{match.group(1).rstrip('.')}"
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


# Separator between multiple values in one DSpace CSV cell (" ; "). At least
# one side must be whitespace so a DOI that itself contains a bare ";" (SICI
# DOIs such as "...3.0.CO;2-E") is never split.
_MULTI_VALUE_SEPARATOR = re.compile(r"\s+;\s*|\s*;\s+")


def is_valid_doi(value):
    """True if value is a DOI already normalised by normalize_doi()."""
    return isinstance(value, str) and value.startswith("https://doi.org/10.")


def parse_doi_field(raw):
    """
    Split a (possibly multi-valued) dc.identifier.doi cell into DOIs.

    Returns (dois, unrecognised):
      dois          unique normalised DOIs, in their original order
                    (e.g. "10.1/x ; 10.1/X" -> ["https://doi.org/10.1/x"])
      unrecognised  pieces that are not DOIs (URLs to other sites, ISBNs, ...)
    """
    dois, unrecognised, seen = [], [], set()
    if not raw:
        return dois, unrecognised
    for piece in _MULTI_VALUE_SEPARATOR.split(raw.strip()):
        piece = piece.strip()
        if not piece:
            continue
        normalized = normalize_doi(piece)
        if is_valid_doi(normalized):
            if normalized not in seen:
                seen.add(normalized)
                dois.append(normalized)
        else:
            unrecognised.append(piece)
    return dois, unrecognised


def _metadata_richness(obj):
    """Number of fields with a non-empty value -- used to pick among duplicates."""
    return sum(1 for v in obj.values() if v not in (None, "", [], {}))


def dedupe_by_key(items, key_func, label):
    """
    Remove duplicates from a list of dicts that share the same key_func(item).

    For each duplicate group the entry with the most populated fields is kept
    (so Pure metadata such as accessType / licenseType / versionType or a link
    description is never lost to an emptier copy); ties keep the earliest one.
    The kept entry takes the position of the group's first occurrence. Items
    whose key is empty are never treated as duplicates and are kept as-is.
    """
    result = []
    position_by_key = {}
    for item in items:
        key = key_func(item)
        if not key:
            result.append(item)
            continue
        if key not in position_by_key:
            position_by_key[key] = len(result)
            result.append(item)
            continue
        pos = position_by_key[key]
        if _metadata_richness(item) > _metadata_richness(result[pos]):
            result[pos] = item
        print(f"  🧹 Removed duplicate {label}: {key}")
    return result


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


def add_type_specific_fields(record, dspace_row, valid_journal_uuids=None, journal_lookup=None, log_entry=None, journal_diagnostics=None):
    """Add type-specific required fields based on typeDiscriminator"""
    type_disc = record["typeDiscriminator"]
    
    # Add peerReview if required for this type
    if type_requires_peer_review(type_disc):
        record["peerReview"] = get_default_peer_review_status(type_disc)
        
    if type_disc == "ContributionToJournal" or type_disc == "ContributionToPeriodical":
        # Journal: journal_uuid -> ISSN -> title (see resolve_journal_uuid);
        # if none is found the record is downgraded to OtherContribution below.
        journal_uuid, journal_source, journal_candidates = resolve_journal_uuid(
            dspace_row, type_disc, valid_journal_uuids, journal_lookup, journal_diagnostics
        )
        if journal_source in ("ISSN", "title") and log_entry is not None:
            log_entry["journalMatchedBy"] = journal_source
            if journal_candidates:
                log_entry["journalCandidates"] = journal_candidates

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
            if log_entry is not None:
                log_entry["typeChangedToOther"] = f"No journal found for {type_disc}"
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
    """Parse semicolon-separated author names from DSpace. HTML character
    references are decoded first, so "O&apos;Dowd, Colin" isn't split at the
    ";" of "&apos;"."""
    if not author_str:
        return []
    author_str = html.unescape(author_str)
    return [a.strip() for a in author_str.split(";") if a.strip()] 


def build_person_name_index(person_mapping):
    """Build a comprehensive index of all person name variations for O(1) lookup"""
    person_index = {}
    
    for person in person_mapping:
        # Pre-index this person's known paper identifiers
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

        # Normalize curved apostrophes to straight ones, and persist the fix
        # back onto the person dict so every downstream consumer (matching,
        # contributor building, output files, etc.) uses the corrected name.
        p_first = fix_apostrophe(person.get("firstName", ""))
        p_last = fix_apostrophe(person.get("lastName", ""))
        alt_firsts = [fix_apostrophe(af) for af in (person.get("alternativeFirstName", []) or [])]
        alt_lasts = [fix_apostrophe(al) for al in (person.get("alternativeLastName", []) or [])]

        person["firstName"] = p_first
        person["lastName"] = p_last
        if person.get("alternativeFirstName") is not None:
            person["alternativeFirstName"] = alt_firsts
        if person.get("alternativeLastName") is not None:
            person["alternativeLastName"] = alt_lasts

        all_firsts = [p_first] if p_first else []
        all_firsts.extend(alt_firsts)
        all_lasts = [p_last] if p_last else []
        all_lasts.extend(alt_lasts)
        
        for af in all_firsts:
            for al in all_lasts:
                key1 = (normalize_person_name(af), normalize_person_name(al))
                if key1 not in person_index:
                    person_index[key1] = []
                person_index[key1].append(person)
                
                key2 = (normalize_person_name(al), normalize_person_name(af))
                if key2 not in person_index:
                    person_index[key2] = []
                person_index[key2].append(person)
    
    return person_index


# Names containing any of these words (as whole words, any case) are
# institutions, not people: they are dropped from the DSpace contributors
# entirely -- neither added as contributors nor listed as unmatched.
NAME_STOPWORDS = [
    "university", "college", "academy", "institute", "association", "department",
    "school", "nuig", "ollscoil", "centre", "center", "laboratory", "institution",
    "organisation", "organization", "foundation", "society", "proceedings",
    "bank", "programme", "union", "education", "research", "national",
    "international", "group", "committee",
]
_NAME_STOPWORD_SET = set(NAME_STOPWORDS)


def is_institution_name(name):
    """True if the name contains a NAME_STOPWORDS word (whole words only)."""
    return bool(_NAME_STOPWORD_SET & set(re.findall(r"\w+", (name or "").lower())))


# Surname prefixes that DSpace sometimes leaves at the end of the first name
# ("Shea, Emma O’" = "O'Shea, Emma"). (prefix, written without a space).
# Longer prefixes first. A bare "O" (no apostrophe) is NOT included: in the
# data it is a middle initial ("Amer, Amal O").
SURNAME_PREFIXES = [
    ("mac giolla", False), ("mac con", False), ("mac an", False), ("nic an", False),
    ("van der", False),
    ("mhic", False), ("mac", False), ("nic", False), ("mc", True), ("o'", True),
    ("ó", False), ("ní", False), ("de", False), ("uí", False), ("ua", False),
    ("van", False), ("von", False), ("la", False),
]

_TRAILING_DIGITS = re.compile(r"(?<=[^\W\d_])\d+(?=[\s,]|$)")


def fold_accents(s):
    """Remove accents (diacritics): "Ní Ríordáin" -> "Ni Riordain"."""
    if not s:
        return ""
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def accent_count(s):
    """Number of accent marks in a string."""
    if not s:
        return 0
    return sum(1 for c in unicodedata.normalize("NFD", s) if unicodedata.combining(c))


def _split_person_name(person_name):
    """'Last, First' or 'First ... Last' -> (first, last). Unchanged split rule."""
    if "," in person_name:
        parts = [p.strip() for p in person_name.split(",", 1)]
        return (parts[1] if len(parts) > 1 else ""), parts[0]
    parts = person_name.split()
    if len(parts) >= 2:
        return " ".join(parts[:-1]), parts[-1]
    return person_name, ""


def _move_surname_prefix(first, last):
    """
    "Emma O’" + "Shea" -> ("Emma", "O’Shea"); "Jos de" + "Bruijn" -> ("Jos", "de Bruijn").
    Returns None if the first name doesn't end with a known prefix. The
    prefix keeps its original spelling.
    """
    words = first.split()
    for prefix, joined in SURNAME_PREFIXES:
        n = len(prefix.split())
        if len(words) > n and " ".join(words[-n:]).translate(_APOSTROPHE_TRANSLATION).lower() == prefix:
            written = " ".join(words[-n:])
            return " ".join(words[:-n]), (written + last if joined else written + " " + last)
    return None


def _repaired_name_candidates(person_name):
    """
    Corrected readings of a malformed DSpace name, in order:
      - a trailing comma with nothing after it is removed
        ("Damien Haberlin," -> "Damien Haberlin");
      - digits attached to the end of a word are removed (footnote marks:
        "Ní Fhlathartaigh1, Mary" -> "Ní Fhlathartaigh, Mary");
      - a surname prefix left at the end of the first name is moved to the
        surname ("Shea, Emma O’" -> "O’Shea, Emma").
    Returns a list of (first, last), not including the unchanged reading.
    """
    original = _split_person_name(person_name)
    cleaned = person_name.strip()
    if cleaned.endswith(","):
        cleaned = cleaned.rstrip(", ").strip()
    cleaned = _TRAILING_DIGITS.sub("", cleaned).strip()
    candidates = []
    base = _split_person_name(cleaned)
    if base != original and base[0] and base[1]:
        candidates.append(base)
    if "," in cleaned:
        moved = _move_surname_prefix(*base)
        if moved and moved[0] and moved not in candidates:
            candidates.append(moved)
    return candidates


_FOLDED_INDEX_CACHE = {"person_index": None, "folded": None}


def _folded_person_index(person_index):
    """The person index with accents removed from its keys (built once, cached)."""
    if _FOLDED_INDEX_CACHE["person_index"] is person_index:
        return _FOLDED_INDEX_CACHE["folded"]
    folded = {}
    for (first_key, last_key), entries in person_index.items():
        bucket = folded.setdefault((fold_accents(first_key), fold_accents(last_key)), [])
        for entry in entries:
            if all(entry is not e for e in bucket):
                bucket.append(entry)
    _FOLDED_INDEX_CACHE["person_index"] = person_index
    _FOLDED_INDEX_CACHE["folded"] = folded
    return folded


def match_person_name(person_name, person_index):
    """
    Find the person-mapping entries for a DSpace contributor name.
    Returns (matches, first, last): first/last is the reading of the DSpace
    name that matched (used to prefer an accented spelling).

    1. The name exactly as before (unchanged behaviour -- tried first, so an
       existing match can never change).
    2. Only if that finds nobody: the corrected readings from
       _repaired_name_candidates (trailing comma, trailing digits, surname
       prefix).
    3. Only if that also finds nobody: the same readings with accents ignored
       ("Ni Riordain" = "Ní Ríordáin"); entries spelled with accents first.
    """
    original = _split_person_name(person_name)
    readings = [original] + _repaired_name_candidates(person_name)
    for first, last in readings:
        matches = person_index.get((normalize_person_name(first), normalize_person_name(last)), [])
        if matches:
            return matches, first, last
    folded_index = _folded_person_index(person_index)
    for first, last in readings:
        key = (fold_accents(normalize_person_name(first)), fold_accents(normalize_person_name(last)))
        matches = folded_index.get(key, [])
        if matches:
            matches = sorted(
                matches,
                key=lambda e: -accent_count(f"{e.get('firstName', '')}{e.get('lastName', '')}"),
            )
            return matches, first, last
    return [], original[0], original[1]


def find_person_match(person_name, person_index):
    """Find matching person using pre-built index (see match_person_name)."""
    return match_person_name(person_name, person_index)[0]


def _with_case_of(base, accented):
    """
    The accented spelling written with the capitalisation (and apostrophe) of
    base, when both are the same letters: "MAIRE" + "Máire" -> "MÁIRE".
    """
    a = unicodedata.normalize("NFC", accented)
    b = unicodedata.normalize("NFC", base)
    if len(a) != len(b):
        return None
    out = []
    for ca, cb in zip(a, b):
        if ca.translate(_APOSTROPHE_TRANSLATION) == "'" and cb.translate(_APOSTROPHE_TRANSLATION) == "'":
            out.append(cb)
        elif fold_accents(ca).lower() == fold_accents(cb).lower():
            out.append(ca.upper() if cb.isupper() else ca.lower() if cb.islower() else ca)
        else:
            return None
    return "".join(out)


def prefer_accented_spelling(base, candidates):
    """
    The accented spelling of a name part is preferred, wherever it comes from.
    base is the spelling that would be written (from the person mapping);
    candidates are other spellings (mapping alternatives, the DSpace name, the
    existing Pure contributor's name). A candidate is used only if it is the
    same name apart from accents (and capitalisation / apostrophe form) and has
    more accents than base; it is then written with base's capitalisation.
    """
    if not base:
        return base
    base_key = fold_accents(normalize_person_name(base))
    best, best_marks = base, accent_count(base)
    for candidate in candidates:
        if not isinstance(candidate, str) or not candidate.strip():
            continue
        if fold_accents(normalize_person_name(candidate)) != base_key:
            continue
        if accent_count(candidate) > best_marks:
            best, best_marks = candidate.strip(), accent_count(candidate)
    if best is base:
        return base
    return _with_case_of(base.strip(), best) or best


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

    If a candidate's pre-indexed paper set contains any of the current
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

    # Institution names (NAME_STOPWORDS) are not people: drop them entirely,
    # so they are neither added as contributors nor listed as unmatched.
    def drop_institutions(names):
        institutions = [n for n in names if is_institution_name(n)]
        if institutions:
            print(f"  ℹ️ Skipping institution name(s) listed as contributors: {institutions}")
        return [n for n in names if not is_institution_name(n)]

    authors = drop_institutions(authors)
    editors = drop_institutions(editors)
    translators = drop_institutions(translators)
    illustrators = drop_institutions(illustrators)

    # Resolve author/editor overlap for the same name
    # Apostrophe-insensitive, so e.g. author "O'Malley, Mary" and editor
    # "O’Malley, Mary" are recognised as the same person.
    author_set = {normalize_person_name(n) for n in authors}
    editor_set = {normalize_person_name(n) for n in editors}
    overlap = author_set & editor_set

    if overlap:
        if prefer_editor:
            # Remove overlapping names from authors, keep in editors
            authors = [n for n in authors if normalize_person_name(n) not in overlap]
            print(f"  ℹ️ Duplicate author/editor names — keeping as editor for type '{dspace_type}': "
                  f"{[n for n in editors if normalize_person_name(n) in overlap]}")
        else:
            # Remove overlapping names from editors, keep in authors
            editors = [n for n in editors if normalize_person_name(n) not in overlap]
            print(f"  ℹ️ Duplicate author/editor names — keeping as author for type '{dspace_type}': "
                  f"{[n for n in authors if normalize_person_name(n) in overlap]}")

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
        existing_contributors: List of existing Pure contributor dicts. When provided
                               (updating a record in precedence mode):
                               - DSpace contributors already present in Pure (by person
                                 UUID, then by name) reuse the linked Pure contributor
                                 rather than being rebuilt;
                               - every Pure contributor NOT used that way is kept, after
                                 the DSpace ones, in its original order and unchanged --
                                 nothing already in Pure is discarded.
                               Pass None or [] for new records or override mode (only the
                               DSpace contributors are returned).
                               In every mode the same person is never added twice.
        pure_uuid:            UUID of the matched Pure record, used in unmatched contributor
                              log entries. Pass None for new records.

    Returns:
        (final_contributors, unmatched_contributors)
        final_contributors:    List of resolved contributor dicts ready for Pure: the DSpace
                               contributors in DSpace order, followed by any unused Pure
                               contributors. Empty if no DSpace contributor could be
                               resolved (the caller then leaves Pure's contributors as they are).
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
            key = (normalize_person_name(pair[0].strip()), normalize_person_name(pair[1].strip()))
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

    # dc.identifier.doi is a multi-entry field -- use every DOI in it
    record_paper_dois.update(parse_doi_field(dspace_row.get("dc.identifier.doi", ""))[0])
    for doi in extract_dois_from_uri(dspace_row.get("dc.identifier.uri", "")):
        record_paper_dois.add(normalize_doi(doi))
    for handle in extract_handles_from_uri(dspace_row.get("dc.identifier.uri", "")):
        record_paper_handles.add(normalize_handle(handle))

    final_contributors = []
    unmatched_contributors = []

    # Never add the same person twice: person/externalPerson UUIDs already in
    # final_contributors, and the Pure contributors already reused (by id()).
    added_person_uuids = set()
    used_existing_ids = set()

    def person_uuid_of(contrib):
        ref = contrib.get("person") or contrib.get("externalPerson") or {}
        return ref.get("uuid")

    # Helper to build an unmatched entry
    handles = extract_handles_from_uri(dspace_row.get("dc.identifier.uri", ""))
    handle_for_log = handles[0] if handles else None

    for role, contributor_names in contributors_by_role.items():
        print(f"  ➤ Processing {len(contributor_names)} {role}(s)")

        for contributor_name in contributor_names:
            print(f"    ➤ Checking match for {role}: '{contributor_name}'")
            matches, dspace_first, dspace_last = match_person_name(contributor_name, person_index)

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
            name_key = (normalize_person_name(first), normalize_person_name(last))

            # Spelling written to Pure: the person mapping's, except that an
            # accented spelling of the same name is preferred, wherever it
            # comes from (mapping alternatives or the DSpace name).
            display_first = prefer_accented_spelling(first, list(matched_person.get("alternativeFirstName") or []) + [dspace_first])
            display_last = prefer_accented_spelling(last, list(matched_person.get("alternativeLastName") or []) + [dspace_last])

            # Reuse existing contributor if present (skip when existing_contributors is empty,
            # i.e. for new records or override mode)
            if existing_contributors:
                existing_match, matched_by = None, None
                if uuid_value in existing_by_uuid:
                    existing_match, matched_by = existing_by_uuid[uuid_value], "UUID"
                elif name_key in existing_by_name:
                    existing_match, matched_by = existing_by_name[name_key], "name"
                if existing_match is not None:
                    existing_uuid = person_uuid_of(existing_match)
                    if id(existing_match) in used_existing_ids or existing_uuid in added_person_uuids:
                        print(f"        ℹ️ {first} {last} is already on the record — not added twice")
                        continue
                    print(f"        ℹ️ Contributor already exists (by {matched_by}), using existing: {first} {last}")
                    existing_contrib = dict(existing_match)
                    existing_name = existing_contrib.get("name") or {}
                    reuse_first = prefer_accented_spelling(display_first, [existing_name.get("firstName")])
                    reuse_last = prefer_accented_spelling(display_last, [existing_name.get("lastName")])
                    if existing_name.get("firstName") != reuse_first or existing_name.get("lastName") != reuse_last:
                        print(f"        ✏️  Updating name spelling to match authors JSON: {existing_contrib.get('name', {})} → {reuse_first} {reuse_last}")
                    existing_contrib["name"] = {"firstName": reuse_first, "lastName": reuse_last}
                    final_contributors.append(existing_contrib)
                    used_existing_ids.add(id(existing_match))
                    added_person_uuids.update(u for u in (existing_uuid, uuid_value) if u)
                    continue

            if uuid_value in added_person_uuids:
                print(f"        ℹ️ {first} {last} is already on the record — not added twice")
                continue

            contributor = build_contributor(
                matched_person, role, pure_type_key,
                collect_external_orgs=COLLECT_EXTERNAL_ORGS,
            )
            if contributor and (display_first, display_last) != (first, last) and isinstance(contributor.get("name"), dict):
                contributor["name"] = dict(contributor["name"], firstName=display_first, lastName=display_last)
            if contributor:
                final_contributors.append(contributor)
                added_person_uuids.add(uuid_value)
                print(f"        ✅ Added {role}: {first} {last}")

    # Build on what Pure already has: keep every existing Pure contributor that
    # wasn't reused above, after the DSpace contributors, in its original order
    # and unchanged. Only done when at least one DSpace contributor was resolved;
    # otherwise final_contributors stays empty and the caller leaves Pure's
    # contributors exactly as they are.
    if existing_contributors and final_contributors:
        kept = 0
        for contrib in existing_contributors:
            if isinstance(contrib, dict) and id(contrib) not in used_existing_ids:
                final_contributors.append(dict(contrib))
                kept += 1
        if kept:
            print(f"  ℹ️ Kept {kept} existing Pure contributor(s) not listed in DSpace")

    return final_contributors, unmatched_contributors


def has_dspace_uuid(record):
    """True if a Pure record has a DSpace identifier (idSource "DSpace") with a non-empty value."""
    return any(
        isinstance(identifier, dict)
        and identifier.get("idSource") == "DSpace"
        and str(identifier.get("value") or "").strip()
        for identifier in (record.get("identifiers") or [])
    )


def dspace_item_handles(row):
    """Normalised repository Handles of a DSpace item (from dc.identifier.uri), in order."""
    handles = []
    for handle in extract_handles_from_uri((row or {}).get("dc.identifier.uri", "")):
        normalized = normalize_handle(handle)
        if normalized not in handles:
            handles.append(normalized)
    return handles


def dspace_item_repo_dois(row):
    """Normalised repository DOIs (10.13025) of a DSpace item: dc.identifier.uri first, then dc.identifier.doi."""
    dois = [d for d in extract_dois_from_uri((row or {}).get("dc.identifier.uri", "")) if "10.13025" in d]
    for d in parse_doi_field((row or {}).get("dc.identifier.doi", ""))[0]:
        if "10.13025" in d and d not in dois:
            dois.append(d)
    return dois


def is_repository_doi_ev(ev):
    """True for a DOI electronic version whose DOI is a repository DOI (10.13025)."""
    return (isinstance(ev, dict) and ev.get("typeDiscriminator") == "DoiElectronicVersion"
            and "10.13025" in str(ev.get("doi") or ""))


def choose_repository_doi_ev(existing_evs, dspace_item_row, dspace_item_uuid, record_uuid=None, quiet=False):
    """
    The ONE repository DOI electronic version a Pure record should carry for a
    DSpace item -- used by both the electronic-version step and the
    single-identity step of an update, so they always agree.

    - The item's repository DOI: the first 10.13025 DOI in dc.identifier.uri,
      then in dc.identifier.doi (only 10.13025 DOIs count, whichever field
      they are in). Pure's electronic version with that DOI is reused (copy),
      otherwise a new one is created.
    - If the item has no repository DOI (or isn't in the CSV): Pure's own
      repository DOI is kept, unless the CSV shows it belongs to another
      DSpace item; if several remain, the first is kept and a manual-review
      warning is printed.
    The result always gets the repository metadata (Open / CC BY / Author
    accepted manuscript, or Embargoed + period) from the item's embargo --
    when the item isn't in the CSV, the metadata is left as Pure has it.
    Returns the electronic version, or None.
    """
    repo_evs = [dict(ev) for ev in (existing_evs or []) if is_repository_doi_ev(ev)]
    wanted_dois = dspace_item_repo_dois(dspace_item_row) if dspace_item_row else []
    if wanted_dois:
        wanted = wanted_dois[0]
        chosen = next((ev for ev in repo_evs if normalize_doi(ev.get("doi") or "") == wanted), None)
        if chosen is None:
            chosen = build_electronic_version(doi=wanted)
    else:
        item_key = (dspace_item_uuid or "").strip().lower()
        candidates = [
            ev for ev in repo_evs
            if _DSPACE_ITEMS["uuid_by_repo_doi"].get(normalize_doi(ev.get("doi") or "")) in (None, item_key)
        ]
        if len(candidates) > 1 and not quiet:
            print(f"  ⚠️ MANUAL REVIEW REQUIRED: {len(candidates)} repository DOIs on record {record_uuid} and none "
                  f"known for DSpace item {dspace_item_uuid} — keeping the first: {candidates[0].get('doi')}")
        chosen = candidates[0] if candidates else None
    if chosen is None:
        return None
    if isinstance(chosen.get("doi"), str):
        chosen["doi"] = normalize_doi(chosen["doi"])
    if dspace_item_row:
        _, embargo_active, _, embargo_period = resolve_embargo_and_access(dspace_item_row)
        apply_repository_access_license_version(chosen, embargo_active, embargo_period)
    return chosen


def build_dspace_item_lookups(dspace_rows):
    """Fill _DSPACE_ITEMS from the DSpace CSV rows."""
    _DSPACE_ITEMS["by_uuid"] = {}
    _DSPACE_ITEMS["uuid_by_handle"] = {}
    _DSPACE_ITEMS["uuid_by_repo_doi"] = {}
    for row in dspace_rows:
        uuid = (row.get("uuid") or "").strip().lower()
        if not uuid:
            continue
        _DSPACE_ITEMS["by_uuid"].setdefault(uuid, row)
        for handle in dspace_item_handles(row):
            _DSPACE_ITEMS["uuid_by_handle"].setdefault(handle, uuid)
        for doi in dspace_item_repo_dois(row):
            _DSPACE_ITEMS["uuid_by_repo_doi"].setdefault(doi, uuid)


def enforce_single_dspace_identity(pure_record, dspace_row, updated_record, log_entry):
    """
    Make the updated record carry exactly one DSpace identity: one DSpace UUID
    identifier, one repository Handle link and one repository DOI electronic
    version, all belonging to the same DSpace item ("target").

    Target: the CSV row's item -- unless the Pure record is already linked to
    a different DSpace item and DSPACE_UUID_PREFERENCE is "pure", in which
    case Pure's existing DSpace UUID is kept (and that item's Handle /
    repository DOI are used when the item is in the CSV).

    When the target item's Handle / repository DOI isn't known (not in the
    CSV, or the item has none), Pure's existing ones are kept, except those
    the CSV shows belong to a different DSpace item; if several remain, a
    manual-review warning is printed.
    """
    row_uuid = (dspace_row.get("uuid") or "").strip()
    pure_uuids = dspace_uuids_of(pure_record)
    mismatch = bool(pure_uuids) and row_uuid.lower() not in {u.lower() for u in pure_uuids}
    if mismatch and DSPACE_UUID_PREFERENCE == "pure":
        target_uuid = pure_uuids[0]
        target_row = _DSPACE_ITEMS["by_uuid"].get(target_uuid.lower())
        log_entry["dspaceUuidResolution"] = "Pure's DSpace UUID kept"
        print(f"  ℹ️ DSpace UUID mismatch — keeping Pure's DSpace UUID {target_uuid} (DSPACE_UUID_PREFERENCE = 'pure')")
    else:
        target_uuid = row_uuid
        target_row = dspace_row
        if mismatch:
            log_entry["dspaceUuidResolution"] = "DSpace UUID from the CSV written"
            print(f"  ℹ️ DSpace UUID mismatch — writing the CSV's DSpace UUID {row_uuid} (DSPACE_UUID_PREFERENCE = 'dspace')")
    if not target_uuid:
        return
    target_key = target_uuid.lower()

    def belongs_elsewhere(owner):
        return owner is not None and owner != target_key

    # --- one DSpace UUID identifier ---
    # Start from Pure's own identifiers; drop every DSpace identifier that is
    # NOT the target (and repeats of the target), then merge exactly as step 10
    # does -- so a record that already carries only the target DSpace UUID
    # comes out exactly as before.
    current_ids = pure_record.get("identifiers") or []
    kept_ids, kept_target = [], False
    for ident in current_ids:
        if isinstance(ident, dict) and ident.get("idSource") == "DSpace":
            if str(ident.get("value") or "").strip().lower() == target_key and not kept_target:
                kept_ids.append(ident)
                kept_target = True
            continue
        kept_ids.append(ident)
    new_ids = merge_identifiers(kept_ids, target_uuid)
    if new_ids != (pure_record.get("identifiers") or []) or "identifiers" in updated_record:
        updated_record["identifiers"] = new_ids

    # --- one repository Handle ---
    current_links = updated_record["links"] if "links" in updated_record else (pure_record.get("links") or [])
    handle_links = [l for l in current_links if isinstance(l, dict) and is_handle_url(l.get("url", ""))]
    other_links = [l for l in current_links if not (isinstance(l, dict) and is_handle_url(l.get("url", "")))]
    target_handles = dspace_item_handles(target_row) if target_row else []
    if target_handles:
        wanted = target_handles[0]
        existing = next((l for l in handle_links if normalize_handle(l.get("url", "")) == wanted), None)
        new_handles = [existing or build_link(wanted, alias="Handle", description="Repository Handle")]
    else:
        # Target item's Handle unknown: start from Pure's ORIGINAL Handles (the
        # update may already have put the CSV row's Handle in their place).
        original_handles = [l for l in (pure_record.get("links") or [])
                            if isinstance(l, dict) and is_handle_url(l.get("url", ""))]
        new_handles = [l for l in original_handles
                       if not belongs_elsewhere(_DSPACE_ITEMS["uuid_by_handle"].get(normalize_handle(l.get("url", ""))))]
        if len(new_handles) > 1:
            print(f"  ⚠️ MANUAL REVIEW REQUIRED: {len(new_handles)} repository Handles on record {pure_record.get('uuid')} "
                  f"and none known for DSpace item {target_uuid} — keeping them")
    new_links = new_handles + other_links
    if new_links != current_links:
        updated_record["links"] = new_links

    # --- one repository DOI (same rule as step 5c: choose_repository_doi_ev) ---
    current_evs = updated_record["electronicVersions"] if "electronicVersions" in updated_record else (pure_record.get("electronicVersions") or [])
    other_evs = [ev for ev in current_evs if not is_repository_doi_ev(ev)]
    # quiet: step 5c has already reported any manual-review case for this record
    chosen = choose_repository_doi_ev(pure_record.get("electronicVersions") or [], target_row, target_uuid,
                                      pure_record.get("uuid"), quiet=(target_row is dspace_row))
    new_repo = [chosen] if chosen is not None else []
    new_evs = new_repo + other_evs
    if new_evs != current_evs:
        updated_record["electronicVersions"] = new_evs


# ---- Title matching safeguards ----
def dspace_publication_year(row):
    match = re.search(r"\b(1[5-9]\d\d|20\d\d)\b", (row or {}).get("dc.date.issued", "") or "")
    return int(match.group(1)) if match else None


def pure_publication_years(item):
    years = set()
    for status in (item or {}).get("publicationStatuses") or []:
        year = ((status or {}).get("publicationDate") or {}).get("year")
        if isinstance(year, int) or (isinstance(year, str) and year.isdigit()):
            years.add(int(year))
    return years


def dspace_output_type_key(row):
    """
    Pure output type (e.g. 'contributiontojournal') a DSpace item maps to --
    the same mapping create_new_record_from_dspace uses: an unmapped dc.type
    becomes Other contribution.
    """
    dspace_type = ((row or {}).get("dc.type") or "").strip().lower()
    uri = dspace_pure_subtype_map.get(dspace_type, "/dk/atira/pure/researchoutput/researchoutputtypes/othercontribution/other")
    return get_pure_type_key(uri)


def pure_output_type_key(item):
    uri = ((item or {}).get("type") or {}).get("uri")
    return get_pure_type_key(uri) if uri else None


def full_title_key(title, subtitle):
    """
    Title + subtitle as one comparison key for the "identical title" test (an
    embedded copy of the subtitle is not counted twice). Case, punctuation and
    symbols are ignored, but -- unlike normalize_for_comparison -- digits are
    kept, so "An Reiviú 2015" and "An Reiviú 2024", or "Part 1" and "Part 2",
    are not identical.
    """
    title = (title or "").strip()
    subtitle = (subtitle or "").strip()
    if subtitle:
        title = strip_subtitle_from_title(title, subtitle)
    text = unicodedata.normalize("NFKC", f"{title} {subtitle}").lower()
    text = "".join(" " if unicodedata.category(ch)[0] in ("P", "S") else ch for ch in text)
    return " ".join(text.split())


# Small words ignored by the word-level title check.
_TITLE_SMALL_WORDS = {
    "a", "an", "the", "of", "and", "in", "on", "for", "to", "with", "by", "at",
    "from", "as", "or", "into", "its", "their", "is", "are", "be",
}
# Two words count as the same word from this similarity (rapidfuzz ratio):
# spelling variants score 83-95 ("centre"/"center", "behaviour"/"behavior",
# "cell"/"cells"), different words at most ~35.
TITLE_WORD_SIMILARITY = 80


def _title_content_words(text):
    """Content words of a title: markup tags such as "[clc]" / "[/clc]" removed
    ("t[clc]e[/clc]v" = "TeV"), lower case, punctuation removed, small words dropped."""
    text = re.sub(r"\[/?[A-Za-z]{1,10}\]", "", text or "")
    return [w for w in full_title_key(text, "").split() if w not in _TITLE_SMALL_WORDS]


def _missing_words(words, others):
    """Words of `words` that have no counterpart (same word or spelling variant) in `others`."""
    return [w for w in words if not any(fuzz.ratio(w, o) >= TITLE_WORD_SIMILARITY for o in others)]


def title_words_agree(dspace_title, dspace_subtitle, pure_title, pure_subtitle):
    """
    Word-level check for a fuzzy title match: for at least one combination of
    title / title + subtitle on each side, every content word of each title has
    a counterpart in the other (spelling variants and small typos allowed,
    small words ignored). An extra or missing content word ("The
    cost-effectiveness of ..." vs "The effectiveness of ...") means the titles
    are different. Returns (agree, extra_words) -- extra_words from the closest
    combination, for the log.
    """
    dspace_versions = [dspace_title] + ([f"{dspace_title} {dspace_subtitle}"] if dspace_subtitle else [])
    pure_versions = [pure_title] + ([f"{pure_title} {pure_subtitle}"] if pure_subtitle else [])
    closest = None
    for d_text in dspace_versions:
        d_words = _title_content_words(d_text)
        for p_text in pure_versions:
            p_words = _title_content_words(p_text)
            if not d_words or not p_words:
                continue
            extra = _missing_words(d_words, p_words) + _missing_words(p_words, d_words)
            if not extra:
                return True, []
            if closest is None or len(extra) < len(closest):
                closest = extra
    return False, closest or []


def dspace_publisher_dois(row):
    """Normalised publisher DOIs of a DSpace item (dc.identifier.doi, repository DOIs excluded)."""
    return {d for d in parse_doi_field((row or {}).get("dc.identifier.doi", ""))[0] if "10.13025" not in d}


def pure_publisher_dois(item):
    """Normalised publisher DOIs of a Pure record (DOI electronic versions and DOI links, repository DOIs excluded)."""
    dois = set()
    for ev in (item or {}).get("electronicVersions") or []:
        if isinstance(ev, dict) and ev.get("doi"):
            doi = normalize_doi(ev["doi"])
            if is_valid_doi(doi) and "10.13025" not in doi:
                dois.add(doi)
    for link in (item or {}).get("links") or []:
        url = (link or {}).get("url", "") if isinstance(link, dict) else ""
        if "doi.org" in url:
            doi = normalize_doi(url)
            if is_valid_doi(doi) and "10.13025" not in doi:
                dois.add(doi)
    return dois


def owned_by_other_dspace_item(row, item):
    """
    The DSpace item(s) in the CSV that a Pure record already belongs to, when
    they are NOT the row's item ([] otherwise). A record with such an owner
    is another item's record.
    """
    row_uuid = (row.get("uuid") or "").strip().lower()
    pure_uuids = dspace_uuids_of(item or {})
    if not pure_uuids or row_uuid in {u.lower() for u in pure_uuids}:
        return []
    return [u for u in pure_uuids if u.lower() in _DSPACE_ITEMS["by_uuid"]]


def _pure_side_types(item):
    """Output types of a Pure record: its linked DSpace items' dc.type when they are in the CSV, else its own type."""
    linked_rows = [_DSPACE_ITEMS["by_uuid"].get(u.lower()) for u in dspace_uuids_of(item or {})]
    linked_rows = [r for r in linked_rows if r]
    if linked_rows:
        return {dspace_output_type_key(r) for r in linked_rows}, linked_rows
    return {t for t in [pure_output_type_key(item)] if t}, linked_rows


# Titles of at most this many words (small words included) are "short":
# generic titles such as "Introduction", "Editorial", "Book review". A title
# match on a short title also needs an author in common.
SHORT_TITLE_MAX_WORDS = 5


def _author_key(first, last):
    """(surname, first initial) comparison key: case, accents and apostrophe form ignored."""
    surname = " ".join(re.sub(r"[^\w']+", " ", fold_accents(normalize_person_name(last or ""))).split())
    if not surname:
        return None
    first_folded = re.sub(r"[^a-z]", "", fold_accents(normalize_person_name(first or "")))
    return surname, first_folded[:1]


def dspace_author_keys(row):
    """Author keys of a DSpace item's people (author, editor, translator and illustrator fields; institutions excluded)."""
    keys = set()
    for field in ("dc.contributor.author", "dc.contributor.editor",
                  "dc.contributor.translator", "dc.contributor.illustrator"):
        for name in parse_author_names((row or {}).get(field, "")):
            if is_institution_name(name):
                continue
            key = _author_key(*_split_person_name(name))
            if key:
                keys.add(key)
    return keys


def pure_author_keys(item):
    """Author keys of a Pure record's contributors."""
    keys = set()
    for contributor in (item or {}).get("contributors") or []:
        name = (contributor or {}).get("name") or {} if isinstance(contributor, dict) else {}
        key = _author_key(name.get("firstName", ""), name.get("lastName", ""))
        if key:
            keys.add(key)
    return keys


def authors_in_common(row_keys, item_keys):
    """True if a person appears on both sides: same surname, and same first initial (or no initial on one side)."""
    for surname, initial in row_keys:
        for other_surname, other_initial in item_keys:
            if surname == other_surname and (initial == other_initial or not initial or not other_initial):
                return True
    return False


def confirm_title_match(row, item, dspace_title, dspace_subtitle):
    """
    Wrapper around the title rules (_confirm_title_match_rules): a match that
    passes them but involves a SHORT title (at most SHORT_TITLE_MAX_WORDS words,
    small words included, on either side) also needs an author in common.
    """
    accepted, reason = _confirm_title_match_rules(row, item, dspace_title, dspace_subtitle)
    if not accepted:
        return accepted, reason
    pure_title = ((item or {}).get("title") or {}).get("value", "")
    pure_subtitle = ((item or {}).get("subTitle") or {}).get("value", "")
    shortest = min(len(full_title_key(dspace_title, dspace_subtitle).split()),
                   len(full_title_key(pure_title, pure_subtitle).split()))
    if shortest <= SHORT_TITLE_MAX_WORDS:
        row_keys, item_keys = dspace_author_keys(row), pure_author_keys(item)
        if not item_keys:
            return False, f"short title ({shortest} words) and the Pure record has no contributors to compare"
        if not authors_in_common(row_keys, item_keys):
            return False, f"short title ({shortest} words) and no author in common"
        return True, f"{reason}; short title confirmed by a common author"
    return accepted, reason


def _confirm_title_match_rules(row, item, dspace_title, dspace_subtitle):
    """
    Decide whether a title match between a DSpace item and a Pure record is
    accepted. Returns (accepted, reason).
      - 100% match (full title + subtitle identical on both sides): accepted
        unless the research output types differ.
      - Anything else (fuzzy, or equal only when one side's subtitle is
        ignored): accepted only if the publication year AND the research
        output type are confirmed to be the same.
    The Pure side's type: when the Pure record belongs to DSpace item(s) that
    are in the CSV, their dc.type is used (compared like with like, and not
    affected by a record having been created as Other contribution because
    its journal wasn't found); otherwise the Pure record's own type. Years:
    the Pure record's publication years, plus those linked items' years.
    """
    # Fix 4: a Pure record that already belongs to ANOTHER DSpace item in the
    # CSV is that item's record -- never matched to this row by title.
    owners = owned_by_other_dspace_item(row, item)
    if owners:
        return False, f"the Pure record belongs to another DSpace item {owners}"

    # Fix 2: if both sides have publisher DOIs and none agree, they are
    # different outputs, whatever the titles say.
    row_dois, item_dois = dspace_publisher_dois(row), pure_publisher_dois(item)
    if row_dois and item_dois and not (row_dois & item_dois):
        return False, f"different publisher DOIs ({sorted(row_dois)} vs {sorted(item_dois)})"

    pure_title = ((item or {}).get("title") or {}).get("value", "")
    pure_subtitle = ((item or {}).get("subTitle") or {}).get("value", "")
    dspace_key = full_title_key(dspace_title, dspace_subtitle)
    pure_key = full_title_key(pure_title, pure_subtitle)
    exact = dspace_key == pure_key

    # Numbers in the titles must agree: items of a numbered series ("Factsheet
    # No. 17" / "No. 18", "Volume 5" / "Volume 6", "2014" / "1998-2022") are
    # different outputs, even when the rest of the title is the same.
    dspace_numbers = sorted(n.lstrip("0") or "0" for n in re.findall(r"\d+", dspace_key))
    pure_numbers = sorted(n.lstrip("0") or "0" for n in re.findall(r"\d+", pure_key))
    if dspace_numbers != pure_numbers:
        return False, f"numbers in the titles differ ({dspace_numbers} vs {pure_numbers})"

    linked_rows = [_DSPACE_ITEMS["by_uuid"].get(u.lower()) for u in dspace_uuids_of(item or {})]
    linked_rows = [r for r in linked_rows if r]
    d_type = dspace_output_type_key(row)
    if linked_rows:
        p_types = {dspace_output_type_key(r) for r in linked_rows}
    else:
        p_types = {t for t in [pure_output_type_key(item)] if t}

    d_year = dspace_publication_year(row)
    p_years = pure_publication_years(item) | {y for y in (dspace_publication_year(r) for r in linked_rows) if y}

    if exact:
        if d_type and p_types and d_type not in p_types:
            return False, f"identical title but different output type ({d_type} vs {sorted(p_types)})"
        # Fix 2: identical titles must also be from the same year (generic
        # titles such as "Introduction" or "Editorial" recur every year).
        # When a year is missing on either side it can't be compared.
        if d_year is not None and p_years and d_year not in p_years:
            return False, f"identical title but different year ({d_year} vs {sorted(p_years)})"
        return True, "identical title"

    if d_year is None or not p_years:
        return False, "title not identical and publication year can't be compared"
    if d_year not in p_years:
        return False, f"title not identical and different year ({d_year} vs {sorted(p_years)})"
    if not d_type or not p_types:
        return False, "title not identical and output type can't be compared"
    if d_type not in p_types:
        return False, f"title not identical and different output type ({d_type} vs {sorted(p_types)})"
    return True, "same year and output type"


def publisher_doi_conflict(row, item, threshold):
    """
    A publisher-DOI match is not trusted when the Pure record already belongs
    to a DIFFERENT DSpace item and the two don't describe the same output --
    the same DOI on two different DSpace items is a data error (a DOI copied
    to the wrong item) or a DOI shared by several outputs (a book's DOI on its
    chapters). The match is kept only if the titles agree at word level
    (title_words_agree, the strict check used for fuzzy title matches) AND
    the output types are the same. Returns a reason string, or None if there
    is no conflict. (threshold is kept for compatibility; no longer used.)
    """
    pure_uuids = dspace_uuids_of(item)
    row_uuid = (row.get("uuid") or "").strip().lower()
    if not pure_uuids or row_uuid in {u.lower() for u in pure_uuids}:
        return None
    dspace_subtitle = (row.get("dc.title.subtitle") or row.get("dc.title.alternative") or "").strip()
    words_ok, extra_words = title_words_agree(
        (row.get("dc.title") or "").strip(), dspace_subtitle,
        ((item.get("title") or {}).get("value") or "").strip(),
        ((item.get("subTitle") or {}).get("value") or "").strip(),
    )
    if not words_ok:
        return (f"same publisher DOI, but the Pure record belongs to DSpace item(s) {pure_uuids} "
                f"and the titles differ (words without a counterpart: {extra_words})")
    d_type = dspace_output_type_key(row)
    p_types, _ = _pure_side_types(item)
    if d_type and p_types and d_type not in p_types:
        return (f"same publisher DOI, but the Pure record belongs to DSpace item(s) {pure_uuids} "
                f"and the output type differs ({d_type} vs {sorted(p_types)})")
    return None


def dspace_uuids_of(record):
    """All non-empty DSpace identifier values (idSource "DSpace") of a Pure record."""
    return [
        str(identifier.get("value")).strip()
        for identifier in (record.get("identifiers") or [])
        if isinstance(identifier, dict)
        and identifier.get("idSource") == "DSpace"
        and str(identifier.get("value") or "").strip()
    ]


def resolve_record_duplicate(records, log_entry=None):
    """
    Choose which of several matching Pure records to update.

    DSpace identifier first:
      - exactly one duplicate has a DSpace UUID among its identifiers -> that
        record is chosen, with no further comparison;
      - several have one -> duplicates without a DSpace UUID are discarded
        and the standard comparison below runs on the rest only;
      - none has one -> the standard comparison runs on all of them.
    Standard comparison: record with most metadata or updated by real user.

    If a dict is passed as log_entry, log_entry["duplicateResolution"]
    records which of the three cases applied.
    """
    if not records:
        return None

    with_dspace_uuid = [r for r in records if has_dspace_uuid(r)]
    if len(with_dspace_uuid) == 1:
        print(f"  ✅ Duplicates: only {with_dspace_uuid[0].get('uuid')} has a DSpace UUID — updating it")
        if log_entry is not None:
            log_entry["duplicateResolution"] = "only record with a DSpace UUID"
        return with_dspace_uuid[0]
    if len(with_dspace_uuid) > 1:
        print(f"  ℹ️ Duplicates: {len(with_dspace_uuid)} of {len(records)} have a DSpace UUID — comparing only those")
        if log_entry is not None:
            log_entry["duplicateResolution"] = f"standard comparison among {len(with_dspace_uuid)} of {len(records)} records with a DSpace UUID"
        records = with_dspace_uuid
    elif log_entry is not None:
        log_entry["duplicateResolution"] = "standard comparison (no record has a DSpace UUID)"

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


def build_electronic_version(doi, version_type_uri=None, access_type="UNKNOWN",
                             license_type=None, embargo_end_date=None):
    """
    Build electronic version object ONLY when a DOI exists.
    If no DOI is supplied, return None (Pure must not receive an electronicVersion entry).

    A DoiElectronicVersion built here never carries licence, openness-status,
    or manuscript-version metadata directly from this function -- callers
    apply the correct treatment afterwards depending on whether the DOI is
    repository-sourced (apply_repository_access_license_version) or not
    (ensure_default_access_type only -- licence/version/embargo are left
    exactly as Pure already has them for anything not repository-sourced).
    version_type_uri, access_type, license_type and embargo_end_date are
    kept as parameters purely so every existing call site still works
    unchanged; they are intentionally ignored.
    """
    if not doi:
        return None

    return {
        "typeDiscriminator": "DoiElectronicVersion",
        "doi": doi,
    }


DEFAULT_UNKNOWN_ACCESS_TYPE = {
    "uri": "/dk/atira/pure/core/openaccesspermission/unknown",
    "term": {"en_IE": "Unknown"}
}

EMBARGOED_ACCESS_TYPE_URI = "/dk/atira/pure/core/openaccesspermission/embargoed"


def ensure_default_access_type(ev):
    """
    accessType is a mandatory field on every Pure electronic version. For
    anything not sourced from the DSpace repository (a publisher DOI, any
    other DOI/link electronic version, or a FileElectronicVersion on a
    record that isn't DSpace-linked), Pure's existing accessType always
    takes precedence and is left untouched -- with one exception forced by
    Pure's own validation, not by us: if the EV already has an embargoPeriod
    set, Pure requires accessType to be "Embargoed", full stop, even once
    the embargo end date has passed (Pure's own error message: "Correct
    this by changing 'Public access to file' to 'Embargoed' even if the
    embargo end date has passed. The file will then be publicly available
    and classified as Open Access."). A record with an embargoPeriod but a
    missing or non-"Embargoed" accessType is rejected outright by Pure with
    validation.accessextensionembargo.embargodateswhennotembargoed -- so
    that combination is corrected here rather than left as Pure finds it.
    embargoPeriod itself is never touched or cleared by this function.

    A default of "Unknown" is filled in only when accessType is missing
    and there's no embargoPeriod present to force "Embargoed" instead.
    """
    if ev.get("embargoPeriod") and (ev.get("accessType") or {}).get("uri") != EMBARGOED_ACCESS_TYPE_URI:
        ev["accessType"] = {"uri": EMBARGOED_ACCESS_TYPE_URI}
        return ev
    if not ev.get("accessType"):
        ev["accessType"] = dict(DEFAULT_UNKNOWN_ACCESS_TYPE)
    return ev


def apply_repository_access_license_version(ev, embargo_active, embargo_period):
    """
    Apply the repository's standard electronic-version metadata: access is
    open unless an active embargo says otherwise, licence is always CC BY,
    and version type is always "Author accepted manuscript". Used for both
    the repository DoiElectronicVersion (10.13025) and any FileElectronicVersion
    on a DSpace-linked record -- both are, by definition, sourced from the
    institutional repository, so both get identical treatment.

    accessType and embargoPeriod are always set together here (Embargoed +
    a period, or Open + no period at all), so this can never itself produce
    the accessType/embargoPeriod mismatch that ensure_default_access_type
    guards against for everything else.
    """
    if embargo_active:
        ev["accessType"] = {"uri": EMBARGOED_ACCESS_TYPE_URI}
        ev["embargoPeriod"] = embargo_period
    else:
        ev["accessType"] = {"uri": "/dk/atira/pure/core/openaccesspermission/open"}
        ev.pop("embargoPeriod", None)
    ev["licenseType"] = {"uri": "/dk/atira/pure/core/document/licenses/cc_by"}
    ev["versionType"] = {
        "uri": "/dk/atira/pure/researchoutput/electronicversion/versiontype/authorsversion"
    }
    return ev


def record_has_dspace_link(pure_record, dspace_row):
    """
    True only if this Pure record is actually connected to DSpace -- either it
    already carries a "DSpace" identifier, or this dspace_row supplies a uuid
    that will be attached to it this run. Guards against ever touching file
    access/licence/version metadata on a Pure record that merely matched by
    title or DOI but was never sourced from DSpace.
    """
    existing_identifiers = pure_record.get("identifiers", []) or []
    if any(i.get("idSource") == "DSpace" for i in existing_identifiers):
        return True
    return bool(dspace_row.get("uuid", "").strip())


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

    if not embargo_active and embargo_desc:
        year, month, day = parse_date(embargo_desc)
        candidate = f"{year:04d}-{month:02d}-{day:02d}"
        if candidate > TODAY:
            embargo_date_iso = candidate
            embargo_active   = True


    access_uri = (
        "/dk/atira/pure/core/openaccesspermission/embargoed"
        if embargo_active else
        "/dk/atira/pure/core/openaccesspermission/open"
    )
    embargo_period = {"endDate": embargo_date_iso} if embargo_active else None

    return embargo_date_iso, embargo_active, access_uri, embargo_period



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


def remove_orphan_organizations(organizations, attached_uuids):
    """
    Split a record-level organisation list into (kept, removed_uuids).

    An organisation is kept only if its UUID is attached to at least one
    contributor (attached_uuids). Entries without a UUID are left untouched.
    Order is kept.
    """
    kept, removed = [], []
    for org in organizations or []:
        uuid = org.get("uuid") if isinstance(org, dict) else None
        if not uuid or uuid in attached_uuids:
            kept.append(org)
        else:
            removed.append(uuid)
    return kept, removed


def resolve_managing_organization(first_internal_org_uuid):
    """
    Return the UUID to use as managingOrganization.
    If the first internal author's org is a Central University org,
    fall back to the Library Repository instead.
    """
    if not first_internal_org_uuid:
        return LIBRARY_REPOSITORY_UUID
    if first_internal_org_uuid in CENTRAL_UNIVERSITY_ORGS:
        print(f"  ℹ️ Primary org {first_internal_org_uuid} is Central University — using Library Repository instead")
        return LIBRARY_REPOSITORY_UUID
    return first_internal_org_uuid


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


def parse_subjects(subject_str):
    """Parse dc.subject field: semicolon-separated keywords."""
    if not subject_str:
        return []
    return [s.strip() for s in subject_str.split(";") if s.strip()]


def merge_keywords(existing_keywords, new_keywords):
    """
    Merge two lists of keyword strings into one, alphabetically sorted list
    with no duplicates. Comparison for duplicates is case-insensitive, but
    the original capitalisation of the first occurrence encountered is kept
    (existing keywords take precedence over new ones with the same value).
    """
    seen = {}
    for kw in existing_keywords + new_keywords:
        key = kw.lower()
        if key not in seen:
            seen[key] = kw
    return sorted(seen.values(), key=lambda k: k.lower())


def build_free_keywords_group(keywords):
    """
    Build a FreeKeywordsKeywordGroup dict (dc.subject -> Pure keywordGroups),
    in the shape Pure itself uses for a user-entered free-keywords group: the
    same en_IE keyword list in "keywords" and in a single ACCEPTED /
    USER_SUPPLIED keyword container (as on valid records exported from Pure;
    a group with only "keywords" is not picked up by Pure).
    """
    return {
        "typeDiscriminator": "FreeKeywordsKeywordGroup",
        "logicalName": "keywordContainers",
        "name": {
            "en_IE": "Keywords"
        },
        "keywords": [
            {
                "locale": "en_IE",
                "freeKeywords": list(keywords)
            }
        ],
        "keywordContainers": [
            {
                "state": "ACCEPTED",
                "origin": "USER_SUPPLIED",
                "freeKeywords": [
                    {
                        "locale": "en_IE",
                        "freeKeywords": list(keywords)
                    }
                ]
            }
        ]
    }


def existing_free_keywords_in_group(group):
    """
    Every keyword string in a free-keywords group, from both places Pure
    stores them: "keywords" (locale entries) and "keywordContainers"
    (containers -> locale entries). Duplicates are removed by merge_keywords.
    """
    locale_entries = list(group.get("keywords") or [])
    for container in group.get("keywordContainers") or []:
        if isinstance(container, dict):
            locale_entries.extend(container.get("freeKeywords") or [])
    found = []
    for locale_entry in locale_entries:
        if not isinstance(locale_entry, dict):
            continue
        found.extend(
            kw for kw in (locale_entry.get("freeKeywords") or [])
            if isinstance(kw, str) and kw.strip()
        )
    return found


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
            normalized = normalize_for_comparison(org_name)
            if normalized not in org_index:
                org_index[normalized] = []
            org_index[normalized].append(org)
    
    return org_index


def find_funder_match(funder_name, org_index):
    """Find matching organization using pre-built index"""
    normalized_name = normalize_for_comparison(funder_name)
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


def build_publisher_name_index(publisher_mapping):
    """Build index for O(1) publisher name lookup"""
    pub_index = {}
    for pub in publisher_mapping:
        name = pub.get("name", "")
        if name:
            normalized = normalize_for_comparison(name)
            if normalized not in pub_index:
                pub_index[normalized] = []
            pub_index[normalized].append(pub)
    return pub_index


def build_journal_uuid_index(journal_mapping):
    """
    Build a set of valid Pure journal UUIDs for O(1) membership checks.
    Used to validate a DSpace-supplied journal_uuid before trusting it --
    a UUID that isn't actually one of Pure's existing journals (stale,
    typo'd, or from a since-merged/deleted journal) would otherwise be
    submitted as-is and rejected by Pure with a "Referenced content ...
    not found" error, the same class of failure as a dangling ExternalPerson
    reference.
    """
    return {j.get("uuid") for j in journal_mapping if j.get("uuid")}


# An ISSN: 4 digits, optional hyphen, 3 digits + check character (digit or X),
# not part of a longer run of digits.
_ISSN_PATTERN = re.compile(r"(?<![0-9Xx])(\d{4})-?(\d{3}[\dXx])(?![0-9Xx])")


def _issn_check_digit_ok(eight_chars):
    """ISSN mod-11 check digit. Filters out ISSN-shaped noise such as page ranges ("1690-1697")."""
    digits = eight_chars.upper()
    total = sum(int(c) * w for c, w in zip(digits[:7], range(8, 1, -1)))
    check = (11 - total % 11) % 11
    return digits[7] == ("X" if check == 10 else str(check))


def extract_issns(value, require_valid_check_digit=True):
    """
    Return the unique ISSNs in a free-text field, normalised to "NNNN-NNNC"
    (upper-case X), in their original order. Handles "a,b", "a ; b",
    "a; b" and ISSNs written without a hyphen.
    """
    result = []
    for first, second in _ISSN_PATTERN.findall(value or ""):
        if require_valid_check_digit and not _issn_check_digit_ok(first + second):
            continue
        issn = f"{first}-{second.upper()}"
        if issn not in result:
            result.append(issn)
    return result


def normalize_journal_title(title):
    """
    Comparison key for a journal title: Unicode-normalised, lowercased, "&"
    counted as "and", every punctuation character replaced by a space,
    whitespace collapsed. Accents are kept as they are.
    E.g. "Stem Cell Research & Therapy" -> "stem cell research and therapy".
    """
    if not isinstance(title, str):
        return ""
    title = unicodedata.normalize("NFKC", title).lower().replace("&", " and ")
    title = "".join(" " if unicodedata.category(ch).startswith("P") else ch for ch in title)
    return " ".join(title.split())


# Small words ignored when comparing an abbreviated title with a full one.
_TITLE_STOPWORDS = {"of", "and", "the", "a", "an", "for", "in", "on", "de", "la", "le", "du"}


def _is_abbreviation_of(title_a, title_b):
    """
    True if one title is an abbreviation of the other, word by word
    ("J. Civ. Struct. Health Monit." / "Journal of Civil Structural Health
    Monitoring"): at least one title contains a ".", both have the same number
    of words once small words are dropped, and each word is the start of the
    corresponding word in the other title.
    """
    if "." not in title_a and "." not in title_b:
        return False
    words_a = [w for w in normalize_journal_title(title_a).split() if w not in _TITLE_STOPWORDS]
    words_b = [w for w in normalize_journal_title(title_b).split() if w not in _TITLE_STOPWORDS]
    return (
        len(words_a) == len(words_b) > 0
        and all(a.startswith(b) or b.startswith(a) for a, b in zip(words_a, words_b))
    )


def are_title_spelling_variants(title_a, title_b):
    """
    True if two journal titles are spelling variants of one title: after
    lowercasing, "&" -> "and", dropping "(...)" qualifiers such as
    "(Switzerland)" and removing punctuation and spaces, they contain the same
    numbers and are at least 95% similar -- or one is a word-by-word
    abbreviation of the other. Numbers must match, so "Ethnomusicology
    Ireland 9" and "... 10" are NOT variants.
    """
    key_a = normalize_journal_title(re.sub(r"\([^)]*\)", " ", title_a or "")).replace(" ", "")
    key_b = normalize_journal_title(re.sub(r"\([^)]*\)", " ", title_b or "")).replace(" ", "")
    if not key_a or not key_b:
        return False
    if re.findall(r"\d+", key_a) != re.findall(r"\d+", key_b):
        return False
    return fuzz.ratio(key_a, key_b) >= 95 or _is_abbreviation_of(title_a, title_b)


def _pure_journal_titles(journal):
    """
    All title strings of a Pure journal, from "titles" and
    "additionalSearchableTitles". Accepted entry shapes: "text",
    {"title": "text"}, {"title": {"value": "text"}},
    {"title": {"<locale>": "text", ...}} and {"value": "text"}.
    Anything else is ignored.
    """
    titles = []
    for field in ("titles", "additionalSearchableTitles"):
        for entry in journal.get(field) or []:
            if isinstance(entry, str):
                candidates = [entry]
            elif isinstance(entry, dict):
                value = entry.get("title", entry.get("value"))
                if isinstance(value, str):
                    candidates = [value]
                elif isinstance(value, dict):
                    if isinstance(value.get("value"), str):
                        candidates = [value["value"]]
                    else:
                        candidates = [v for v in value.values() if isinstance(v, str)]
                else:
                    candidates = []
            else:
                candidates = []
            for title in candidates:
                if title.strip() and title not in titles:
                    titles.append(title)
    return titles


def _pure_journal_issns(journal):
    """All ISSNs of a Pure journal, from both "issns" and "additionalSearchableIssns"."""
    issns = []
    for field in ("issns", "additionalSearchableIssns"):
        for entry in journal.get(field) or []:
            value = entry.get("issn") if isinstance(entry, dict) else entry
            if isinstance(value, dict):
                value = value.get("value")
            for issn in extract_issns(value if isinstance(value, str) else "", require_valid_check_digit=False):
                if issn not in issns:
                    issns.append(issn)
    return issns


def _journal_metadata_richness(journal):
    """Number of non-empty top-level fields, excluding system fields (SYSTEM_FIELDS)."""
    return sum(
        1 for key, value in journal.items()
        if key not in SYSTEM_FIELDS and value not in (None, "", [], {})
    )


def build_journal_lookup(journal_mapping):
    """
    Build ISSN and title lookups from the Pure journals JSON.

    Returns a dict:
      "by_issn":   ISSN (NNNN-NNNC) -> [journal UUIDs]  (issns + additionalSearchableIssns)
      "by_title":  normalised title  -> [journal UUIDs]  (titles + additionalSearchableTitles)
      "titles":    journal UUID -> its title strings
      "richness":  journal UUID -> number of non-empty non-system fields
      "order":     journal UUID -> position in the JSON (final tie-break)
    """
    lookup = {"by_issn": defaultdict(list), "by_title": defaultdict(list), "titles": {}, "richness": {}, "order": {}}
    for position, journal in enumerate(journal_mapping or []):
        if not isinstance(journal, dict):
            continue
        uuid = journal.get("uuid")
        if not uuid or uuid in lookup["order"]:
            continue
        lookup["order"][uuid] = position
        lookup["richness"][uuid] = _journal_metadata_richness(journal)
        for issn in _pure_journal_issns(journal):
            lookup["by_issn"][issn].append(uuid)
        lookup["titles"][uuid] = _pure_journal_titles(journal)
        for title in lookup["titles"][uuid]:
            key = normalize_journal_title(title)
            if key and uuid not in lookup["by_title"][key]:
                lookup["by_title"][key].append(uuid)
    return lookup


def _pick_richest_journal(candidates, journal_lookup):
    """Journal with the most filled metadata fields (system fields excluded); ties -> first in the JSON."""
    return sorted(
        candidates,
        key=lambda u: (-journal_lookup["richness"].get(u, 0), journal_lookup["order"].get(u, 0)),
    )[0]


def _group_journals_by_title_variant(candidates, journal_lookup):
    """
    Group journals whose titles are spelling variants of each other (any
    title of one is a variant of any title of the other; grouping is
    transitive). One group = all candidates are the same journal.
    """
    groups = []
    for uuid in candidates:
        own_titles = journal_lookup["titles"].get(uuid, [])
        linked = [
            group for group in groups
            if any(
                are_title_spelling_variants(a, b)
                for other in group for a in journal_lookup["titles"].get(other, []) for b in own_titles
            )
        ]
        merged = [uuid] + [u for group in linked for u in group]
        groups = [group for group in groups if group not in linked] + [merged]
    return groups


def resolve_journal_uuid(dspace_row, type_disc, valid_journal_uuids=None, journal_lookup=None, diagnostics=None):
    """
    Find the Pure journal for a DSpace row. Returns (journal_uuid, source, candidates):
    source is "journal_uuid", "ISSN", "title" or None; candidates lists every
    journal that qualified when there was more than one (else []).

    1. journal_uuid column -- used if (when the journals JSON is loaded) it is
       one of Pure's journals.
    2. ISSNs from journal_issn and dc.identifier.issn (both multi-valued;
       dc.identifier.issn values must pass the ISSN check digit) against every
       ISSN of every Pure journal (issns + additionalSearchableIssns). All
       matching journals must be the same journal (titles are spelling
       variants of each other -- duplicate records); the one with most
       metadata is used. If they have genuinely different titles, the ISSN
       result is not used and step 3 decides.
    3. Titles from dc.identifier.journal and journal_title against every Pure
       journal title (titles + additionalSearchableTitles; lowercased,
       punctuation stripped, "&" = "and"). If several journals match, the one
       with most metadata wins.
    4. Nothing found -> ("", None, []); the caller decides what to do.

    If a dict is passed as diagnostics, diagnostics["reason"] explains why
    nothing was found.
    """
    journal_uuid = (dspace_row.get("journal_uuid") or "").strip()

    # A UUID that isn't one of Pure's actual journals is treated the same as
    # no UUID at all, rather than being submitted and rejected by Pure.
    if journal_uuid and valid_journal_uuids is not None and journal_uuid not in valid_journal_uuids:
        print(f"    ⚠️ journal_uuid {journal_uuid} not found in JOURNAL_MAPPING_JSON for {type_disc} - treating as missing")
        journal_uuid = ""

    if journal_uuid:
        return journal_uuid, "journal_uuid", []

    if not journal_lookup:
        return "", None, []

    def usable(uuid):
        return valid_journal_uuids is None or uuid in valid_journal_uuids

    # 2. ISSN
    issns = extract_issns(dspace_row.get("journal_issn", ""), require_valid_check_digit=False)
    for issn in extract_issns(dspace_row.get("dc.identifier.issn", "")):
        if issn not in issns:
            issns.append(issn)
    issn_candidates = []
    for issn in issns:
        for uuid in journal_lookup["by_issn"].get(issn, []):
            if usable(uuid) and uuid not in issn_candidates:
                issn_candidates.append(uuid)
    if not issns:
        issn_status = "no valid ISSN in DSpace"
    elif not issn_candidates:
        issn_status = "ISSN not found in Pure"
    else:
        groups = _group_journals_by_title_variant(issn_candidates, journal_lookup)
        if len(groups) == 1:
            chosen = _pick_richest_journal(issn_candidates, journal_lookup)
            print(f"    ✅ Journal found by ISSN {issns}: {chosen}"
                  + (f" (chosen from {len(issn_candidates)} records of the same journal)" if len(issn_candidates) > 1 else ""))
            return chosen, "ISSN", (issn_candidates if len(issn_candidates) > 1 else [])
        issn_status = "ISSN matches journals with different titles"
        print(f"    ⚠️ ISSNs {issns} match {len(groups)} journals with different titles "
              f"{[journal_lookup['titles'].get(g[0], [''])[0] for g in groups]} - trying title matching")

    # 3. Title
    titles = []
    for field in ("dc.identifier.journal", "journal_title"):
        for piece in _MULTI_VALUE_SEPARATOR.split(dspace_row.get(field, "") or ""):
            key = normalize_journal_title(piece)
            if key and key not in titles:
                titles.append(key)
    candidates = []
    for key in titles:
        for uuid in journal_lookup["by_title"].get(key, []):
            if usable(uuid) and uuid not in candidates:
                candidates.append(uuid)
    if candidates:
        chosen = _pick_richest_journal(candidates, journal_lookup)
        print(f"    ✅ Journal found by title {titles}: {chosen}"
              + (f" (chosen from {len(candidates)} candidates)" if len(candidates) > 1 else ""))
        return chosen, "title", (candidates if len(candidates) > 1 else [])

    if diagnostics is not None:
        title_status = "no journal title in DSpace" if not titles else "title not found in Pure"
        diagnostics["reason"] = f"{issn_status}; {title_status}"
    return "", None, []


def _unmatched_journal_entry(dspace_row, pure_uuid, reason):
    """Row for unmatched_journals_<date>.csv."""
    handles = extract_handles_from_uri(dspace_row.get("dc.identifier.uri", ""))
    return {
        "handle": handles[0] if handles else None,
        "title": dspace_row.get("dc.title", ""),
        "dc.identifier.issn": dspace_row.get("dc.identifier.issn", ""),
        "journal_issn": dspace_row.get("journal_issn", ""),
        "dc.identifier.journal": dspace_row.get("dc.identifier.journal", ""),
        "journal_title": dspace_row.get("journal_title", ""),
        "reason": reason,
        "pure_uuid": pure_uuid,
    }


def find_publisher_match(publisher_name, pub_index):
    """Find matching publisher using pre-built index"""
    if not publisher_name:
        return []
    normalized = normalize_for_comparison(publisher_name)
    return pub_index.get(normalized, [])


def append_record_to_file(filepath, new_record):
    existing = []
    if os.path.exists(filepath):
        with open(filepath, "r", encoding="utf-8-sig") as f:
            try:
                existing = json.load(f)
            except json.JSONDecodeError:
                print(f"  ⚠️ Could not parse existing file {filepath} — starting fresh.")
                existing = []

    record_uuid = new_record.get("uuid")

    # For new records without a Pure UUID, use the DSpace identifier as the dedup key
    if record_uuid:
        dedup_key = record_uuid
    else:
        dspace_id = next(
            (i.get("value") for i in new_record.get("identifiers", [])
             if i.get("idSource") == "DSpace"),
            None
        )
        handle = next(
            (l.get("url") for l in new_record.get("links", [])
             if "hdl.handle.net" in l.get("url", "")),
            None
        )
        dedup_key = dspace_id or handle or id(new_record)  # id() as last resort

    records_by_uuid = {
        (r.get("uuid") or next(
            (i.get("value") for i in r.get("identifiers", []) if i.get("idSource") == "DSpace"), None
        ) or next(
            (l.get("url") for l in r.get("links", []) if "hdl.handle.net" in l.get("url", "")), None
        )): r
        for r in existing
    }

    if dedup_key in records_by_uuid:
        print(f"  ℹ️ Record {dedup_key} already in {os.path.basename(filepath)} — replacing.")

    records_by_uuid[dedup_key] = new_record

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(list(records_by_uuid.values()), f, indent=2, ensure_ascii=False)


# --- UPDATING RECORDS ---

def update_record_from_dspace(pure_record, dspace_row, person_index, org_index, log_entry, before_update_records, pub_index=None, journal_index=None, override_mode=False, journal_lookup=None):
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
    # Precedence mode: DSpace contributors reuse the Pure contributors already
    # linked to them, and all other Pure contributors are kept (nothing in Pure
    # is discarded). Override mode: pass an empty list so only DSpace
    # contributors are used.
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
        managing_org_uuid = resolve_managing_organization(first_internal_org_uuid)
        updated_record["managingOrganization"] = {
            "uuid": managing_org_uuid,
            "systemName": "Organization"
        }
        print(f"  ✅ Override: Set managingOrganization to: {managing_org_uuid}")
    elif not pure_record.get("managingOrganization", {}).get("uuid"):
        managing_org_uuid = resolve_managing_organization(first_internal_org_uuid)
        updated_record["managingOrganization"] = {
            "uuid": managing_org_uuid,
            "systemName": "Organization"
        }
        print(f"  ✅ Precedence: Set managingOrganization to: {managing_org_uuid}")

    # --- 1b2. Remove orphan record-level organisations (internal and external) ---
    # A record-level organisation is an orphan if no contributor on the record
    # -- as it will be after this update -- has it attached. Internal
    # organisations ("organizations") are checked against contributors'
    # "organizations"; external ones ("externalOrganizations") against
    # contributors' "externalOrganizations". managingOrganization is neither
    # read nor changed here; step 1b3 below adds it to "organizations".
    # Applies in both normal and override mode.
    if "contributors" in updated_record:
        effective_contributors = updated_record["contributors"]
    else:
        effective_contributors = [c for c in (pure_record.get("contributors") or []) if c]

    attached_internal_uuids = set()
    attached_external_uuids = set()
    for contributor in effective_contributors:
        if not isinstance(contributor, dict):
            continue
        for org in contributor.get("organizations") or []:
            if isinstance(org, dict) and org.get("uuid"):
                attached_internal_uuids.add(org["uuid"])
        for org in contributor.get("externalOrganizations") or []:
            if isinstance(org, dict) and org.get("uuid"):
                attached_external_uuids.add(org["uuid"])

    for field, attached_uuids, label in (
        ("organizations", attached_internal_uuids, "organisation"),
        ("externalOrganizations", attached_external_uuids, "external organisation"),
    ):
        current = updated_record[field] if field in updated_record else (pure_record.get(field) or [])
        kept, removed = remove_orphan_organizations(current, attached_uuids)
        if removed:
            updated_record[field] = kept
            log_entry.setdefault("removedOrphanOrganizations", {})[field] = removed
            print(f"  🧹 Removed {len(removed)} orphan record-level {label}(s) not attached to any contributor: {removed}")

    # --- 1b3. Managing organisation in the record-level organisations ---
    # "organizations" can't be empty: it always includes the managing
    # organisation. A record that has only external contributors (after this
    # update) is managed by the Library Repository.
    has_internal_contributor = any(
        isinstance(c, dict) and c.get("typeDiscriminator") == "InternalContributorAssociation"
        for c in effective_contributors
    )
    managing_uuid = (updated_record.get("managingOrganization") or pure_record.get("managingOrganization") or {}).get("uuid")
    if effective_contributors and not has_internal_contributor and managing_uuid != LIBRARY_REPOSITORY_UUID:
        print(f"  ✅ Only external contributors: managingOrganization {managing_uuid} → Library Repository {LIBRARY_REPOSITORY_UUID}")
        updated_record["managingOrganization"] = {
            "uuid": LIBRARY_REPOSITORY_UUID,
            "systemName": "Organization"
        }
        managing_uuid = LIBRARY_REPOSITORY_UUID
    if managing_uuid:
        current_orgs = updated_record["organizations"] if "organizations" in updated_record else (pure_record.get("organizations") or [])
        if managing_uuid not in {o.get("uuid") for o in current_orgs if isinstance(o, dict)}:
            updated_record["organizations"] = list(current_orgs) + [{"systemName": "Organization", "uuid": managing_uuid}]
            print(f"  ✅ Added managing organisation {managing_uuid} to record-level organizations")

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
    existing_file_evs = []
    existing_other_evs = []
    
    for ev in existing_evs:
        # Work on a shallow COPY, never the original dict. repo_ev / file_ev
        # below get mutated in place -- mutating the original would also
        # mutate existing_evs (same object reference), which would make the
        # "final_evs != existing_evs" check at the end always see no change,
        # silently dropping electronicVersions from the update payload.
        ev = dict(ev)
        # FileElectronicVersion has no "doi" field -- pull it out FIRST so it
        # never falls into "other" below. It's the only EV type that should
        # carry accessType / licenseType / versionType (step 5f).
        if ev.get("typeDiscriminator") == "FileElectronicVersion":
            existing_file_evs.append(ev)
            continue
        doi = normalize_doi(ev.get("doi") or "")
        if not is_handle_url(doi):
            if doi.startswith("https://doi.org/10.13025"):
                existing_repo_evs.append(ev)
            elif doi and doi.startswith("https://doi.org/"):
                existing_publisher_evs.append(ev)
            else:
                existing_other_evs.append(ev)
    
    # Pure may already hold the same DOI more than once (e.g. with different
    # case or URL prefix) -- keep a single electronic version per DOI.
    existing_publisher_evs = dedupe_by_key(
        existing_publisher_evs, lambda e: normalize_doi(e.get("doi") or ""), "publisher DOI electronic version"
    )
    existing_other_evs = dedupe_by_key(
        existing_other_evs, lambda e: (e.get("doi") or "").strip().lower(), "DOI electronic version"
    )

    existing_publisher_dois = [normalize_doi(ev.get("doi", "")) for ev in existing_publisher_evs]

    # --- 5a. Embargo (dc.date.embargo / dc.description.embargo) > overwrite for repo version ---
    embargo_date, embargo_active, _, embargo_period = resolve_embargo_and_access(dspace_row)

    # --- 5b. Publisher DOI (dc.identifier.doi) > add if blank ---
  
    publisher_doi_raw = dspace_row.get("dc.identifier.doi", "").strip()
    new_publisher_evs = []
    if publisher_doi_raw:
        dspace_dois, unrecognised = parse_doi_field(publisher_doi_raw)
        for piece in unrecognised:
            print(f"  ⚠️ dc.identifier.doi value is not a DOI — not added as an electronic version: '{piece}'")
        for publisher_doi in dspace_dois:
            # A repository DOI (10.13025) in this field is handled in step 5c,
            # together with those in dc.identifier.uri -- every other DOI here
            # is a publisher DOI.
            if "10.13025" in publisher_doi:
                continue
            if publisher_doi not in existing_publisher_dois:
                ev = build_electronic_version(doi=publisher_doi)
                if ev:
                    new_publisher_evs.append(ev)
                    existing_publisher_dois.append(publisher_doi)

    # --- 5c. Repository DOI (10.13025, from dc.identifier.uri or dc.identifier.doi) ---
    # Exactly one, chosen by choose_repository_doi_ev (shared with step 10b,
    # so both always agree), with the repository metadata applied.
    repo_ev = choose_repository_doi_ev(existing_evs, dspace_row, dspace_row.get("uuid", ""), pure_record.get("uuid"))

    # --- 5e. Build final electronic versions list: repository DOI first, then publisher DOIs, then others, then files ---
    final_evs = []
    if repo_ev:
        final_evs.append(repo_ev)

    # Add publisher DOIs second. Rule 2: not repository-sourced, so licence,
    # version type, and embargo are left exactly as Pure already has them --
    # only accessType is touched, and only to default to "Unknown" if Pure
    # doesn't already have a value.
    for ev in existing_publisher_evs:
        if "doi" in ev and isinstance(ev["doi"], str):
            ev["doi"] = normalize_doi(ev["doi"])
        ensure_default_access_type(ev)
        final_evs.append(ev)

    for new_publisher_ev in new_publisher_evs:
        if "doi" in new_publisher_ev and isinstance(new_publisher_ev["doi"], str):
            new_publisher_ev["doi"] = normalize_doi(new_publisher_ev["doi"])
        ensure_default_access_type(new_publisher_ev)
        final_evs.append(new_publisher_ev)

    # Add other electronic versions next -- same rule 2 treatment as publisher DOIs.
    for ev in existing_other_evs:
        if "doi" in ev and isinstance(ev["doi"], str):
            ev["doi"] = normalize_doi(ev["doi"])
        ensure_default_access_type(ev)
        final_evs.append(ev)

    # --- 5f. File Electronic Version (access + licence + manuscript type) ---
    # DSpace-linked record: the file is repository-sourced, so it gets the
    # same treatment as the repository DOI above (rule 1) -- access is
    # embargo-aware, licence and version type are always set. Not
    # DSpace-linked: rule 3 -- only ensure accessType is present (Pure's
    # existing value always wins, defaulted to "Unknown" only if missing);
    # licence/version-type/embargo are left exactly as Pure already has them.
    if record_has_dspace_link(pure_record, dspace_row):
        for file_ev in existing_file_evs:
            apply_repository_access_license_version(file_ev, embargo_active, embargo_period)
    else:
        for file_ev in existing_file_evs:
            ensure_default_access_type(file_ev)
    final_evs.extend(existing_file_evs)

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
        elif is_handle_url(url):
            existing_handle_links.append(link)
        else:
            non_handle_non_doi_links.append(link)

    # The same handle may already be in Pure more than once (e.g. http and
    # https, or different case) -- keep one link per handle, preferring the
    # copy with the most metadata (alias/description).
    existing_handle_links = dedupe_by_key(
        existing_handle_links,
        lambda l: str(normalize_handle(l.get("url", "") or "")).strip().lower(),
        "handle link",
    )

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
    abstract = sanitize_abstract(dspace_row.get("dc.description.abstract", "").strip())
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
        existing_journal = pure_record.get("journalAssociation", {}).get("journal", {}).get("uuid")

        # Journal: journal_uuid -> ISSN -> title (see resolve_journal_uuid).
        # Only looked up when Pure has no journal, since an existing one is kept.
        journal_uuid, journal_source, journal_candidates = ("", None, [])
        journal_diagnostics = {}
        if not existing_journal:
            journal_uuid, journal_source, journal_candidates = resolve_journal_uuid(
                dspace_row, type_disc, journal_index, journal_lookup, journal_diagnostics
            )

        # Add journal if we have a UUID and (no existing journal)
        if journal_uuid and (not existing_journal):
            if journal_source in ("ISSN", "title"):
                log_entry["journalMatchedBy"] = journal_source
                if journal_candidates:
                    log_entry["journalCandidates"] = journal_candidates
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
            _unmatched_journals.append(_unmatched_journal_entry(
                dspace_row, pure_record.get("uuid"), journal_diagnostics.get("reason", "journal not found")
            ))

    
    # --- 10. Identifiers — set DSpace UUID as PrimaryId ---
    dspace_uuid = dspace_row.get("uuid", "").strip()
    if dspace_uuid:
        existing_identifiers = pure_record.get("identifiers", [])
        updated_record["identifiers"] = merge_identifiers(existing_identifiers, dspace_uuid)
    else:
        print(f"  ⚠️ No DSpace UUID found for record: {dspace_row.get('dc.title', '')[:80]}")
    
    # --- 10b. One DSpace identity: one DSpace UUID, Handle and repository DOI ---
    enforce_single_dspace_identity(pure_record, dspace_row, updated_record, log_entry)

    # --- 11. Publisher (dc.publisher) > inject for BookAnthology / ContributionToBookAnthology ---
    PUBLISHER_TYPES = {"BookAnthology", "ContributionToBookAnthology", "OtherContribution", "WorkingPaper", "NonTextual"}
    if pub_index and pure_type in PUBLISHER_TYPES:
        dspace_publisher = dspace_row.get("dc.publisher", "").strip()
        existing_publisher_uuid = pure_record.get("publisher", {}).get("uuid") if pure_record.get("publisher") else None
        if dspace_publisher and (not existing_publisher_uuid or override_mode):
            matches = find_publisher_match(dspace_publisher, pub_index)
            if matches:
                matched_pub = matches[0]  # take first — names are unique enough
                updated_record["publisher"] = {
                    "uuid": matched_pub["uuid"],
                    "systemName": "Publisher"
                }
                print(f"  ✅ Set publisher: '{dspace_publisher}' → {matched_pub['uuid']}")
            else:
                print(f"  ⚠️ No publisher match found for: '{dspace_publisher}'")
                _unmatched_publishers.append({
                    "name": dspace_publisher,
                    "handle": extract_handles_from_uri(dspace_row.get("dc.identifier.uri", ""))[0] if extract_handles_from_uri(dspace_row.get("dc.identifier.uri", "")) else None,
                    "title": dspace_row.get("dc.title", ""),
                    "pure_uuid": pure_record.get("uuid")
                })

    # --- 11a. Keywords (dc.subject) > add new keywords, don't overwrite existing ---
    dspace_subjects = parse_subjects(dspace_row.get("dc.subject", ""))
    if dspace_subjects:
        # Base off whatever keywordGroups is currently staged for the update (e.g. after
        # step 1c removed the authors group); fall back to the original Pure record.
        base_keyword_groups = updated_record.get("keywordGroups", pure_record.get("keywordGroups", []))

        existing_free_keywords = []
        other_keyword_groups = []
        for kg in base_keyword_groups:
            if not kg:
                continue
            # Match ONLY the general free-keywords group (typeDiscriminator +
            # logicalName both required). This intentionally excludes any other
            # keyword group, including Pure's own "authors" free-keywords group
            # (logicalName "/dk/atira/pure/authors") and any classification/
            # discipline keyword groups — those are passed through untouched.
            if kg.get("typeDiscriminator") == "FreeKeywordsKeywordGroup" and kg.get("logicalName") == "keywordContainers":
                # Existing keywords from both "keywords" and "keywordContainers".
                existing_free_keywords.extend(existing_free_keywords_in_group(kg))
            else:
                other_keyword_groups.append(kg)

        merged_keywords = merge_keywords(existing_free_keywords, dspace_subjects)
        updated_record["keywordGroups"] = other_keyword_groups + [build_free_keywords_group(merged_keywords)]
        print(f"  ✅ Keywords: added {len(dspace_subjects)} DSpace subject(s), {len(merged_keywords)} total after merge")

    # --- 12. Set workflow step ---
    updated_record["workflow"] = {
        "step": "validated"
    }

    # Write log entry
    log_entry["success"] = success and not errors
    if errors:
        log_entry["error"] = "; ".join(errors)

    before_update_records.append(strip_system_fields(pure_record))

    # pureId is a system field: only the record-level pureId is supplied, to
    # identify the record (next to "uuid"). Every nested pureId -- carried over
    # from Pure in contributors, electronic versions and their files,
    # identifiers, keyword groups, ... -- is removed.
    record_pure_id = pure_record.get("pureId")
    finalised = {"uuid": updated_record.get("uuid")}
    if record_pure_id is not None:
        finalised["pureId"] = record_pure_id
    finalised.update(strip_nested_pure_ids(
        {k: v for k, v in updated_record.items() if k not in ("uuid", "pureId")}
    ))
    updated_record = finalised

    return updated_record, success


# --- CREATING RECORDS ---

def create_new_record_from_dspace(dspace_row, person_index, org_index, pub_index=None, journal_index=None, journal_lookup=None, log_entry=None):
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
            "uuid":  LIBRARY_REPOSITORY_UUID,
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
    requested_type_disc = record["typeDiscriminator"]
    journal_diagnostics = {}
    record = add_type_specific_fields(record, dspace_row, journal_index, journal_lookup, log_entry, journal_diagnostics)
    journal_downgraded = (
        requested_type_disc in ("ContributionToJournal", "ContributionToPeriodical")
        and record["typeDiscriminator"] == "OtherContribution"
    )

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
    abstract = sanitize_abstract(dspace_row.get("dc.description.abstract", "").strip())
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

    if journal_downgraded:
        _unmatched_journals.append(_unmatched_journal_entry(
            dspace_row, None, journal_diagnostics.get("reason", "journal not found")
        ))

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

    managing_org_uuid = resolve_managing_organization(first_internal_org_uuid)
    record["managingOrganization"] = {
        "uuid": managing_org_uuid,
        "systemName": "Organization"
    }
    print(f"✅ Set managingOrganization to: {managing_org_uuid}")

    # The record-level "organizations" list always includes the managing
    # organisation, so it is never empty. For a record with only external
    # contributors the managing organisation is the Library Repository
    # (see resolve_managing_organization).
    record_orgs = record.setdefault("organizations", [])
    if managing_org_uuid not in {o.get("uuid") for o in record_orgs if isinstance(o, dict)}:
        record_orgs.append({"systemName": "Organization", "uuid": managing_org_uuid})
    
    # Set DOIs and Handles - Repository DOI first, then Publisher DOI
    electronic_versions = []
    
    embargo_date, embargo_active, _, embargo_period = resolve_embargo_and_access(dspace_row)

    uri_str = dspace_row.get("dc.identifier.uri", "").strip()
    if uri_str:
        dois = extract_dois_from_uri(uri_str)
        
        # Add repository DOI first. Rule 1: this DOI only ever exists because
        # the institutional repository minted it for a DSpace item, so it's
        # always repository-sourced -- access (embargo-aware), licence, and
        # version type are always set, the same treatment a DSpace-linked
        # FileElectronicVersion gets in update_record_from_dspace. A brand-new
        # record has no deposited file yet, so there's no FileElectronicVersion
        # to handle here.
        for doi in dois:
            doi = normalize_doi(doi)
            if doi.startswith("https://doi.org/10.13025"):
                ev = build_electronic_version(doi=doi)
                if ev:
                    electronic_versions.append(
                        apply_repository_access_license_version(ev, embargo_active, embargo_period)
                    )
                    break  # only one repo DOI expected
    
    # Second, add publisher DOI(s) -- each distinct DOI only once
    publisher_doi_raw = dspace_row.get("dc.identifier.doi", "").strip()
    if publisher_doi_raw:
        dspace_dois, unrecognised = parse_doi_field(publisher_doi_raw)
        for piece in unrecognised:
            print(f"  ⚠️ dc.identifier.doi value is not a DOI — not added as an electronic version: '{piece}'")
        for publisher_doi in dspace_dois:
            if "10.13025" in publisher_doi:
                # Treat as repo DOI if not already added
                already_added = any("10.13025" in ev.get("doi", "") for ev in electronic_versions)
                if not already_added:
                    ev = build_electronic_version(doi=publisher_doi)
                    if ev:
                        electronic_versions.insert(
                            0, apply_repository_access_license_version(ev, embargo_active, embargo_period)
                        )
            else:
                # Rule 2: not repository-sourced. Since this is a brand new
                # record there's no pre-existing Pure metadata to preserve, so
                # accessType defaults straight to "Unknown"; licence/version
                # type are left unset (build_electronic_version never adds them,
                # and there's nothing to strip on a freshly built dict).
                if any(ev.get("doi") == publisher_doi for ev in electronic_versions):
                    continue
                ev = build_electronic_version(publisher_doi)
                if ev:
                    electronic_versions.append(ensure_default_access_type(ev))
    
    # Set electronic versions on record (DOIs)
    if electronic_versions:
        record["electronicVersions"] = electronic_versions

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

    
    # Publisher (dc.publisher) > inject for BookAnthology / ContributionToBookAnthology
    PUBLISHER_TYPES = {"BookAnthology", "ContributionToBookAnthology", "OtherContribution", "WorkingPaper", "NonTextual"}
    if pub_index and record.get("typeDiscriminator") in PUBLISHER_TYPES:
        dspace_publisher = dspace_row.get("dc.publisher", "").strip()
        if dspace_publisher:
            matches = find_publisher_match(dspace_publisher, pub_index)
            if matches:
                matched_pub = matches[0]
                record["publisher"] = {
                    "uuid": matched_pub["uuid"],
                    "systemName": "Publisher"
                }
                print(f"  ✅ Set publisher: '{dspace_publisher}' → {matched_pub['uuid']}")
            else:
                print(f"  ⚠️ No publisher match found for: '{dspace_publisher}'")
                _unmatched_publishers.append({
                    "name": dspace_publisher,
                    "handle": extract_handles_from_uri(dspace_row.get("dc.identifier.uri", ""))[0] if extract_handles_from_uri(dspace_row.get("dc.identifier.uri", "")) else None,
                    "title": dspace_row.get("dc.title", ""),
                    "pure_uuid": None
                })

    # Set keywords (dc.subject)
    dspace_subjects = parse_subjects(dspace_row.get("dc.subject", ""))
    if dspace_subjects:
        merged_keywords = merge_keywords([], dspace_subjects)
        record["keywordGroups"] = [build_free_keywords_group(merged_keywords)]

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
    with open(DSPACE_CSV, 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("pdf_handle_paths"):
                row["pdf_handle_paths"] = clean_dspace_filename(row["pdf_handle_paths"])
            dspace_rows.append(row)
    build_dspace_item_lookups(dspace_rows)
    print(f"✅ Loaded {len(dspace_rows)} records from {DSPACE_CSV}")

    print("Loading Pure JSON...")
    with open(PURE_JSON, 'r', encoding='utf-8-sig') as f:
        pure_items = json.load(f)
    print(f"✅ Loaded {len(pure_items)} records from {PURE_JSON}")

    print("Loading Person Mapping...")
    with open(PERSON_MAPPING_JSON, 'r', encoding='utf-8-sig') as f:
        person_mapping = json.load(f)
    print(f"✅ Loaded {len(person_mapping)} person records from {PERSON_MAPPING_JSON}")

    print("Loading Organization Mapping...")
    with open(ORGANIZATION_MAPPING_JSON, 'r', encoding='utf-8-sig') as f:
        organization_mapping = json.load(f)
    print(f"✅ Loaded {len(organization_mapping)} organization records from {ORGANIZATION_MAPPING_JSON}")

    print("Loading Publisher Mapping...")
    with open(PUBLISHER_MAPPING_JSON, 'r', encoding='utf-8-sig') as f:
        publisher_mapping = json.load(f)
    print(f"✅ Loaded {len(publisher_mapping)} publisher records from {PUBLISHER_MAPPING_JSON}")

    print("Loading Journal Mapping...")
    with open(JOURNAL_MAPPING_JSON, 'r', encoding='utf-8-sig') as f:
        journal_mapping = json.load(f)
    print(f"✅ Loaded {len(journal_mapping)} journal records from {JOURNAL_MAPPING_JSON}")

    # Build indices
    print("\n🔨 Building lookup indices...")
    person_index = build_person_name_index(person_mapping)
    print(f"✅ Built person name index with {len(person_index)} entries")
    
    org_index = build_organization_name_index(organization_mapping)
    print(f"✅ Built organization name index with {len(org_index)} entries")

    # A Pure record that already carries a DSpace UUID is matched ONLY through
    # that DSpace UUID (step 0) -- never by publisher DOI, repository DOI,
    # Handle or title. Steps 1-4 therefore only see records without a DSpace
    # UUID ("unlinked").
    pure_by_dspace_uuid = defaultdict(list)
    unlinked_pure_items = []
    for item in pure_items:
        linked_uuids = dspace_uuids_of(item)
        if linked_uuids:
            for linked_uuid in linked_uuids:
                pure_by_dspace_uuid[linked_uuid.lower()].append(item)
        else:
            unlinked_pure_items.append(item)
    print(f"✅ Pure records linked to a DSpace item: {len(pure_items) - len(unlinked_pure_items)} "
          f"(matched only by DSpace UUID) | without a DSpace UUID: {len(unlinked_pure_items)}")

    print("Building title token index...")
    title_token_index = build_title_token_index(unlinked_pure_items)
    print(f"✅ Built title token index with {len(title_token_index)} tokens")

    pub_index = build_publisher_name_index(publisher_mapping)
    print(f"✅ Built publisher name index with {len(pub_index)} entries")

    journal_index = build_journal_uuid_index(journal_mapping)
    print(f"✅ Built journal UUID index with {len(journal_index)} entries")

    journal_lookup = build_journal_lookup(journal_mapping)
    print(f"✅ Built journal lookup: {len(journal_lookup['by_issn'])} ISSNs, {len(journal_lookup['by_title'])} titles")


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

    for item in unlinked_pure_items:
        # Index by Publisher DOI (from electronic versions)
        for ev in item.get("electronicVersions", []):
            doi = ev.get("doi", "")
            if doi:
                if "10.13025" in doi:
                    pure_by_repo_doi[normalize_doi(doi)].append(item)
                # Index handles found in electronic versions (DOIs that look like handles)
                elif is_handle_url(doi):
                    pure_by_handle[normalize_handle(doi)].append(item)
                else:
                    pure_by_doi[normalize_doi(doi)].append(item)

        # Index by links (including handles from links)
        for link in item.get("links", []):
            url = link.get("url", "")
            if not url:
                continue
            if is_handle_url(url):
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

        # Filter: datasets are not uploaded from DSpace to Pure
        if (row.get("dc.type") or "").strip().lower() in EXCLUDED_DSPACE_TYPES:
            log_entry["success"] = False
            log_entry["error"] = "Skipped: dataset (not uploaded to Pure)"
            log_entry["handle"] = extract_handles_from_uri(row.get("dc.identifier.uri", ""))[0] if extract_handles_from_uri(row.get("dc.identifier.uri", "")) else None
            log_entries.append(log_entry)
            print(f"⚠️ Skipping record - dataset, not uploaded to Pure: {row.get('dc.title', '')[:50]}...")
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

        # dc.identifier.doi is a multi-entry field (" ; "-separated). Publisher
        # DOIs in it are used in step 1; any repository (10.13025) DOI in it is
        # added to the repository-DOI candidates for step 2, after the ones
        # from dc.identifier.uri.
        field_dois, _ = parse_doi_field(row.get("dc.identifier.doi", ""))
        field_publisher_dois = [d for d in field_dois if "10.13025" not in d]
        for d in field_dois:
            if "10.13025" in d and d not in repo_dois:
                repo_dois.append(d)

        matched_records = []
        match_type = None  # Track which match method was used

        # 0. DSpace UUID -- highest priority: a Pure record that already carries
        # this item's DSpace UUID is its record. When found, no other matching.
        row_dspace_uuid = (row.get("uuid") or "").strip().lower()
        if row_dspace_uuid and row_dspace_uuid in pure_by_dspace_uuid:
            matched_records.extend(pure_by_dspace_uuid[row_dspace_uuid])
            match_type = "DSpace UUID"

        # 1. Try to match by Publisher DOI -- every DOI in the field. A Pure
        # record reached through several DOIs (or indexed twice, e.g. via an
        # electronic version and a DOI link) is only counted once.
        seen_record_ids = set()
        for normalized_doi in ([] if matched_records else field_publisher_dois):
            for pure_item in pure_by_doi.get(normalized_doi, []):
                conflict = publisher_doi_conflict(row, pure_item, TITLE_SIMILARITY_THRESHOLD) if id(pure_item) not in seen_record_ids else None
                if conflict:
                    seen_record_ids.add(id(pure_item))
                    print(f"  ⚠️ Publisher DOI match with {pure_item.get('uuid')} rejected: {conflict}")
                    log_entry.setdefault("rejectedMatches", []).append({"pureUUID": pure_item.get("uuid"), "reason": conflict})
                    row_handles = extract_handles_from_uri(row.get("dc.identifier.uri", ""))
                    _publisher_doi_conflicts.append({
                        "dspace_uuid": row.get("uuid", ""),
                        "handle": row_handles[0] if row_handles else None,
                        "dspace_title": row.get("dc.title", ""),
                        "doi": normalized_doi,
                        "pure_uuid": pure_item.get("uuid"),
                        "pure_title": (pure_item.get("title") or {}).get("value", ""),
                        "pure_record_dspace_uuids": " ; ".join(dspace_uuids_of(pure_item)),
                    })
                    continue
                if id(pure_item) not in seen_record_ids:
                    seen_record_ids.add(id(pure_item))
                    matched_records.append(pure_item)
        if matched_records and match_type is None:
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

                # Strategy 4a. Exact match: try all three key variants against the index.
                # Each candidate must pass confirm_title_match (identical full title +
                # subtitle and same output type, or else same year and output type).
                exact_candidates = [dspace_title, combined_dspace_title]
                for candidate in dict.fromkeys(exact_candidates):  # deduplicate, preserve order
                    key = normalize(candidate)
                    if key in pure_by_title:
                        accepted = []
                        for pure_item in pure_by_title[key]:
                            ok, reason = confirm_title_match(row, pure_item, dspace_title, dspace_subtitle)
                            if ok:
                                if all(pure_item is not a for a in accepted):
                                    accepted.append(pure_item)
                            else:
                                print(f"  ⚠️ Title match with {pure_item.get('uuid')} rejected: {reason}")
                                log_entry.setdefault("rejectedMatches", []).append({"pureUUID": pure_item.get("uuid"), "reason": reason})
                        if accepted:
                            matched_records.extend(accepted)
                            match_type = "Title (Exact)"
                            break

                # Strategy 4b. Fuzzy title match: candidates only, not all Pure records
                if not matched_records:
                    candidates = find_fuzzy_title_candidates(
                        dspace_title, dspace_subtitle, title_token_index, unlinked_pure_items
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
                                words_ok, extra_words = title_words_agree(
                                    dspace_title, dspace_subtitle, pure_title_val, pure_subtitle_val
                                )
                                if not words_ok:
                                    reason = f"titles differ in wording (words without a counterpart: {extra_words})"
                                    print(f"  ⚠️ Title match ({similarity:.1%}) with {pure_item.get('uuid')} rejected: {reason}")
                                    log_entry.setdefault("rejectedMatches", []).append({"pureUUID": pure_item.get("uuid"), "reason": reason})
                                    continue
                                ok, reason = confirm_title_match(row, pure_item, dspace_title, dspace_subtitle)
                                if not ok:
                                    print(f"  ⚠️ Title match ({similarity:.1%}) with {pure_item.get('uuid')} rejected: {reason}")
                                    log_entry.setdefault("rejectedMatches", []).append({"pureUUID": pure_item.get("uuid"), "reason": reason})
                                    continue
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
        resolved_from_duplicates = len(matched_records) > 1
        if len(matched_records) > 1:
            log_entry["duplicates"] = True
            chosen_record = resolve_record_duplicate(matched_records, log_entry)
            if chosen_record:
                matched_records = [chosen_record]

        # The Pure record about to be updated may already be linked to a
        # DIFFERENT DSpace item -- whether it was the only match or was chosen
        # among duplicates (e.g. by the DSpace UUID rule). It is still updated,
        # but every such case is reported (processing log + CSV).
        if matched_records:
            target_record = matched_records[0]
            record_dspace_uuids = dspace_uuids_of(target_record)
            row_dspace_uuid = (row.get("uuid") or "").strip()
            if record_dspace_uuids and row_dspace_uuid.lower() not in {u.lower() for u in record_dspace_uuids}:
                row_handles = extract_handles_from_uri(row.get("dc.identifier.uri", ""))
                mismatch_case = "chosen among duplicates" if resolved_from_duplicates else "single match"
                print(f"  ⚠️ DSpace UUID mismatch ({mismatch_case}): DSpace item {row_dspace_uuid or '(no uuid)'} is updating "
                      f"Pure record {target_record.get('uuid')}, which already has DSpace UUID(s) {record_dspace_uuids}")
                _dspace_uuid_mismatches.append({
                    "dspace_uuid": row_dspace_uuid,
                    "pure_record_dspace_uuids": " ; ".join(record_dspace_uuids),
                    "handle": row_handles[0] if row_handles else None,
                    "pure_uuid": target_record.get("uuid"),
                    "dspace_title": row.get("dc.title", ""),
                    "pure_title": (target_record.get("title") or {}).get("value", ""),
                    "portal_url": target_record.get("portalUrl", ""),
                    "case": mismatch_case,
                    "match_type": match_type,
                    "resolution": ("Pure's DSpace UUID kept" if DSPACE_UUID_PREFERENCE == "pure"
                                   else "DSpace UUID from the CSV written"),
                })

        if matched_records:
            log_entry["matched"] = True
            record = matched_records[0]
            log_entry["uuid"] = record.get("uuid", "")
            log_entry["pureType"] = record.get("type", {}).get("uri", "")
            log_entry["matchType"] = match_type

            try:
                updated_record, success = update_record_from_dspace(
                    record, row, person_index, org_index, log_entry,
                    before_update_records, pub_index, journal_index=journal_index, override_mode=OVERRIDE_MODE,
                    journal_lookup=journal_lookup,
                )
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
                new_record = create_new_record_from_dspace(
                    row, person_index, org_index, pub_index, journal_index=journal_index,
                    journal_lookup=journal_lookup, log_entry=log_entry,
                )
                
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

    #  Unmatched funders CSV
    if _unmatched_funders:
        unmatched_funders_csv = os.path.join(OUTPUT_DIR, f"unmatched_funders_{TODAY}.csv")
        print(f"\n📝 Writing {len(_unmatched_funders)} unmatched funders to CSV...")
        with open(unmatched_funders_csv, 'w', newline='', encoding='utf-8') as f:
            fieldnames = ['name', 'handle', 'title', 'pure_uuid']
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(_unmatched_funders)
        print(f"✅ Unmatched funders saved to: {unmatched_funders_csv}")

    # Unmatched publishers CSV
    if _unmatched_publishers:
        unmatched_publishers_csv = os.path.join(OUTPUT_DIR, f"unmatched_publishers_{TODAY}.csv")
        print(f"\n📝 Writing {len(_unmatched_publishers)} unmatched publishers to CSV...")
        with open(unmatched_publishers_csv, 'w', newline='', encoding='utf-8') as f:
            fieldnames = ['name', 'handle', 'title', 'pure_uuid']
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(_unmatched_publishers)
        print(f"✅ Unmatched publishers saved to: {unmatched_publishers_csv}")

    # Unmatched journals CSV
    if _unmatched_journals:
        unmatched_journals_csv = os.path.join(OUTPUT_DIR, f"unmatched_journals_{TODAY}.csv")
        print(f"\n📝 Writing {len(_unmatched_journals)} unmatched journals to CSV...")
        with open(unmatched_journals_csv, 'w', newline='', encoding='utf-8') as f:
            fieldnames = ['handle', 'title', 'dc.identifier.issn', 'journal_issn', 'dc.identifier.journal',
                          'journal_title', 'reason', 'pure_uuid']
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(_unmatched_journals)
        print(f"✅ Unmatched journals saved to: {unmatched_journals_csv}")

    # Publisher DOI conflicts CSV (same publisher DOI on two different DSpace
    # items with different titles -- the DOI match was not used)
    if _publisher_doi_conflicts:
        conflicts_csv = os.path.join(OUTPUT_DIR, f"publisher_doi_conflicts_{TODAY}.csv")
        with open(conflicts_csv, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=['dspace_uuid', 'handle', 'dspace_title', 'doi',
                                                   'pure_uuid', 'pure_title', 'pure_record_dspace_uuids'])
            writer.writeheader()
            writer.writerows(_publisher_doi_conflicts)
        print(f"✅ Publisher DOI conflicts saved to: {conflicts_csv}")

    # DSpace UUID mismatches CSV (the Pure record updated -- single match or
    # chosen among duplicates -- carries a different DSpace item's UUID)
    if _dspace_uuid_mismatches:
        mismatches_csv = os.path.join(OUTPUT_DIR, f"dspace_uuid_mismatches_{TODAY}.csv")
        print(f"\n📝 Writing {len(_dspace_uuid_mismatches)} DSpace UUID mismatches to CSV...")
        with open(mismatches_csv, 'w', newline='', encoding='utf-8') as f:
            fieldnames = ['dspace_uuid', 'pure_record_dspace_uuids', 'handle', 'pure_uuid',
                          'dspace_title', 'pure_title', 'portal_url', 'case', 'match_type', 'resolution']
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(_dspace_uuid_mismatches)
        print(f"✅ DSpace UUID mismatches saved to: {mismatches_csv}")

 
    # Count results
    # Every record lands in exactly one of these buckets per breakdown, so
    # nothing below is counted in more than one place within the same total.
    not_publications_count = sum(1 for e in log_entries if e.get('error') == "Skipped: not in a Publications collection")
    no_contributors_count = sum(1 for e in log_entries if e.get('error') == "No contributors found in any contributor field")
    dataset_count = sum(1 for e in log_entries if e.get('error') == "Skipped: dataset (not uploaded to Pure)")
    skipped_count = not_publications_count + no_contributors_count + dataset_count

    matched_count = sum(1 for e in log_entries if e['matched'])
    unmatched_new_record_count = sum(1 for e in log_entries if not e['matched'] and e['success'])
    no_matched_authors_count = sum(1 for e in log_entries if e.get('error') == "No matched contributors")
    error_count = len(error_log)  # exceptions raised while updating or creating a record
    failed_count = no_matched_authors_count + error_count

    success_count = sum(1 for e in log_entries if e['success'])

    print(f"\n✅ Done! {len(log_entries)} records processed.")
    print(f"   Skipped (out of scope): {skipped_count}")
    print(f"     ↳ Not in Publications collection: {not_publications_count}")
    print(f"     ↳ Dataset (not uploaded to Pure): {dataset_count}")
    print(f"     ↳ No contributors in any field: {no_contributors_count}")
    print(f"   Matched to existing Pure record: {matched_count}")
    print(f"   Unmatched (new records created): {unmatched_new_record_count}")
    print(f"   Successfully processed: {success_count}")
    print(f"   Failed (total): {failed_count}")
    print(f"     ↳ No contributors matched to Pure persons: {no_matched_authors_count}")
    print(f"     ↳ Other errors: {error_count}")
    print(f"   Unmatched contributors: {len(_unmatched_contributors)}")
    print(f"   Unmatched funders: {len(_unmatched_funders)}")
    print(f"   Unmatched publishers: {len(_unmatched_publishers)}")
    print(f"   Unmatched journals: {len(_unmatched_journals)}")
    print(f"   DSpace UUID mismatches: {len(_dspace_uuid_mismatches)}")
    print(f"   Publisher DOI conflicts: {len(_publisher_doi_conflicts)}")
    print(f"   Logs saved to: {LOG_DIR}")

    # Calculate elapsed time
    elapsed_time = time.time() - start_time
    hours = int(elapsed_time // 3600)
    minutes = int((elapsed_time % 3600) // 60)
    seconds = int(elapsed_time % 60)
    print(f"\n⏱️  Total time elapsed: {hours:02d}:{minutes:02d}:{seconds:02d}")
    
    logger.close()
    sys.stdout = logger.terminal


if __name__ == "__main__":
    main()