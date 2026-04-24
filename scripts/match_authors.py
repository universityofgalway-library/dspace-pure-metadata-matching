import regex as re
import os
import sys
import json
import logging
from tqdm import tqdm
from datetime import date 

# --- CONFIGURATION ---
TODAY = date.today().isoformat()

DSpace_Authors_JSON = "./author_matching/2026-04-22/missing_authors_2026-04-22.json"
Pure_Internal_JSON = "./pure_entities/pure_persons_2026-04-22.json"
Pure_External_JSON = "./pure_entities/pure_external-persons_2026-04-24.json"
IRISH_SURNAMES_JSON = "./author_matching/irish_surnames.json"  # New file with canonical Irish surnames and their variants   
OUTPUT_DIR = f"./author_matching/{TODAY}"
HYPHEN_CAP_REGEX = re.compile(r'([-–])(\p{L})', re.UNICODE)

# GENERATE_INITIAL_VARIANTS = False  # Generate variants from existing initials (e.g., "J." -> "J", "J P", etc.)
# GENERATE_INITIALS_FROM_NAMES = False  # Generate initials from full names (e.g., "John" -> "J", "J.")

os.makedirs(OUTPUT_DIR, exist_ok=True)

# Configure logging to write to file and stdout
logging.basicConfig(
    level=logging.INFO,
    format='%(message)s',
    handlers=[
        logging.FileHandler(f"./author_matching/match_authors_{TODAY}.log", mode='w', encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)

# Replace print() with logging.info()
def print(*args, **kwargs):
    logging.info(' '.join(map(str, args)))

# --- HELPER FUNCTIONS ---

def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)
    

def normalize(s):
    """Normalize string: replace curly apostrophes in Irish surnames, strip + lower case"""
    if not s:
        return ""
    # Replace curly apostrophes with straight ones
    s = s.replace("’", "'")
    s = s.replace("‘", "'")
    return s.strip().lower()


def capitalize_after_hyphen(name: str) -> str:
    """
    Capitalize any Unicode letter following - or – in a name.
    Example: 'Marie-louise' -> 'marie-Louise'
             'óscar–pérez' -> 'óscar–Pérez'
    """
    if not name:
        return name

    def repl(match):
        return match.group(1) + match.group(2).upper()

    return HYPHEN_CAP_REGEX.sub(repl, name)


def build_irish_surname_index(json_file_path):
    """
    Build lookup index from canonical Irish surname JSON file.
    Returns dict: normalized_variant -> {'canonical': str, 'alternatives': [str]}
    
    JSON format expected:
    [
        {
            "canonical": "O'Donoghoe",
            "alternatives": ["O Donoghoe", "O' Donoghoe"]
        }
    ]
    
    Index maps all variants (canonical + alternatives) to the canonical form and its alternatives.
    """
    if not os.path.exists(json_file_path):
        print(f"⚠️ WARNING: Irish surnames file not found: {json_file_path}")
        print("   Irish surname normalization will be skipped.")
        return {}
    
    index = {}
    
    with open(json_file_path, 'r', encoding='utf-8') as f:
        surnames_data = json.load(f)
    
    for entry in surnames_data:
        canonical = entry.get("canonical", "")
        alternatives = entry.get("alternatives", [])
        
        if not canonical:
            continue
        
        # Normalize canonical for lookup
        canonical_lower = normalize(canonical)
        
        # Store the canonical and alternatives together
        entry_data = {
            'canonical': canonical,
            'alternatives': alternatives
        }
        
        # Index the canonical form
        index[canonical_lower] = entry_data
        
        # Index all alternatives pointing to the same canonical form
        for alt in alternatives:
            alt_lower = normalize(alt)
            index[alt_lower] = entry_data
    
    print(f"✅ Loaded {len(surnames_data)} canonical Irish surnames with {len(index)} total indexed variants")
    return index


def get_firstname_variants(first_name, generate_initial_variants=False, generate_initials_from_names=False):
    """
    Generate variants of a first name.
    Returns two lists:
    - all_variants: all variants for recording in alternativeFirstNames
    - matching_variants: only variants that should be used for matching
    
    Parameters:
    - generate_initial_variants: If True, generate variants from existing initials (J. -> J, J P, etc.) for matching
    - generate_initials_from_names: If True, generate initials from full names (John -> J, J.) for recording ONLY
    """
    if not first_name:
        return [], []
    
    all_variants = set()
    matching_variants = set()
    name = first_name.strip()
    
    # Check if it's initials (contains dots or is very short)
    has_dots = '.' in name
    is_short = len(name.replace('.', '').replace(' ', '').replace('-', '')) <= 2  # Likely initials if ≤2 chars
    
    if has_dots or (is_short and len(name) <= 5):
        # This is already initials
        if generate_initial_variants:
            words = name.split()
            variants = {
                name.replace('.', '').title(),  # Remove dots
                ' '.join([w[0].upper() for w in words]),  # Initials no dots, with spaces
                ''.join([w[0].upper() for w in words]),  # Initials no dots, no spaces
                '.'.join([w[0].upper() for w in words]) + '.',  # Initials with dots, no spaces
                '. '.join([w[0].upper() for w in words]) + '.',  # Initials with dots, with spaces
                '.-'.join([w[0].upper() for w in words]) + '.',  # Initials with dots and hyphens, without spaces
            }
            all_variants.update(variants)
            matching_variants.update(variants)  # Use for matching
    
    else:
        # This is a full name
        if generate_initials_from_names:
            initials = []
            
            # Handle hyphenated names like "Mary-Jane" -> "M-J" or "M.-J."
            if '-' in name:
                parts = name.split('-')
                initials = [p[0].upper() for p in parts if p]
                # Add hyphenated initials
                all_variants.add('-'.join(initials))
                all_variants.add('-'.join([i + '.' for i in initials]))
            else:
                # Simple name
                parts = name.split()
                if parts:
                    initials = [p[0].upper() for p in parts]
            
            # Add simple initial variants (for recording only, NOT matching)
            if len(initials) > 0:
                all_variants.add(initials[0])  # "J"
                all_variants.add(initials[0] + '.')  # "J."
                
                # If multiple initials, add combined versions
                if len(initials) > 1:
                    all_variants.add(''.join(initials))  # "JP"
                    all_variants.add(' '.join(initials))  # "J P"
                    all_variants.add('.'.join(initials) + '.')  # "J.P."
                    all_variants.add('. '.join(initials) + '.')  # "J. P."
            
            # NEVER add to matching_variants - these are for recording only
    
    # Remove the original name from variants (we'll add it separately)
    all_variants.discard(name)
    matching_variants.discard(name)
    
    return list(all_variants), list(matching_variants)


def get_surname_variants_from_list(surname, irish_surname_index):
    """
    Check if surname matches a canonical Irish surname from the JSON list.
    
    Returns dict with:
    {
        'variants': [list of normalized variants for matching],
        'canonical': canonical spelling from list (preserving original case),
        'alternatives': [list of alternative spellings with original case]
    }
    
    If no match in list, returns original surname with no variants.
    """
    if not surname:
        return {'variants': [], 'canonical': '', 'alternatives': []}
    
    normalized = normalize(surname)
    
    # Look up in the index
    if normalized in irish_surname_index:
        entry_data = irish_surname_index[normalized]
        canonical = entry_data['canonical']
        alternatives = entry_data['alternatives']
        
        # Generate all normalized variants for matching
        variants = {normalize(canonical)}
        for alt in alternatives:
            variants.add(normalize(alt))
        
        return {
            'variants': list(variants),
            'canonical': canonical,  # Keep original capitalization
            'alternatives': alternatives  # Keep original capitalization
        }
    
    # No match found - return original
    return {
        'variants': [normalized],
        'canonical': surname,
        'alternatives': []
    }


def normalize_surname_for_output(surname, irish_surname_index):
    """Return canonical form from Irish surname list, or original if not found."""
    if not surname:
        return surname
    
    result = get_surname_variants_from_list(surname, irish_surname_index)
    return result['canonical']


def get_identifiers_from_pure_person(person):
    """
    Extract ORCID and Scopus Author ID from a Pure internal person record.
    Returns (orcid, scopus_id) as strings, or empty strings if not found.
    """
    orcid = person.get("orcid", "")
    
    scopus_id = ""
    for identifier in person.get("identifiers", []):
        if isinstance(identifier.get("type"), dict):
            uri = identifier["type"].get("uri", "")
            if uri == "/dk/atira/pure/person/personsources/scopusauthor":
                scopus_id = identifier.get("id", "")
                break
    
    return orcid, scopus_id


def get_organizations_from_pure_person(person, is_internal=True):
    """Extract organization UUIDs from Pure person record"""
    internal_orgs = []
    external_orgs = []
    primary_internal_org = ""
    
    if is_internal:
        # For internal persons: extract from various staff/student/visitor associations
        association_types = [
            ("staffOrganizationAssociations", "organization"),
            ("honoraryStaffOrganizationAssociations", "organization"),
            ("visitingScholarOrganizationAssociations", "organization"),
            ("studentOrganizationAssociations", "organization")
        ]
        
        # Extract UUIDs from all configured association types
        for field_name, org_field in association_types:
            for assoc in person.get(field_name, []):
                org = assoc.get(org_field, {})
                if org.get("uuid") and org.get("systemName") == "Organization":
                    internal_orgs.append(org["uuid"])
                    
                    # Check for primary association
                    if assoc.get("primaryAssociation") == True and not primary_internal_org:
                        primary_internal_org = org["uuid"]
        
        # Check externalPositions for internal persons (they can have external org affiliations)
        for ext_pos in person.get("externalPositions", []):
            ext_org = ext_pos.get("externalOrganization", {})
            if ext_org.get("uuid") and ext_org.get("systemName") == "ExternalOrganization":
                external_orgs.append(ext_org["uuid"])
    
    else:
        # For external persons: organizations are stored directly in externalOrganizations array
        for ext_org in person.get("externalOrganizations", []):
            if ext_org.get("uuid"):
                external_orgs.append(ext_org["uuid"])
        
        # Also check externalPositions and contributorAssociations if they exist
        for ext_pos in person.get("externalPositions", []):
            ext_org = ext_pos.get("externalOrganization", {})
            if ext_org.get("uuid"):
                external_orgs.append(ext_org["uuid"])
        
        for contrib in person.get("contributorAssociations", []):
            ext_org = contrib.get("externalOrganization", {})
            if ext_org.get("uuid"):
                external_orgs.append(ext_org["uuid"])
    
    return list(set(internal_orgs)), list(set(external_orgs)), primary_internal_org  # Return both lists, deduplicated, + primary association


def build_index_persons(person_list, irish_surname_index, is_internal=True, generate_initial_variants=False, generate_initials_from_names=False):
    index = {}
    alt_names_by_uuid = {}
    internal_orgs_by_uuid = {}
    external_orgs_by_uuid = {}
    primary_internal_org_by_uuid = {}
    visibility_by_uuid = {}
    orcid_by_uuid = {}        
    scopus_id_by_uuid = {}    

    for person in person_list:
        uuid = person.get("uuid")
        if not uuid:
            continue

        if is_internal:
            visibility = person.get("visibility", {})
            vis_key = visibility.get("key", "")
            visibility_by_uuid[uuid] = vis_key

            # Extract and store ORCID and Scopus ID for internal persons only
            orcid, scopus_id = get_identifiers_from_pure_person(person)
            if orcid:
                if uuid in orcid_by_uuid:
                    print(f"⚠️ WARNING: Multiple ORCIDs found for internal UUID {uuid}: existing='{orcid_by_uuid[uuid]}', new='{orcid}'")
                else:
                    orcid_by_uuid[uuid] = orcid
            if scopus_id:
                if uuid in scopus_id_by_uuid:
                    print(f"⚠️ WARNING: Multiple Scopus IDs found for internal UUID {uuid}: existing='{scopus_id_by_uuid[uuid]}', new='{scopus_id}'")
                else:
                    scopus_id_by_uuid[uuid] = scopus_id

        primary_first = person.get("name", {}).get("firstName", "")
        primary_last = person.get("name", {}).get("lastName", "")

        all_first_names = [primary_first] if primary_first else []
        all_last_names = [primary_last] if primary_last else []

        for name_entry in person.get("names", []):
            name_obj = name_entry.get("name", {})
            if first := name_obj.get("firstName", ""):
                all_first_names.append(first)
            if last := name_obj.get("lastName", ""):
                all_last_names.append(last)

        if not all_first_names or not all_last_names:
            continue

        # Use new function for surname variants
        all_name_variants = [
            (norm_f_var, variant, first, last)
            for first in all_first_names
            for last in all_last_names
            for variant in get_surname_variants_from_list(last, irish_surname_index)['variants']
            for norm_f_var in (
                {normalize(first)} |
                {normalize(v) for v in get_firstname_variants(first, generate_initial_variants, generate_initials_from_names)[1]}  # [1] = matching_variants
            )
        ]

        # Index direct order
        for norm_f, norm_l, orig_f, orig_l in all_name_variants:
            key = (norm_f, norm_l)
            if key not in index:
                index[key] = []
            index[key].append(uuid)
        
        # Index swapped order
        for norm_f, norm_l, orig_f, orig_l in all_name_variants:
            key_swapped = (norm_l, norm_f)
            if key_swapped not in index:
                index[key_swapped] = []
            index[key_swapped].append(uuid)

        alt_names = [(orig_f, orig_l) for norm_f, norm_l, orig_f, orig_l in all_name_variants
                     if orig_f != primary_first or orig_l != primary_last]
        if alt_names:
            alt_names_by_uuid[uuid] = list(set(alt_names))

        int_orgs, ext_orgs, primary_int_org = get_organizations_from_pure_person(person, is_internal=is_internal)
        if int_orgs:
            internal_orgs_by_uuid[uuid] = int_orgs
        if ext_orgs:
            external_orgs_by_uuid[uuid] = ext_orgs
        if primary_int_org:
            primary_internal_org_by_uuid[uuid] = primary_int_org

    return index, alt_names_by_uuid, internal_orgs_by_uuid, external_orgs_by_uuid, visibility_by_uuid, primary_internal_org_by_uuid, orcid_by_uuid, scopus_id_by_uuid 


def enrich_authors(authors, internal_index, external_index, irish_surname_index, internal_alt_names=None, external_alt_names=None, 
                   internal_internal_orgs=None, internal_external_orgs=None, external_internal_orgs=None, external_external_orgs=None, 
                   internal_visibility_by_uuid=None, internal_primary_org_by_uuid=None, external_primary_org_by_uuid=None, 
                   internal_orcid_by_uuid=None, internal_scopus_id_by_uuid=None,output_option="all", generate_initial_variants=False, generate_initials_from_names=False):
    """
    Enrich authors with Pure match data using canonical Irish surname list.
    """
    enriched = []
    filtered_out_count = 0

    for author in tqdm(authors, desc="Matching Authors", unit="author"):
        first = author.get("firstName", "").strip()
        last = author.get("lastName", "").strip()
        
        # Capitalize first names normally
        if first:
            parts = first.split()
            first = ' '.join([capitalize_after_hyphen(p.capitalize()) for p in parts])
        
        # Use canonical form from list for last name
        if last:
            last = capitalize_after_hyphen(last)

        if not first or not last:
            filtered_out_count += 1
            continue

        # Get surname variants from canonical list
        surname_result = get_surname_variants_from_list(last, irish_surname_index)
        surname_variants = surname_result['variants']
        canonical_last_name = surname_result['canonical']
        surname_alternatives = surname_result['alternatives']
        
        # Normalize for matching - only use matching_variants [1]
        norm_first = normalize(first)
        norm_first_variants = {norm_first} | {normalize(v) for v in get_firstname_variants(first, generate_initial_variants, generate_initials_from_names)[1]}  # [1] = matching_variants
        
        # Try to find matches with surname variants
        internal_matches = []
        external_matches = []
        
        for surname_var in surname_variants:
            for norm_f_var in norm_first_variants:
                key = (norm_f_var, surname_var)
                internal_matches.extend(internal_index.get(key, []))
                external_matches.extend(external_index.get(key, []))

        # Swapped order lookup
        for surname_var in surname_variants:
            for norm_f_var in norm_first_variants:
                key_swapped = (surname_var, norm_f_var)
                internal_matches.extend(internal_index.get(key_swapped, []))
                external_matches.extend(external_index.get(key_swapped, []))

        # Deduplicate
        internal_matches = list(set(internal_matches))
        external_matches = list(set(external_matches))

        # Extract alternative names from matches
        alternative_firstnames = set()
        alternative_lastnames = set()

        orcid_values = set()
        scopus_id_values = set()
        orcid_out = ""       
        scopus_id_out = ""    

        for uuid in internal_matches:
            if internal_alt_names and uuid in internal_alt_names:
                for orig_first, orig_last in internal_alt_names[uuid]:
                    alt_first = capitalize_after_hyphen(orig_first.capitalize())
                    # Get canonical form from list (preserves original capitalization)
                    alt_last_result = get_surname_variants_from_list(orig_last, irish_surname_index)
                    alt_last = alt_last_result['canonical']
                    if alt_first != first:
                        alternative_firstnames.add(alt_first)
                    if alt_last != canonical_last_name:
                        alternative_lastnames.add(alt_last)

        for uuid in external_matches:
            if external_alt_names and uuid in external_alt_names:
                for orig_first, orig_last in external_alt_names[uuid]:
                    alt_first = capitalize_after_hyphen(orig_first.capitalize())
                    # Get canonical form from list (preserves original capitalization)
                    alt_last_result = get_surname_variants_from_list(orig_last, irish_surname_index)
                    alt_last = alt_last_result['canonical']
                    if alt_first != first:
                        alternative_firstnames.add(alt_first)
                    if alt_last != canonical_last_name:
                        alternative_lastnames.add(alt_last)
        
        # Add first name variants - use all_variants [0] for recording
        first_name_variants = get_firstname_variants(first, generate_initial_variants, generate_initials_from_names)[0]  # [0] = all_variants
        for variant in first_name_variants:
            if variant != first:
                alternative_firstnames.add(variant)
        
        # Add surname alternatives from canonical list (preserve original capitalization)
        for alt in surname_alternatives:
            if alt != canonical_last_name:
                alternative_lastnames.add(alt)
        
        # Remove duplicates
        alternative_lastnames.discard(canonical_last_name)
        alternative_firstnames.discard(first)

        # Get organizations and visibility
        internal_organizations = []
        external_organizations = []
        internal_uuids_with_visibility = []
        primary_internal_organization = ""

        for uuid in internal_matches:
            if internal_internal_orgs and uuid in internal_internal_orgs:
                internal_organizations.extend(internal_internal_orgs[uuid])
            if internal_external_orgs and uuid in internal_external_orgs:
                external_organizations.extend(internal_external_orgs[uuid])
            if internal_visibility_by_uuid and uuid in internal_visibility_by_uuid:
                visibility = internal_visibility_by_uuid[uuid]
                internal_uuids_with_visibility.append({
                    "uuid": uuid,
                    "visibility": visibility
                })
            if not primary_internal_organization and internal_primary_org_by_uuid and uuid in internal_primary_org_by_uuid:
                primary_internal_organization = internal_primary_org_by_uuid[uuid]

        # Collect ORCID and Scopus ID from internal matches
        orcid_values = set()
        scopus_id_values = set()
        for uuid in internal_matches:
            if internal_orcid_by_uuid and uuid in internal_orcid_by_uuid:
                orcid_values.add(internal_orcid_by_uuid[uuid])
            if internal_scopus_id_by_uuid and uuid in internal_scopus_id_by_uuid:
                scopus_id_values.add(internal_scopus_id_by_uuid[uuid])

        if len(orcid_values) > 1:
            print(f"⚠️ WARNING: Multiple distinct ORCIDs for author '{first} {last}': {orcid_values}")
        if len(scopus_id_values) > 1:
            print(f"⚠️ WARNING: Multiple distinct Scopus IDs for author '{first} {last}': {scopus_id_values}")

        orcid_out = next(iter(orcid_values), "")
        scopus_id_out = next(iter(scopus_id_values), "")

        for uuid in external_matches:
            if external_internal_orgs and uuid in external_internal_orgs:
                internal_organizations.extend(external_internal_orgs[uuid])
            if external_external_orgs and uuid in external_external_orgs:
                external_organizations.extend(external_external_orgs[uuid])

        internal_organizations = list(set(internal_organizations))
        external_organizations = list(set(external_organizations))

        # Preserve papers
        papers = author.get("papers", [])
        seen_handles = set()
        unique_papers = []
        for paper in papers:
            if handle := paper.get("handle", "").strip():
                if handle not in seen_handles:
                    seen_handles.add(handle)
                    unique_papers.append({
                        "handle": handle,
                        "doi": paper.get("doi", "").strip(),
                        "title": paper.get("title", "").strip()
                    })

        # Use canonical last name from list
        enriched_author = {
            **author,
            "lastName": canonical_last_name,
            "orcid": orcid_out,           
            "scopusId": scopus_id_out,    
            "papers": unique_papers,
            "paperCount": len(unique_papers),
            "internal": len(internal_matches) > 0,
            "external": len(external_matches) > 0,
            "internalDuplicates": len(internal_matches) > 1,
            "externalDuplicates": len(external_matches) > 1,
            "internalUUIDs": internal_uuids_with_visibility,
            "externalUUIDs": external_matches,
            "internalOrganizations": internal_organizations,
            "externalOrganizations": external_organizations,
            "primaryInternalOrganization": primary_internal_organization,
            "alternativeFirstName": list(alternative_firstnames),
            "alternativeLastName": list(alternative_lastnames)
        }

        filter_map = {
            "all": lambda a: True,
            "matched_internal": lambda a: a["internal"],
            "matched_external": lambda a: a["external"],
            "unmatched": lambda a: not (a["internal"] or a["external"]),
            "matched_all_duplicates": lambda a: a["internalDuplicates"] or a["externalDuplicates"],
            "matched_internal_duplicates": lambda a: a["internalDuplicates"],
            "matched_external_duplicates": lambda a: a["externalDuplicates"],
            "matched_internal_external_duplicates": lambda a: a["internal"] and a["external"],
        }
        
        if filter_map.get(output_option, lambda a: False)(enriched_author):
            enriched.append(enriched_author)

    print(f"✅ Total DSpace authors processed: {len(authors)}")
    print(f"✅ Authors filtered out (missing first or last name): {filtered_out_count}")
    print(f"✅ Authors remaining after filtering: {len(enriched)}")

    return enriched


def analyze_persons(json_file_path):
    with open(json_file_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    total_entries = len(data)
    internal_true = 0
    external_true = 0
    both_true = 0
    internal_duplicates_true = 0
    external_duplicates_true = 0
    alt_first_count = 0
    alt_last_count = 0

    total_matched = sum(1 for e in data if e["internal"] or e["external"])
    
    all_paper_handles = set()

    for entry in data:
        # Count internal = true
        if entry.get("internal", False):
            internal_true += 1

        # Count external = true
        if entry.get("external", False):
            external_true += 1

        # Count both internal and external = true
        if entry.get("internal", False) and entry.get("external", False):
            both_true += 1

        # Count internal_duplicates = true
        if entry.get("internalDuplicates", False):
            internal_duplicates_true += 1

        # Count external_duplicates = true
        if entry.get("externalDuplicates", False):
            external_duplicates_true += 1

        # Count alternative names
        if entry.get("alternativeFirstName", []):
            alt_first_count += 1
        if entry.get("alternativeLastName", []):
            alt_last_count += 1

        # Add all paper handles from this author to the global set
        papers = entry.get("papers", [])
        for paper in papers:
            handle = paper.get("handle", "").strip()
            if handle:  # Avoid empty/None values
                all_paper_handles.add(handle)

    # Total unique papers across all authors
    total_unique_papers = len(all_paper_handles)

    # Print results
    print("\n=== DATA STATS ===")
    print(f"Total filtered entries: {total_entries}")
    print(f"Total authors matched (internal or external): {total_matched}")
    print(f"Unmatched authors: {len(data) - total_matched}")
    print(f"Internal matches: {internal_true}")
    print(f"External matches: {external_true}")
    print(f"Matches in both internal & external: {both_true}")
    print(f"Entries with internal duplicates: {internal_duplicates_true}")
    print(f"Entries with external duplicates: {external_duplicates_true}")
    print(f"Entries with alternative first names: {alt_first_count}")
    print(f"Entries with alternative last names: {alt_last_count}")
    print(f"Total unique papers across all authors: {total_unique_papers}")


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Match DSpace authors to Pure persons and output enriched JSON.")
    parser.add_argument("--output-dir", type=str, help="Override default output directory")
    parser.add_argument("--prefix", type=str, default="authors", help="Prefix for output filenames (default: 'authors')")
    parser.add_argument("--option", choices=[
        "all",
        "matched_internal",
        "matched_external",
        "unmatched",
        "matched_all_duplicates",
        "matched_internal_duplicates",
        "matched_external_duplicates",
        "matched_internal_external_duplicates"
    ], help="Single output option to generate (if not specified, all options are generated)")
    parser.add_argument("--generate-initial-variants", action="store_true", 
                       help="Generate variants from existing initials (e.g., J. -> J, etc.) and use for matching")
    parser.add_argument("--generate-initials-from-names", action="store_true",
                       help="Generate initials from full names (e.g., John -> J, J.) for recording ONLY (never used for matching)")

    args = parser.parse_args()

    # Load data
    input_authors = load_json(DSpace_Authors_JSON)
    internal_persons = load_json(Pure_Internal_JSON)
    external_persons = load_json(Pure_External_JSON)
    
    # Build Irish surname index from JSON
    irish_surname_index = build_irish_surname_index(IRISH_SURNAMES_JSON)

    # Pass configuration flags to build_index_persons
    internal_index, internal_alt_names, internal_internal_orgs, internal_external_orgs, internal_visibility_by_uuid, internal_primary_org_by_uuid, internal_orcid_by_uuid, internal_scopus_id_by_uuid = build_index_persons(
        internal_persons, irish_surname_index, is_internal=True,
        generate_initial_variants=args.generate_initial_variants,
        generate_initials_from_names=args.generate_initials_from_names
    )
    external_index, external_alt_names, external_internal_orgs, external_external_orgs, external_visibility_by_uuid, external_primary_org_by_uuid, _orcid_unused, _scopus_unused = build_index_persons(
        external_persons, irish_surname_index, is_internal=False,
        generate_initial_variants=args.generate_initial_variants,
        generate_initials_from_names=args.generate_initials_from_names
    )

    output_options = [
        ("all", "All authors"),
        ("matched_internal", "Only internal matches"),
        ("matched_external", "Only external matches"),
        ("unmatched", "Only unmatched"),
        ("matched_all_duplicates", "Only duplicates (internal or external)"),
        ("matched_internal_duplicates", "Only internal duplicates"),
        ("matched_external_duplicates", "Only external duplicates"),
        ("matched_internal_external_duplicates", "Only people found in both internal and external lists")
    ]

    if args.option:
        selected_options = [opt for opt in output_options if opt[0] == args.option]
    else:
        selected_options = output_options

    output_dir = args.output_dir if args.output_dir else OUTPUT_DIR
    os.makedirs(output_dir, exist_ok=True)

    for option, description in selected_options:
        print(f"\n--- Processing: {description} ---")
        # Pass configuration flags to enrich_authors
        enriched = enrich_authors(
            input_authors,
            internal_index,
            external_index,
            irish_surname_index, 
            internal_alt_names,
            external_alt_names,
            internal_internal_orgs,
            internal_external_orgs,
            external_internal_orgs,
            external_external_orgs,
            internal_visibility_by_uuid,
            internal_primary_org_by_uuid,
            external_primary_org_by_uuid,
            internal_orcid_by_uuid,      
            internal_scopus_id_by_uuid,   
            output_option=option,
            generate_initial_variants=args.generate_initial_variants,
            generate_initials_from_names=args.generate_initials_from_names
        )

        filename = f"{args.prefix}_{option}_{TODAY}.json"
        filepath = os.path.join(output_dir, filename)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(enriched, f, indent=4, ensure_ascii=False)

        print(f"✅ Output saved to: {filepath}")
        analyze_persons(filepath)

    print("\n🎉 All outputs generated!")


if __name__ == "__main__":
    main()