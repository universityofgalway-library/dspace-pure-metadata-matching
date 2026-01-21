import regex as re
import os
import sys
import json
import logging
from tqdm import tqdm
from datetime import date 

# --- CONFIGURATION ---
DSpace_Authors_JSON = "dspace_test_authors_all_2025-12-02.json"
Pure_Internal_JSON = "./pure_entities/pure_test_persons_2026-01-13.json"
Pure_External_JSON = "./pure_entities/pure_test_external-persons_2026-01-13.json"
OUTPUT_DIR = "./matching_test/matched_authors"
HYPHEN_CAP_REGEX = re.compile(r'([-–])(\p{L})', re.UNICODE)
TODAY = date.today().isoformat()

os.makedirs(OUTPUT_DIR, exist_ok=True)

# Configure logging to write to file and stdout
logging.basicConfig(
    level=logging.INFO,
    format='%(message)s',
    handlers=[
        logging.FileHandler("./matching_test/match_authors.log", mode='w', encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)

# Replace print() with logging.info()
def print(*args, **kwargs):
    logging.info(' '.join(map(str, args)))

# --- HELPER FUNCTIONS ---

def normalize(s):
    """Normalize string: replace curly apostrophes in Irish surnames, strip + lower case"""
    if not s:
        return ""
    # Replace curly apostrophes with straight ones
    s = s.replace("’", "'")
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

def capitalize_irish_surname(surname):
    """Capitalize Irish surnames with special rules for prefixes.
    Preserves spacing from the input variant and handles multi-word surnames.
    
    Rules:
    - Mc, O': no space after prefix (by default)
    - Mac, Nic, Ní, Ua, Uí, Ó: space after prefix (by default)
    - But if the variant already has a space after the prefix, preserve it
    - For Ua, Uí, Ní, Ó: if next letter is 'h', keep it lowercase and capitalize next vowel
    - "an" after Mac/Nic should not be capitalized (e.g., "Mac an tSaoi", "Nic an Fhionnlaoich")
    - Special longer prefixes: Mac Con, Mac Giolla, Mac an, Nic an (handle before simple Mac/Nic)
    - For compound surnames with spaces (e.g., "O'Reilly-De brún" or "O'Malley keighran"), capitalize each part
    """
    if not surname:
        return surname
    
    # Don't normalize here - keep the original spacing from the variant
    original = surname.strip()
    normalized = normalize(original)
    vowels = set('aeiouáéíóú')
    
    # Prefixes that require special capitalization (Irish, European, etc.)
    # Order matters: longer prefixes first to avoid partial matches
    # Format: prefix_lower -> (prefix_capitalized, needs_space)
    all_prefixes = {
        # Longer Irish prefixes first
        'mac con': ('Mac Con', True),
        'mac giolla': ('Mac Giolla', True),
        'mac an': ('Mac an', True),
        'nic an': ('Nic an', True),
        # Single Irish prefixes
        'mc': ('Mc', False),         # Can vary: space or no space
        'mac': ('Mac', False),       # Can vary: space or no space
        'nic': ('Nic', False),       # Can vary: space or no space
        'ní': ('Ní', True),
        'ua': ('Ua', True),
        'uí': ('Uí', True),
        "o'": ("O'", False),
        'ó': ('Ó', True),
        # 'o': ('O', True),
        'mhic': ('Mhic', True),
        'de': ('De', True)
    }
    
    for prefix_lower, (prefix_cap, needs_space) in all_prefixes.items():
        if normalized.startswith(prefix_lower):
            # Extract the rest after the prefix, preserving original capitalization
            rest_original = original[len(prefix_lower):]
            
            # Check if there's a space after the prefix in the original
            has_space_after = rest_original.startswith(' ') if rest_original else False
            rest = rest_original.lstrip()
            
            if not rest:
                continue
            
            # Special handling for "Mac an" and "Nic an" - don't capitalize "an"
            if prefix_lower in ('mac an', 'nic an'):
                rest_words = rest.split()
                if rest_words:
                    # Capitalize each word after "an"
                    capitalized_words = [w[0].upper() + w[1:] if len(w) > 1 else w.upper() for w in rest_words]
                    remaining = ' '.join(capitalized_words)
                    return f"{prefix_cap} {remaining}"
            
            # For Ua, Uí, Ní, Ó: special handling if next letter is 'h'
            if prefix_lower in ('ua', 'uí', 'ní', 'ó'):
                # Check only the first part (before any space) for 'h' handling
                first_part = rest.split()[0] if ' ' in rest else rest
                remaining_parts = ' '.join(rest.split()[1:]) if ' ' in rest else ''
                
                if first_part.startswith('h') and len(first_part) > 1:
                    # Next letter is 'h', keep it lowercase and capitalize the next vowel
                    rest_after_h = first_part[1:]
                    # Find the first vowel and capitalize it
                    for i, char in enumerate(rest_after_h):
                        if char.lower() in vowels:
                            first_part_cap = 'h' + rest_after_h[:i] + rest_after_h[i].upper() + rest_after_h[i+1:]
                            if remaining_parts:
                                # Capitalize remaining parts
                                remaining_caps = ' '.join([p.capitalize() for p in remaining_parts.split()])
                                return f"{prefix_cap} {first_part_cap} {remaining_caps}"
                            return f"{prefix_cap} {first_part_cap}"
                    # No vowel found, just capitalize first letter of rest_after_h
                    first_part_cap = 'h' + (rest_after_h[0].upper() + rest_after_h[1:] if len(rest_after_h) > 1 else rest_after_h.upper())
                    if remaining_parts:
                        remaining_caps = ' '.join([p.capitalize() for p in remaining_parts.split()])
                        return f"{prefix_cap} {first_part_cap} {remaining_caps}"
                    return f"{prefix_cap} {first_part_cap}"
            
            # Standard capitalization: capitalize first letter of each space-separated part
            # This handles compound surnames like "O'Reilly-De brún" -> "O'Reilly-De Brún"
            # or "O'Malley keighran" -> "O'Malley Keighran"
            if ' ' in rest:
                parts = rest.split(' ')
                rest_cap = ' '.join([p[0].upper() + p[1:] if len(p) > 1 else p.upper() for p in parts])
            else:
                rest_cap = rest[0].upper() + rest[1:] if len(rest) > 1 else rest.upper()
            
            # Determine if we should use space: use the configured default unless the original has a different spacing
            use_space = needs_space or has_space_after
            
            if use_space:
                return f"{prefix_cap} {rest_cap}"
            else:
                return f"{prefix_cap}{rest_cap}"
    
    # For non-Irish surnames, capitalize each space-separated part
    if ' ' in original:
        parts = original.split(' ')
        return ' '.join([p.capitalize() for p in parts])
    
    return original.capitalize()

def get_firstname_variants(first_name):
    """
    Generate variants of a first name:
    - Full name "John" -> add initials ["J", "J."]
    - Initial with dot "J." -> add without dot ["J"]
    - Initials with dots "J. P." -> add variants ["J P", "JP", "J.P.", "J P."]
    - Initials without dots "J P" -> add with dots ["J. P."]
    - Initials no space "JP" -> add with space and dots ["J P", "J. P."]
    """
    if not first_name:
        return []
    
    variants = set()
    name = first_name.strip()
    
    # Check if it's initials (contains dots or is very short)
    has_dots = '.' in name
    has_spaces = ' ' in name
    is_short = len(name.replace('.', '').replace(' ', '')) <= 3  # Likely initials if ≤3 chars
    
    if has_dots or (is_short and len(name) <= 5):
        words = name.split()
        variants.add(name.replace('.', '').title())  # Remove dots
        variants.add(' '.join([w[0].upper() for w in words]))  # Initials no dots, with spaces
        variants.add(''.join([w[0].upper() for w in words]))  # Initials no dots, no spaces
        variants.add('.'.join([w[0].upper() for w in words]) + '.')  # Initials with dots, no spaces
        variants.add('. '.join([w[0].upper() for w in words]) + '.')  # Initials with dots, with spaces            
    
    else:
        # This is a full name - extract initials
        # Handle hyphenated names like "Mary-Jane" -> "M-J" or "M.-J."
        if '-' in name:
            parts = name.split('-')
            initials = [p[0].upper() for p in parts if p]
            # Add hyphenated initials
            variants.add('-'.join(initials))
            variants.add('-'.join([i + '.' for i in initials]))
        else:
            # Simple name
            parts = name.split()
            if parts:
                initials = [p[0].upper() for p in parts]
        
        # Add simple initial variants
        if len(initials) > 0:
            variants.add(initials[0])  # "J"
            variants.add(initials[0] + '.')  # "J."
            
            # If multiple initials, add combined versions
            if len(initials) > 1:
                variants.add(''.join(initials))  # "JP"
                variants.add(' '.join(initials))  # "J P"
                variants.add('.'.join(initials) + '.')  # "J.P."
                variants.add('. '.join(initials) + '.')  # "J. P."
    
    # Remove the original name from variants (we'll add it separately)
    variants.discard(name)
    
    return list(variants)


def get_surname_variants(surname):
    """Generate all variants of a surname with different prefix spacing patterns
    for Irish surnames (O/O'/Ó, Mac/Mc, Ua/Uí, Nic/Ní).
    Preserves multi-word surnames (e.g., "O'Malley Keighran" stays as two words).
    
    Returns dict with prefix info:
    {
        'variants': [list of all variants],
        'canonical': canonical form (for output normalization)
    }
    """
    if not surname:
        return {'variants': [], 'canonical': ''}
    
    # Normalize multiple spaces to single space before processing
    surname = ' '.join(surname.split())
    
    normalized = normalize(surname)
    variants = set([normalized])  # Use set to auto-deduplicate
    canonical = None
    
    # Define prefixes and their properties with related prefix forms
    # Format: (prefix_lowercase, requires_space, canonical_form_with_X, related_prefixes_list)
    prefixes_config = [
        # Irish/Scottish with related forms (longer ones first to avoid partial matches)
        ("mac giolla", True, "Mac Giolla {0}", []),  # Longer prefix, no related forms
        ("mac con", True, "Mac Con {0}", []),        # Longer prefix, no related forms
        ("mac an", True, "Mac an {0}", []),          # Special: "an" not capitalized - NO spacing variants
        ("nic an", True, "Nic an {0}", []),          # Special: "an" not capitalized - NO spacing variants
        ("mhic", True, "Mhic {0}", []),              # Must have space
        ("ua", True, "Ua {0}", []),              
        ("uí", True, "Uí {0}", []),              
        ("nic", False, "Nic {0}", []),           # Canonical with space, variant without
        ("ní", False, "Ní {0}", []),            
        ("mac", False, "Mac {0}", []),                # Canonical with space, variant without
        ("mc", False, "Mc{0}", []),                   # Canonical without space, variant with space
        # O/O'/Ó variants
        ("o'", False, "O'{0}", ["o"]),               # Can generate O variants
        ("ó", True, "Ó {0}", ["o"])                 # Can generate O variants
        # ("o ", True, "O {0}", ["o'", "ó"])          # Can generate O' and Ó variants
    ]

    # Check each prefix (longer ones first to avoid partial matches)
    for prefix, requires_space, canonical_template, related_prefixes in prefixes_config:
        prefix_len = len(prefix)
        
        if normalized.startswith(prefix):
            # Extract the rest of the surname - preserve internal spaces in multi-word surnames
            rest_with_space = normalized[prefix_len:].lstrip()
            
            # Only remove spaces between the prefix and the main part, NOT internal spaces
            # Example: "o'malley keighran" -> rest should be "malley keighran" (keep the space)
            
            # Only process if there's something after the prefix
            if not rest_with_space:
                continue
            
            # For single-letter prefixes (O, U, D, etc.), verify they're actually prefixes
            # by checking if they're followed by space or apostrophe in the original
            if prefix_len == 1:
                original_lower = surname.strip().lower()
                original_lower = original_lower.replace("'", "'")  # Normalize apostrophes
                if original_lower.startswith(prefix):
                    after_prefix = original_lower[1:2]
                    # Single letter prefix must be followed by space or apostrophe
                    if after_prefix not in (' ', "'", ''):
                        # This is just a regular name starting with this letter, not a prefix
                        continue
            
            # Define all prefix variants to generate (main prefix + related prefixes)
            all_prefix_variants = [prefix] + related_prefixes
            
            # Generate variants for each prefix form
            for current_prefix in all_prefix_variants:
                # Get requires_space setting for this variant
                current_requires_space = requires_space
                if current_prefix in ("o'", "mc", "d'"):
                    current_requires_space = False
                elif current_prefix in ("ua", "uí", "nic", "ní", "mac", "ó"):
                    current_requires_space = requires_space
                
                # For variants, preserve multi-word structure
                # Extract first word after prefix (for spacing variants) and keep remaining words
                parts = rest_with_space.split()
                first_word = parts[0] if parts else ""
                remaining_words = " ".join(parts[1:]) if len(parts) > 1 else ""
                
                # Single-letter prefixes like "o" and "ó" MUST have space to avoid ambiguity
                if len(current_prefix) == 1 and current_prefix in ("ó", "o"):
                    # Always require space for single-letter Irish prefixes
                    variants.add(f"{current_prefix} {rest_with_space}")
                # Mac an and Nic an should ONLY have space variant, no no-space variant
                elif current_prefix in ("mac an", "nic an"):
                    variants.add(f"{current_prefix} {rest_with_space}")
                # O' prefix should ONLY generate spaced variants (O' and O with space)
                elif current_prefix == "o'":
                    # Generate O'Name variant (no space after apostrophe)
                    variants.add(f"{current_prefix}{rest_with_space}")
                    # Generate O' Name variant (with space after apostrophe)
                    variants.add(f"{current_prefix} {rest_with_space}")
                    # Also generate "o " (O with space) variant
                    variants.add(f"o {rest_with_space}")
                elif current_requires_space:
                    # Must have space: generate with and without space after prefix
                    # But preserve internal spaces in multi-word surnames
                    variants.add(f"{current_prefix} {rest_with_space}")
                    if remaining_words:
                        variants.add(f"{current_prefix}{first_word} {remaining_words}")
                    else:
                        variants.add(f"{current_prefix}{first_word}")
                else:
                    # Can vary: generate both with and without space after prefix
                    if current_prefix in ("mc", "d'"):
                        # These prefixes typically don't have space in canonical form
                        if remaining_words:
                            variants.add(f"{current_prefix}{first_word} {remaining_words}")
                            variants.add(f"{current_prefix} {first_word} {remaining_words}")
                        else:
                            variants.add(f"{current_prefix}{first_word}")
                            variants.add(f"{current_prefix} {first_word}")
                    else:
                        # Other prefixes typically have space in canonical form
                        variants.add(f"{current_prefix} {rest_with_space}")
                        if remaining_words:
                            variants.add(f"{current_prefix}{first_word} {remaining_words}")
                        else:
                            variants.add(f"{current_prefix}{first_word}")
            
            # Set canonical form (use the original detected prefix form)
            # Preserve multi-word structure in canonical form
            if canonical is None:
                canonical = canonical_template.format(rest_with_space)
            
            break
    
    # If no canonical form was set, use the original
    if canonical is None:
        canonical = surname
    
    return {
        'variants': list(variants),
        'canonical': canonical
    }

def normalize_surname_for_output(surname):
    """Normalize surname to canonical pattern for output using get_surname_variants()."""
    if not surname:
        return surname
    
    result = get_surname_variants(surname)
    canonical = result['canonical']
    
    # Apply proper capitalization to the canonical form
    if canonical:
        # For Irish surnames, use special capitalization rules
        normalized = normalize(surname)
        if any(normalized.startswith(p) for p in ['mc', 'mac', 'nic', 'ní', 'ua', 'uí', "o'", 'ó']):
            return capitalize_irish_surname(canonical)
        
        # For other surnames, standard capitalization
        parts = canonical.split()
        capitalized = ' '.join([p.capitalize() for p in parts])
        return capitalized
    
    return surname.strip()

def get_organizations_from_pure_person(person, is_internal=True):
    """Extract organization UUIDs from Pure person record"""
    internal_orgs = []
    external_orgs = []
    
    if is_internal:
        # For internal persons: extract from various staff/student/visitor associations
        association_types = [
            ("staffOrganizationAssociations", "organization"),
            ("honoraryStaffOrganizationAssociations", "organization"),
            ("visitingScholarOrganizationAssociations", "organization"),
        ]
        
        # Extract UUIDs from all configured association types
        for field_name, org_field in association_types:
            for assoc in person.get(field_name, []):
                org = assoc.get(org_field, {})
                if org.get("uuid") and org.get("systemName") == "Organization":
                    internal_orgs.append(org["uuid"])
        
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
    
    return list(set(internal_orgs)), list(set(external_orgs))  # Return both lists, deduplicated


def build_index_persons(person_list, is_internal=True):
    """
    Build a lookup index with Irish surname variant handling:
    normalized_surname → [uuid1, uuid2, ...]
    For internal persons: only index if visibility.key == "FREE"
    For external persons: include all (no visibility filter)
    Also collects original alternative names and visibility for ALL internal persons.
    Checks both direct and inverse (swapped) name-surname order.
    All output lists (UUIDs, orgs) are deduplicated.
    """
    index = {}
    alt_names_by_uuid = {}  # maps uuid → list of (original_first, original_last)
    internal_orgs_by_uuid = {}  # maps uuid → list of internal organization UUIDs
    external_orgs_by_uuid = {}  # maps uuid → list of external organization UUIDs
    visibility_by_uuid = {}  # maps uuid → visibility key (for ALL internal persons)

    for person in person_list:
        uuid = person.get("uuid")
        if not uuid:
            continue

        # ✅ Always store visibility for internal persons — even if not FREE
        if is_internal:
            visibility = person.get("visibility", {})
            vis_key = visibility.get("key", "")  # e.g., "FREE", "CAMPUS", etc.
            visibility_by_uuid[uuid] = vis_key  

        # Get primary name
        primary_first = person.get("name", {}).get("firstName", "")
        primary_last = person.get("name", {}).get("lastName", "")

        # Collect all first names and last names (including alternatives)
        all_first_names = [primary_first] if primary_first else []
        all_last_names = [primary_last] if primary_last else []

        # Add alternatives from 'names' array
        for name_entry in person.get("names", []):
            name_obj = name_entry.get("name", {})
            if first := name_obj.get("firstName", ""):
                all_first_names.append(first)
            if last := name_obj.get("lastName", ""):
                all_last_names.append(last)

        # If no valid first or last names, skip this person
        if not all_first_names or not all_last_names:
            continue

        # Generate all possible (first, last) pairs and their variants
        all_name_variants = [
            (normalize(first), variant, first, last)
            for first in all_first_names
            for last in all_last_names
            for variant in get_surname_variants(last)['variants']
        ]

        # Index each variant - direct order
        for norm_f, norm_l, orig_f, orig_l in all_name_variants:
            key = (norm_f, norm_l)
            if key not in index:
                index[key] = []
            index[key].append(uuid)
        
        # Also index inverse order (swapped names)
        for norm_f, norm_l, orig_f, orig_l in all_name_variants:
            # Swap: use last as first, first as last
            key_swapped = (norm_l, norm_f)
            if key_swapped not in index:
                index[key_swapped] = []
            index[key_swapped].append(uuid)

        # Store original alternative names (excluding primary) for this UUID
        alt_names = [(orig_f, orig_l) for norm_f, norm_l, orig_f, orig_l in all_name_variants
                     if orig_f != primary_first or orig_l != primary_last]
        if alt_names:
            alt_names_by_uuid[uuid] = list(set(alt_names))  # Deduplicate

        # Store organization UUIDs for this person (separated by type)
        int_orgs, ext_orgs = get_organizations_from_pure_person(person, is_internal=is_internal)
        if int_orgs:
            internal_orgs_by_uuid[uuid] = int_orgs
        if ext_orgs:
            external_orgs_by_uuid[uuid] = ext_orgs

    return index, alt_names_by_uuid, internal_orgs_by_uuid, external_orgs_by_uuid, visibility_by_uuid


def enrich_authors(authors, internal_index, external_index, internal_alt_names=None, external_alt_names=None, internal_internal_orgs=None, internal_external_orgs=None, external_internal_orgs=None, external_external_orgs=None, internal_visibility_by_uuid=None, output_option="all"):
    """
    Enrich authors with Pure match data.
    output_option: "all", "matched_internal", "matched_external", "unmatched", "matched_all_duplicates", "matched_internal_duplicates", "matched_external_duplicates", "matched_internal_external_duplicates"
    """
    enriched = []
    filtered_out_count = 0

    for author in tqdm(authors, desc="Matching Authors", unit="author"):
        first = author.get("firstName", "").strip()
        last = author.get("lastName", "").strip()
        
        # Capitalize first names normally (not using Irish surname rules)
        if first:
            parts = first.split()
            first = ' '.join([capitalize_after_hyphen(p.capitalize()) for p in parts])
        
        # Capitalize last names using Irish surname rules
        if last:
            last = capitalize_after_hyphen(last)

        # ⚠️ Exclude if either firstName or lastName is empty
        if not first or not last:
            filtered_out_count += 1
            continue  # Skip this author

        # Normalize for matching - generate surname variants
        norm_first = normalize(first)
        norm_last_base = normalize(last)
        surname_variants = get_surname_variants(last)['variants']
        
        # Try to find matches with any surname variant
        internal_matches = []
        external_matches = []
        
        # Try each surname variant with direct name order
        for surname_var in surname_variants:
            key = (norm_first, surname_var)
            internal_matches.extend(internal_index.get(key, []))
            external_matches.extend(external_index.get(key, []))
        
        # Also try inverse order (swapped names)
        for surname_var in surname_variants:
            key_swapped = (surname_var, norm_first)
            internal_matches.extend(internal_index.get(key_swapped, []))
            external_matches.extend(external_index.get(key_swapped, []))

        # Deduplicate UUIDs
        internal_matches = list(set(internal_matches))
        external_matches = list(set(external_matches))

# Extract original alternative names used in matches
        alternative_firstnames = set()
        alternative_lastnames = set()

        # Check internal matches
        for uuid in internal_matches:
            if internal_alt_names and uuid in internal_alt_names:
                for orig_first, orig_last in internal_alt_names[uuid]:
                    alt_first = capitalize_after_hyphen(orig_first.capitalize())
                    alt_last = capitalize_after_hyphen(capitalize_irish_surname(orig_last))
                    # Only add if different from primary name
                    if alt_first != first:
                        alternative_firstnames.add(alt_first)
                    if alt_last != last:
                        alternative_lastnames.add(alt_last)

        # Check external matches
        for uuid in external_matches:
            if external_alt_names and uuid in external_alt_names:
                for orig_first, orig_last in external_alt_names[uuid]:
                    alt_first = capitalize_after_hyphen(orig_first.capitalize())
                    alt_last = capitalize_after_hyphen(capitalize_irish_surname(orig_last))
                    # Only add if different from primary name
                    if alt_first != first:
                        alternative_firstnames.add(alt_first)
                    if alt_last != last:
                        alternative_lastnames.add(alt_last)
        
        # ✅ Add first name variants (initials, dots, spaces)
        first_name_variants = get_firstname_variants(first)
        for variant in first_name_variants:
            if variant != first:  # Don't add the original
                alternative_firstnames.add(variant)
        
        # ✅ Normalize the primary lastName to canonical form
        normalized_last_name = capitalize_after_hyphen(normalize_surname_for_output(last))
        
        # ✅ Add all surname variants to alternative last names with proper capitalization
        all_surname_variants = get_surname_variants(last)['variants']
        for variant in all_surname_variants:
            # Capitalize the variant properly (handles Irish prefixes)
            capitalized_variant = capitalize_after_hyphen(capitalize_irish_surname(variant))
            # Only add if it's different from both the original and the normalized form
            if capitalized_variant != last and capitalized_variant != normalized_last_name:
                # Check if this surname has a Mc, Mac, or Nic prefix AND is NOT "Mac an" or "Nic an"
                normalized_variant_lower = normalize(variant).lower()
                is_mac_an_or_nic_an = normalized_variant_lower.startswith('mac an ') or normalized_variant_lower.startswith('nic an ')
                if not is_mac_an_or_nic_an:
                    alternative_lastnames.add(capitalized_variant)
        
        # ✅ Add no-space variant for "Mac" and "Mc" surnames only (e.g., "Mac Aodha" -> "MacAodha", "Mc Crae" -> "McCrae")
        if ' ' in last and any(last.lower().startswith(prefix) for prefix in ["mac ", "mc "]):
            no_space_variant = last.replace(' ', '')
            no_space_capitalized = capitalize_after_hyphen(capitalize_irish_surname(no_space_variant))
            if no_space_capitalized != last and no_space_capitalized != normalized_last_name:
                alternative_lastnames.add(no_space_capitalized)
        
        # ✅ WORKAROUND: If last name starts with Mc, add spacing variant (capitalized properly)
        # E.g., "McCrae" -> add "Mc Crae" (not "Mc crae")
        if len(last) >= 3 and last[0:2].lower() == 'mc' and ' ' not in last:
            # Extract the part after "Mc"
            rest = last[2:]
            # Capitalize the rest properly (handles compound parts like "Crae")
            rest_cap = rest[0].upper() + rest[1:] if len(rest) > 1 else rest.upper()
            spacing_variant = f"Mc {rest_cap}"
            if spacing_variant != last and spacing_variant != normalized_last_name:
                alternative_lastnames.add(spacing_variant)

        if len(last) >= 4 and last[0:3].lower() == 'mac' and not last.lower().endswith(("i", "j")) and ' ' not in last:
            # Extract the part after "Mac"
            rest = last[3:]
            # Capitalize the rest properly (handles compound parts like "Crae")
            rest_cap = rest[0].upper() + rest[1:] if len(rest) > 1 else rest.upper()
            spacing_variant = f"Mac {rest_cap}"
            if spacing_variant != last and spacing_variant != normalized_last_name:
                alternative_lastnames.add(spacing_variant)
        
        # Remove any alternative that's identical to the canonical lastName or original
        alternative_lastnames.discard(normalized_last_name)
        alternative_lastnames.discard(last)
        alternative_firstnames.discard(first)

        # ✅ Normalize the primary lastName to canonical form
        normalized_last_name = capitalize_after_hyphen(normalize_surname_for_output(last))
        
        # Remove any alternative that's identical to the canonical lastName
        alternative_lastnames.discard(normalized_last_name)

        # Get organization UUIDs and visibility for matched persons
        internal_organizations = []
        external_organizations = []
        internal_uuids_with_visibility = []  # ✅ List of {uuid, visibility}

        for uuid in internal_matches:
            # Add internal organizations
            if internal_internal_orgs and uuid in internal_internal_orgs:
                internal_organizations.extend(internal_internal_orgs[uuid])
            # Add external organizations
            if internal_external_orgs and uuid in internal_external_orgs:
                external_organizations.extend(internal_external_orgs[uuid])
            # Add visibility
            if internal_visibility_by_uuid and uuid in internal_visibility_by_uuid:
                visibility = internal_visibility_by_uuid[uuid]
                internal_uuids_with_visibility.append({
                    "uuid": uuid,
                    "visibility": visibility
                })

        for uuid in external_matches:
            # Add internal organizations (rare but possible)
            if external_internal_orgs and uuid in external_internal_orgs:
                internal_organizations.extend(external_internal_orgs[uuid])
            # Add external organizations
            if external_external_orgs and uuid in external_external_orgs:
                external_organizations.extend(external_external_orgs[uuid])

        # Deduplicate organizations
        internal_organizations = list(set(internal_organizations))
        external_organizations = list(set(external_organizations))

        # ✅ Preserve papers as list of {handle, doi} — deduplicate by handle
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


        # Build enriched author
        enriched_author = {
            **author,
            "lastName": normalized_last_name,  # ✅ Use normalized canonical form
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
            "alternativeFirstName": list(alternative_firstnames),
            "alternativeLastName": list(alternative_lastnames)
        }

        # Apply output filter — map 'matched_*' to internal logic
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


def analyse_persons(json_file_path):
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

    args = parser.parse_args()

    # Load data
    input_authors = load_json(DSpace_Authors_JSON)
    internal_persons = load_json(Pure_Internal_JSON)
    external_persons = load_json(Pure_External_JSON)

    # Build lookup tables + collect alternative names, organizations (separated by type), and visibility (for internal only)
    internal_index, internal_alt_names, internal_internal_orgs, internal_external_orgs, internal_visibility_by_uuid = build_index_persons(internal_persons, is_internal=True)
    external_index, external_alt_names, external_internal_orgs, external_external_orgs, external_visibility_by_uuid = build_index_persons(external_persons, is_internal=False)


    # Define output options
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

    # Determine which options to process
    if args.option:
        selected_options = [opt for opt in output_options if opt[0] == args.option]
    else:
        selected_options = output_options

    # Set output directory
    output_dir = args.output_dir if args.output_dir else OUTPUT_DIR
    os.makedirs(output_dir, exist_ok=True)

    for option, description in selected_options:
        print(f"\n--- Processing: {description} ---")
        enriched = enrich_authors(
            input_authors,
            internal_index,
            external_index,
            internal_alt_names,
            external_alt_names,
            internal_internal_orgs,
            internal_external_orgs,
            external_internal_orgs,
            external_external_orgs,
            internal_visibility_by_uuid,  # ✅ Pass visibility map
            output_option=option
        )



        # Build filename: {prefix}_authors_{option}.json
        filename = f"{args.prefix}_{option}_{TODAY}.json"
        filepath = os.path.join(output_dir, filename)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(enriched, f, indent=4, ensure_ascii=False)

        print(f"✅ Output saved to: {filepath}")

        # Analyse this subset
        analyse_persons(filepath)

    print("\n🎉 All outputs generated!")


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


if __name__ == "__main__":
    from datetime import datetime
    main()