#!/usr/bin/env python3

import os
import re
import csv
import sys
import json
import argparse
from datetime import date, datetime
from collections import defaultdict
from tqdm import tqdm
from dotenv import load_dotenv


# --- CONFIGURATION ---

load_dotenv()

DOI_REGEX = re.compile(r'^(?:https?://)?(?:doi\.org/|doi:)?(10\.\S+)$', re.IGNORECASE)
HANDLE_REGEX = re.compile(r'^(?:https?://hdl\.handle\.net/)?(10379/\S+)$', re.IGNORECASE)

TODAY_DATE = date.today().isoformat()
TODAY = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')

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


# --- HELPER FUNCTIONS ---

def normalize(s):
    return s.strip().lower() if s else ""

def calculate_title_similarity(title1, title2, threshold=0.8):
    """
    Calculate similarity between two titles.
    Returns tuple (similarity_score, is_match)
    """
    if not title1 or not title2:
        return (0.0, False)
    
    # Normalize titles for comparison
    t1 = normalize(title1)
    t2 = normalize(title2)
    
    if t1 == t2:
        return (1.0, True)
    
    # Calculate simple character matching similarity
    longer = max(len(t1), len(t2))
    if longer == 0:
        return (0.0, False)
    
    # Calculate matching characters
    matches = sum(1 for a, b in zip(t1, t2) if a == b)
    
    # Add bonus for common substrings
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
        return value

    return f"https://doi.org/{match.group(1)}"

def normalize_handle(value: str) -> str:
    if not isinstance(value, str):
        return value

    v = value.strip().lower()
    match = HANDLE_REGEX.match(v)

    if not match:
        return value

    return f"http://hdl.handle.net/{match.group(1)}"

def extract_dois_from_uri(uri_str):
    """Extract DOIs from dc.identifier.uri (semicolon-separated)"""
    if not uri_str:
        return []
    uris = [u.strip().lower() for u in uri_str.split(";") if u.strip()]
    dois = []
    for u in uris:
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

def parse_author_names(author_str):
    """
    Parse semicolon-separated author names from DSpace.
    Returns list of tuples: [(lastName, firstName), ...]
    """
    if not author_str:
        return []
    
    authors = []
    for author in author_str.split(";"):
        author = author.strip()
        if not author:
            continue
            
        if "," in author:
            # Format: "Last, First"
            parts = [p.strip() for p in author.split(",", 1)]
            last = parts[0]
            first = parts[1] if len(parts) > 1 else ""
            authors.append((last, first))
        else:
            # Format: "First Last" - assume last word is surname
            parts = author.split()
            if len(parts) >= 2:
                first = " ".join(parts[:-1])
                last = parts[-1]
                authors.append((last, first))
            else:
                # Only one name - treat as last name
                authors.append((author, ""))
    
    return authors


class TeeOutput:
    """Class to duplicate output to both stdout and a log file"""
    def __init__(self, log_file):
        self.terminal = sys.stdout
        self.log = open(log_file, 'w', encoding='utf-8')
    
    def write(self, message):
        self.terminal.write(message)
        self.log.write(message)
    
    def flush(self):
        self.terminal.flush()
        self.log.flush()
    
    def close(self):
        self.log.close()

def main():
    parser = argparse.ArgumentParser(
        description="Extract matched and unmatched DSpace records with metadata"
    )
    parser.add_argument(
        "--dspace-csv",
        required=True,
        help="Path to DSpace CSV file"
    )
    parser.add_argument(
        "--pure-json",
        required=True,
        help="Path to Pure JSON file with research outputs"
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        help="Base output directory where 'matched' and 'unmatched' folders will be created"
    )
    parser.add_argument(
        "--type-filter",
        choices=list(set(get_pure_type_key(uri) for uri in dspace_pure_subtype_map.values())),
        help="Filter by Pure subtype (e.g., 'contributiontojournal'). If specified, creates a single CSV for that type only."
    )
    parser.add_argument(
        "--title-threshold",
        type=float,
        default=0.9,
        help="Title similarity threshold (0-1), default 0.9"
    )
    
    args = parser.parse_args()
    
    # Set up logging
    os.makedirs(args.output_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = os.path.join(args.output_dir, f"record_sorting_log_{timestamp}.txt")
    tee = TeeOutput(log_file)
    sys.stdout = tee
    
    try:
        # Load DSpace CSV
        print(f"Loading DSpace CSV from {args.dspace_csv}...")
        dspace_rows = []
        dspace_fieldnames = []
        with open(args.dspace_csv, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            dspace_fieldnames = reader.fieldnames
            for row in reader:
                dspace_rows.append(row)
        print(f"✅ Loaded {len(dspace_rows)} DSpace records")

        # Load Pure JSON
        print(f"Loading Pure JSON from {args.pure_json}...")
        with open(args.pure_json, 'r', encoding='utf-8') as f:
            pure_items = json.load(f)
        print(f"✅ Loaded {len(pure_items)} Pure records")

        # Index Pure records by identifiers
        print("Indexing Pure records...")
        pure_by_doi = defaultdict(list)
        pure_by_handle = defaultdict(list)
        pure_by_repo_doi = defaultdict(list)
        pure_by_title = defaultdict(list)

        for item in pure_items:
            # Index by DOI
            for ev in item.get("electronicVersions", []):
                doi = ev.get("doi", "")
                if doi:
                    if "10.13025" in doi:
                        pure_by_repo_doi[normalize_doi(doi)].append(item)
                    elif "hdl.handle.net" in doi:
                        pure_by_handle[normalize_handle(doi)].append(item)
                    else:
                        pure_by_doi[normalize_doi(doi)].append(item)

            # Index by links (handles)
            for link in item.get("links", []):
                url = link.get("url", "")
                if url and "hdl.handle.net" in url:
                    pure_by_handle[normalize_handle(url)].append(item)
            
            # Index by title
            title = item.get("title", {}).get("value", "")
            if title:
                pure_by_title[normalize(title)].append(item)

        # Find matched and unmatched records
        print(f"\nMatching DSpace records against Pure...")
        
        # Group records by type
        matched_by_type = defaultdict(list)
        unmatched_by_type = defaultdict(list)
        
        for row in tqdm(dspace_rows, desc="Processing", unit="record"):
            # Get DSpace type
            dspace_type = row.get("dc.type", "").strip().lower()
            pure_type_uri = dspace_pure_subtype_map.get(dspace_type, "/dk/atira/pure/researchoutput/researchoutputtypes/othercontribution/other")
            type_key = get_pure_type_key(pure_type_uri)
            
            # Apply type filter if specified
            if args.type_filter and type_key != args.type_filter:
                continue

            matched_records = []

            # Try to match by Publisher DOI
            publisher_doi = row.get("dc.identifier.doi", "").strip()
            if publisher_doi:
                normalized_doi = normalize_doi(publisher_doi)
                if normalized_doi in pure_by_doi:
                    matched_records.extend(pure_by_doi[normalized_doi])

            # Try to match by Repository DOI
            if not matched_records:
                repo_dois = extract_dois_from_uri(row.get("dc.identifier.uri", ""))
                for repo_doi in repo_dois:
                    normalized_repo_doi = normalize_doi(repo_doi)
                    if normalized_repo_doi in pure_by_repo_doi:
                        matched_records.extend(pure_by_repo_doi[normalized_repo_doi])
                        break

            # Try to match by Handle
            if not matched_records:
                handles = extract_handles_from_uri(row.get("dc.identifier.uri", ""))
                for handle in handles:
                    normalized_handle = normalize_handle(handle)
                    if normalized_handle in pure_by_handle:
                        matched_records.extend(pure_by_handle[normalized_handle])
                        break

            # Try to match by Title
            if not matched_records:
                title = row.get("dc.title", "").strip()
                if title:
                    normalized_title = normalize(title)
                    # Try exact match
                    if normalized_title in pure_by_title:
                        matched_records.extend(pure_by_title[normalized_title])
                    else:
                        # Try similarity matching
                        best_match = None
                        best_similarity = 0
                        for pure_item in pure_items:
                            pure_title = pure_item.get("title", {}).get("value", "")
                            if pure_title:
                                pure_subtitle = pure_item.get("subTitle", {}).get("value", "")
                                if pure_subtitle:
                                    pure_title += f": {pure_subtitle}"
                                similarity, is_match = calculate_title_similarity(
                                    title, pure_title, args.title_threshold
                                )
                                if is_match and similarity > best_similarity:
                                    best_match = pure_item
                                    best_similarity = similarity
                        
                        if best_match:
                            matched_records = [best_match]

            # Categorize as matched or unmatched
            if matched_records:
                matched_by_type[type_key].append(row)
            else:
                unmatched_by_type[type_key].append(row)

        # Save results
        total_matched = sum(len(rows) for rows in matched_by_type.values())
        total_unmatched = sum(len(rows) for rows in unmatched_by_type.values())
        
        print(f"\n✅ Found {total_matched} matched records")
        print(f"✅ Found {total_unmatched} unmatched records")
        
        # If type filter is specified, save single CSV
        if args.type_filter:
            output_file = os.path.join(args.output_dir, f"{args.type_filter}_{TODAY}.csv")
            os.makedirs(args.output_dir, exist_ok=True)
            
            rows_to_save = unmatched_by_type.get(args.type_filter, [])
            if rows_to_save:
                with open(output_file, 'w', newline='', encoding='utf-8') as f:
                    writer = csv.DictWriter(f, fieldnames=dspace_fieldnames)
                    writer.writeheader()
                    writer.writerows(rows_to_save)
                print(f"\n📄 Filtered CSV saved to: {output_file}")
                print(f"   Records: {len(rows_to_save)}")
            else:
                print(f"\n⚠️ No unmatched records found for type: {args.type_filter}")
        
        else:
            # Save matched records by type
            matched_dir = os.path.join(args.output_dir, "matched")
            os.makedirs(matched_dir, exist_ok=True)
            
            print("\n📁 Saving matched records by type:")
            for type_key, rows in sorted(matched_by_type.items()):
                if rows:
                    output_file = os.path.join(matched_dir, f"{type_key}_{TODAY}.csv")
                    with open(output_file, 'w', newline='', encoding='utf-8') as f:
                        writer = csv.DictWriter(f, fieldnames=dspace_fieldnames)
                        writer.writeheader()
                        writer.writerows(rows)
                    print(f"   {type_key}: {len(rows)} records → {output_file}")
            
            # Save unmatched records by type
            unmatched_dir = os.path.join(args.output_dir, "unmatched")
            os.makedirs(unmatched_dir, exist_ok=True)
            
            print("\n📁 Saving unmatched records by type:")
            for type_key, rows in sorted(unmatched_by_type.items()):
                if rows:
                    output_file = os.path.join(unmatched_dir, f"{type_key}_{TODAY}.csv")
                    with open(output_file, 'w', newline='', encoding='utf-8') as f:
                        writer = csv.DictWriter(f, fieldnames=dspace_fieldnames)
                        writer.writeheader()
                        writer.writerows(rows)
                    print(f"   {type_key}: {len(rows)} records → {output_file}")
            
            # Print summary statistics
            print("\n📊 Summary by type:")
            all_types = set(matched_by_type.keys()) | set(unmatched_by_type.keys())
            for type_key in sorted(all_types):
                matched_count = len(matched_by_type.get(type_key, []))
                unmatched_count = len(unmatched_by_type.get(type_key, []))
                total = matched_count + unmatched_count
                match_pct = (matched_count / total * 100) if total > 0 else 0
                print(f"   {type_key}: {matched_count}/{total} matched ({match_pct:.1f}%)")
        print(f"\n📝 Log saved to: {log_file}")
    finally:
        # Restore stdout and close log file
        sys.stdout = tee.terminal
        tee.close()

if __name__ == "__main__":
    main()