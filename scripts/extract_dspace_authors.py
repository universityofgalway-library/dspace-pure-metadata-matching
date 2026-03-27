import csv
import json
from collections import defaultdict


# --- CONFIGURATION ---
# DC types to exclude from author extraction (case-insensitive)
# User can edit this list to add/remove types
EXCLUDED_DC_TYPES = [
    "doctoral thesis",
    "master thesis"
]

# Contributor fields to extract (all contributors except funders)
CONTRIBUTOR_FIELDS = [
    "dc.contributor.author",
    "dc.contributor.advisor", 
    "dc.contributor.editor",
    "dc.contributor.illustrator",
    "dc.contributor.translator"
]

# Stopwords to filter out institutional/organizational names (case-insensitive)
# Authors with these words in their first or last name will be excluded
# Note: Names containing ANY digits are automatically filtered out
# User can edit this list to add/remove stopwords
NAME_STOPWORDS = [
    "university",
    "college",
    "academy",
    "institute",
    "association"
]


def normalize_full_name(first, last):
    """
    Normalize full first & last name components into readable, consistent format.
    Simply applies title case capitalization.
    """
    if not first:
        first = ""
    if not last:
        last = ""
    
    # Replace curvy apostrophes with straight ones
    first = first.replace("'", "'").replace("'", "'")
    last = last.replace("'", "'").replace("'", "'")
    
    return first.strip().title(), last.strip().title()


def normalize_name_key(first, last):
    """
    Internal normalization for merging purposes:
    - lowercased
    - spaces collapsed
    """
    first = " ".join(first.strip().lower().split())
    last = " ".join(last.strip().lower().split())
    return first, last


def fix_misplaced_prefix(first, last):
    """
    Fix cases where Irish/Scottish surname prefixes are erroneously in the first name.
    
    Example: first="Sarah Mc", last="Garrigle" → first="Sarah", last="McGarrigle"
             first="John O'", last="Brien" → first="John", last="O'Brien"
             first="Mary Mac", last="Donald" → first="Mary", last="Mac Donald"
    
    Prefixes without space after: Mc, O'
    Prefixes with space after: Mac, Ó, Ní, Nic, Mhic, De, Mac Giolla, Mac Con, Uí, Mac an, Nic an, Ua
    """
    if not first or not last:
        return first, last
    
    # ✅ Replace curvy apostrophes with straight ones
    first = first.replace("'", "'").replace("'", "'")
    last = last.replace("'", "'").replace("'", "'")
    
    # Define prefixes: (prefix_pattern, needs_space_after)
    # Order matters: check longer prefixes first to avoid partial matches
    prefixes = [
        ("Mac Giolla", True),   # Longer compound prefixes first
        ("Mac Con", True),
        ("Mac an", True),
        ("Nic an", True),
        ("Mhic", True),
        ("Mac", True),
        ("Nic", True),
        ("Mc", False),
        ("O'", False),
        ("Ó", True),
        ("Ní", True),
        ("De", True),
        ("Uí", True),
        ("Ua", True),
    ]
    
    first_parts = first.strip().split()
    
    # Check if the last word(s) of first name match any prefix
    for prefix, needs_space in prefixes:
        prefix_parts = prefix.split()
        prefix_len = len(prefix_parts)
        
        # Check if the last N parts of first name match this prefix (case-insensitive)
        if len(first_parts) >= prefix_len + 1:  # Need at least one name part before prefix
            potential_prefix_parts = first_parts[-prefix_len:]
            potential_prefix = " ".join(potential_prefix_parts)
            
            if potential_prefix.lower() == prefix.lower():
                # Found a match! Split the first name
                actual_first = " ".join(first_parts[:-prefix_len])
                
                # Preserve original capitalization of the prefix from the input
                prefix_part = potential_prefix
                
                # Reconstruct the last name with the prefix
                # Preserve the original capitalization of 'last' as well
                if needs_space:
                    corrected_last = f"{prefix_part} {last}"
                else:
                    corrected_last = f"{prefix_part}{last}"
                
                return actual_first, corrected_last
    
    return first, last


def valid_author_name(first, last, strict=True):
    """
    Filtering rules for strict mode:
    1. Reject if first or last name is empty
    2. Reject if the first name contains '.' and is < 5 chars long
    """
    if not strict:
        return True

    if not first.strip() or not last.strip():
        return False
        
    if len(first.strip()) < 2 or len(last.strip()) < 2:
        return False

    if "." in first and len(first) < 9:
        return False
        
    if " " in first and len(first) < 7:
        return False

    return True


def contains_stopword(first, last, stopwords):
    """
    Check if first or last name contains any institutional stopwords or digits.
    Returns True if a stopword is found (should be excluded).
    Case-insensitive comparison.
    """
    import re
    
    if not stopwords:
        stopwords = []
    
    first_lower = first.strip().lower()
    last_lower = last.strip().lower()
    
    # ✅ Check for any digits in the name
    if re.search(r'\d', first_lower) or re.search(r'\d', last_lower):
        return True
    
    # Check for stopwords
    for stopword in stopwords:
        stopword_lower = stopword.strip().lower()
        if stopword_lower in first_lower or stopword_lower in last_lower:
            return True
    
    return False


def parse_names(name_field):
    """Parse 'Last, First; Last2, First2' into (first, last) tuples."""
    if not name_field:
        return []

    names = []
    parts = [p.strip() for p in name_field.split(";") if p.strip()]

    for p in parts:
        # ✅ Replace curvy apostrophes with straight ones
        p = p.replace("'", "'").replace("'", "'")
        
        if "," in p:
            last, first = [x.strip() for x in p.split(",", 1)]
        else:
            tokens = p.split()
            if len(tokens) > 1:
                first = tokens[0]
                last = " ".join(tokens[1:])
            else:
                first = p
                last = ""
        
        # ✅ Fix misplaced prefixes before adding to names list
        first, last = fix_misplaced_prefix(first, last)
        names.append((first, last))

    return names


def extract_handles(uri_field):
    """Extract only Handle links."""
    if not uri_field:
        return []

    handles = []
    urls = [u.strip() for u in uri_field.split(";") if u.strip()]

    for u in urls:
        if "hdl.handle.net" in u:
            handles.append(u)

    return handles


def should_exclude_item(dc_type, excluded_types):
    """
    Check if the item should be excluded based on its DC type.
    Case-insensitive comparison.
    """
    if not dc_type:
        return False
    
    dc_type_lower = dc_type.strip().lower()
    excluded_lower = [t.strip().lower() for t in excluded_types]
    
    return dc_type_lower in excluded_lower


def process_csv(input_csv, output_json, strict_names, normalize_names_flag, excluded_types=None, name_stopwords=None):
    """
    Extract authors from multiple contributor fields, excluding specified DC types.
    
    strict_names         -> apply filtering rules
    normalize_names_flag -> merge based on normalized names
    excluded_types       -> list of DC types to exclude (uses EXCLUDED_DC_TYPES if None)
    name_stopwords       -> list of stopwords to filter institutional names (uses NAME_STOPWORDS if None)
    """
    if excluded_types is None:
        excluded_types = EXCLUDED_DC_TYPES
    
    if name_stopwords is None:
        name_stopwords = NAME_STOPWORDS
    
    # Initialize all counters at the start
    authors = {}
    excluded_count = 0
    processed_count = 0
    stopword_filtered_count = 0

    with open(input_csv, newline='', encoding="utf-8") as f:
        reader = csv.DictReader(f)

        for row in reader:
            # ✅ Check if this item should be excluded based on DC type
            dc_type = row.get("dc.type", "")
            if should_exclude_item(dc_type, excluded_types):
                excluded_count += 1
                continue
            
            processed_count += 1
            
            # ✅ Extract contributors from all specified fields
            all_contributors = []
            for field in CONTRIBUTOR_FIELDS:
                field_contributors = parse_names(row.get(field, ""))
                all_contributors.extend(field_contributors)
            
            # Remove duplicates while preserving order
            seen = set()
            unique_contributors = []
            for first, last in all_contributors:
                key = (first.lower().strip(), last.lower().strip())
                if key not in seen:
                    seen.add(key)
                    unique_contributors.append((first, last))
            
            handles = extract_handles(row.get("dc.identifier.uri", ""))
            publisher_doi = row.get("dc.identifier.doi", "").strip()
            title = row.get("dc.title", "").strip()

            # ✅ Only use publisher DOI if it's not a repository DOI (doesn't start with 10.13025)
            if publisher_doi and publisher_doi.startswith("10.13025"):
                publisher_doi = ""  # Exclude repository DOIs

            # ✅ Ensure DOI has https://doi.org/ prefix
            if publisher_doi and not publisher_doi.startswith("https://doi.org/"):
                # If it starts with "doi:" or just "10.", convert to full URL
                if publisher_doi.startswith("doi:"):
                    publisher_doi = "https://doi.org/" + publisher_doi[4:]
                else:
                    publisher_doi = "https://doi.org/" + publisher_doi

            if not handles:
                continue

            for first, last in unique_contributors:

                # ✅ Filter out institutional/organizational names
                if contains_stopword(first, last, name_stopwords):
                    stopword_filtered_count += 1
                    continue

                # Apply strict filtering
                if not valid_author_name(first, last, strict=strict_names):
                    continue

                # Key for merging
                if normalize_names_flag:
                    key = normalize_name_key(first, last)
                else:
                    key = (first.strip(), last.strip())

                # Normalize names for final JSON output
                clean_first, clean_last = normalize_full_name(first, last)

                if key not in authors:
                    authors[key] = {
                        "firstName": clean_first,
                        "lastName": clean_last,
                        "papers": []
                    }

                # Add each handle with its DOI and title
                for handle in handles:
                    authors[key]["papers"].append({
                        "handle": handle,
                        "doi": publisher_doi if publisher_doi else "",
                        "title": title
                    })

    # Convert to final list
    result = []
    for data in authors.values():
        result.append({
            "firstName": data["firstName"],
            "lastName": data["lastName"],
            "papers": data["papers"]  # Already list of {handle, doi, title}
        })

    # Sort alphabetically
    def sort_key(x):
        return (x["lastName"].lower(), x["firstName"].lower())

    result = sorted(result, key=sort_key)

    with open(output_json, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=4, ensure_ascii=False)

    print(f"\n=== Extraction Summary ===")
    print(f"Items excluded (by DC type): {excluded_count}")
    print(f"Items processed: {processed_count}")
    print(f"Authors filtered (stopwords): {stopword_filtered_count}")
    print(f"Authors found: {len(result)}")
    print(f"JSON written to: {output_json}")
    if excluded_types:
        print(f"\nExcluded DC types: {', '.join(excluded_types)}")
    if name_stopwords:
        print(f"Name stopwords: {', '.join(name_stopwords)}")

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Extract authors from multiple contributor fields in CSV metadata.",
        epilog=f"Default excluded DC types: {', '.join(EXCLUDED_DC_TYPES)}"
    )
    parser.add_argument("input_csv", help="Path to input CSV file")
    parser.add_argument("output_json", help="Path to output JSON file")

    parser.add_argument(
        "--strict-names",
        action="store_true",
        help="Discard authors with missing names or dotted short names"
    )

    parser.add_argument(
        "--no-normalization",
        action="store_true",
        help="Disable name normalization (case, whitespace)."
    )
    
    parser.add_argument(
        "--exclude-types",
        nargs="+",
        metavar="TYPE",
        help=f"DC types to exclude (default: {', '.join(EXCLUDED_DC_TYPES)}). Use this to override the default list."
    )
    
    parser.add_argument(
        "--no-exclusions",
        action="store_true",
        help="Disable DC type exclusions (process all items)"
    )
    
    parser.add_argument(
        "--stopwords",
        nargs="+",
        metavar="WORD",
        help=f"Stopwords to filter institutional names (default: {', '.join(NAME_STOPWORDS)}). Use this to override the default list."
    )
    
    parser.add_argument(
        "--no-stopword-filter",
        action="store_true",
        help="Disable stopword filtering (include all names)"
    )

    args = parser.parse_args()
    
    # Determine which types to exclude
    if args.no_exclusions:
        excluded_types = []
    elif args.exclude_types:
        excluded_types = args.exclude_types
    else:
        excluded_types = EXCLUDED_DC_TYPES
    
    # Determine which stopwords to use
    if args.no_stopword_filter:
        name_stopwords = []
    elif args.stopwords:
        name_stopwords = args.stopwords
    else:
        name_stopwords = NAME_STOPWORDS

    process_csv(
        args.input_csv,
        args.output_json,
        strict_names=args.strict_names,
        normalize_names_flag=not args.no_normalization,
        excluded_types=excluded_types,
        name_stopwords=name_stopwords
    )