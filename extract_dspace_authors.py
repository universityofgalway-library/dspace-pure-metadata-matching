import csv
import json
from collections import defaultdict


def normalize_component(name):
    """
    Normalize a name component into readable 'Title Case' while preserving:
    - initials like P., J.P.
    - O' prefixes
    - hyphens
    - particles like 'de', 'van', 'von', 'der'
    """
    if not name:
        return ""

    parts = name.strip().split()
    fixed = []

    for p in parts:
        # Handle initials with dots, e.g. "J.", "A.B."
        if "." in p:
            fixed.append(p.upper())  # Initials traditionally uppercase
            continue

        # Handle hyphens: "smith-jones" → "Smith-Jones"
        if "-" in p:
            sub = p.split("-")
            fixed.append("-".join(s.capitalize() for s in sub))
            continue

        # Handle O' names: o'brien → O'Brien
        if p.lower().startswith("o'") and len(p) > 2:
            fixed.append("O'" + p[2:].capitalize())
            continue

        # Default capitalization
        fixed.append(p.capitalize())

    return " ".join(fixed)


def normalize_full_name(first, last):
    """
    Normalize full first & last name components into readable, consistent format.
    """
    return normalize_component(first), normalize_component(last)


def normalize_name_key(first, last):
    """
    Internal normalization for merging purposes:
    - lowercased
    - spaces collapsed
    """
    first = " ".join(first.strip().lower().split())
    last = " ".join(last.strip().lower().split())
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


def parse_names(name_field):
    """Parse 'Last, First; Last2, First2' into (first, last) tuples."""
    if not name_field:
        return []

    names = []
    parts = [p.strip() for p in name_field.split(";") if p.strip()]

    for p in parts:
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


def process_csv(input_csv, output_json, strict_names, normalize_names_flag):
    """
    strict_names    -> apply filtering rules
    normalize_names_flag -> merge based on normalized names
    """
    authors = {}

    with open(input_csv, newline='', encoding="utf-8") as f:
        reader = csv.DictReader(f)

        for row in reader:
            raw_authors = parse_names(row.get("dc.contributor.author", ""))
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

            for first, last in raw_authors:

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
                        "title": title  # ✅ Added title
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

    print(f"{len(result)} authors found. JSON written to {output_json}")

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description = "Extract authors + handle links from CSV metadata.")
    parser.add_argument("input_csv", help = "Path to input CSV file")
    parser.add_argument("output_json", help = "Path to output JSON file")

    parser.add_argument(
        "--strict-names",
        action = "store_true",
        help = "Discard authors with missing names or dotted short names"
    )

    parser.add_argument(
        "--no-normalization",
        action = "store_true",
        help = "Disable name normalization (case, whitespace)."
    )

    args = parser.parse_args()

    process_csv(
        args.input_csv,
        args.output_json,
        strict_names = args.strict_names,
        normalize_names_flag = not args.no_normalization
    )
