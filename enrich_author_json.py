import json
import argparse
from pathlib import Path

def normalize_name(name):
    """Normalize name for comparison by removing dots and extra spaces."""
    return name.replace('.', '').strip().lower()

def format_author_name(author):
    """
    Format author name as 'FirstName LastName' for matching.
    Handles both regular firstName and alternativeFirstName.
    """
    names = []
    
    # Get all possible first names
    first_names = [author.get('firstName', '')]
    if author.get('alternativeFirstName'):
        first_names.extend(author.get('alternativeFirstName', []))
    
    # Get all possible last names
    last_names = [author.get('lastName', '')]
    if author.get('alternativeLastName'):
        last_names.extend(author.get('alternativeLastName', []))
    
    # Create all possible name combinations
    for first in first_names:
        for last in last_names:
            if first and last:
                names.append(f"{first} {last}")
    
    return names

def build_log_lookup(log_data):
    """
    Build a lookup dictionary from log entries.
    Key: normalized name, Value: uuid
    """
    lookup = {}
    
    for entry in log_data:
        if entry.get('success') and entry.get('uuid'):
            name = entry.get('name', '')
            if name:
                normalized = normalize_name(name)
                lookup[normalized] = entry['uuid']
    
    return lookup

def enrich_authors(authors_file, log_file):
    """
    Enrich authors JSON with UUIDs from log file.
    
    Args:
        authors_file: Path to the authors JSON file to update
        log_file: Path to the log JSON file with UUIDs
    """
    # Load the authors file
    print(f"Loading authors file: {authors_file}")
    with open(authors_file, 'r', encoding='utf-8') as f:
        authors = json.load(f)
    
    # Load the log file
    print(f"Loading log file: {log_file}")
    with open(log_file, 'r', encoding='utf-8') as f:
        log_data = json.load(f)
    
    # Build lookup from log data
    log_lookup = build_log_lookup(log_data)
    print(f"Built lookup with {len(log_lookup)} entries from log file\n")
    
    # Track statistics
    matched_count = 0
    unmatched_count = 0
    already_external_count = 0
    
    # Process each author
    for i, author in enumerate(authors):
        # Check if both internal and external are false
        if not author.get('internal', False) and not author.get('external', False):
            # Get all possible name combinations for this author
            author_names = format_author_name(author)
            
            # Try to find a match in the log
            matched_uuid = None
            matched_name = None
            
            for author_name in author_names:
                normalized = normalize_name(author_name)
                if normalized in log_lookup:
                    matched_uuid = log_lookup[normalized]
                    matched_name = author_name
                    break
            
            if matched_uuid:
                # Update the author record
                author['external'] = True
                if 'externalUUIDs' not in author:
                    author['externalUUIDs'] = []
                if matched_uuid not in author['externalUUIDs']:
                    author['externalUUIDs'].append(matched_uuid)
                
                matched_count += 1
                print(f"✓ Matched: {author.get('firstName', '')} {author.get('lastName', '')} → UUID: {matched_uuid}")
            else:
                unmatched_count += 1
                print(f"✗ No match: {author.get('firstName', '')} {author.get('lastName', '')}")
        else:
            already_external_count += 1
    
    # Save the updated file
    authors_path = Path(authors_file)
    output_file = authors_path.parent / f"updated_{authors_path.name}"
    
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(authors, f, indent=2, ensure_ascii=False)
    
    # Print summary
    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    print(f"Total authors processed: {len(authors)}")
    print(f"Authors matched and updated: {matched_count}")
    print(f"Authors without matches: {unmatched_count}")
    print(f"Authors already internal/external: {already_external_count}")
    print(f"\nOutput file: {output_file}")

def main():
    parser = argparse.ArgumentParser(
        description='Enrich authors JSON with UUIDs from log file'
    )
    parser.add_argument(
        '--authors-file',
        required=True,
        help='Path to the authors JSON file to update'
    )
    parser.add_argument(
        '--log-file',
        required=True,
        help='Path to the log JSON file containing UUIDs'
    )
    
    args = parser.parse_args()
    
    enrich_authors(args.authors_file, args.log_file)

if __name__ == "__main__":
    main()