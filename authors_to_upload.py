import json
from datetime import date

TODAY = date.today().isoformat()

def extract_external_persons(input_file, output_file):
    """
    Extract authors where both internal and external are false,
    and format them as External Persons for database upload.
    
    Args:
        input_file: Path to input JSON file with authors data
        output_file: Path to output JSON file for External Persons
    """
    
    # Read input JSON
    with open(input_file, 'r', encoding='utf-8') as f:
        authors = json.load(f)
    
    external_persons = []
    
# Filter and transform authors
    for author in authors:
        # Check if both internal and external are false
        if not author.get('internal', True) and not author.get('external', True):
            # Create External Person record
            external_person = {
                "name": {
                    "firstName": author.get('firstName', ''),
                    "lastName": author.get('lastName', '')
                },
                # "alternativeFirstName": author.get('alternativeFirstName', []),
                # "alternativeLastName": author.get('alternativeLastName', []),
                "type": {
                    "uri": "/dk/atira/pure/externalperson/externalpersontypes/externalperson/externalperson",
                    "term": {
                        "en_IE": "External person"
                    }
                },
                "workflow": {
                    "step": "forApproval",
                    "description": {
                        "en_IE": "For approval"
                    }
                },
                "systemName": "ExternalPerson"
            }
            
            external_persons.append(external_person)
    
    # Write output JSON
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(external_persons, f, indent=2, ensure_ascii=False)
    
    print(f"Processed {len(authors)} authors")
    print(f"Extracted {len(external_persons)} external persons")
    print(f"Output written to: {output_file}")
    
    return external_persons


if __name__ == "__main__":
    # Example usage
    input_file = "./matching_test/matched_authors/temp_authors_all_2026-01-22.json"  # Replace with your input file path
    output_file = f"./matching_test/matched_authors/authors_to_upload_{TODAY}.json"  # Replace with desired output path
    
    external_persons = extract_external_persons(input_file, output_file)
    
    # Optional: Print first record as sample
    if external_persons:
        print("\nSample output (first record):")
        print(json.dumps(external_persons[0], indent=2, ensure_ascii=False))