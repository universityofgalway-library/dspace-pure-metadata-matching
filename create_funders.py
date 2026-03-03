import csv
import json
import sys
from datetime import date
from pathlib import Path

punc = set('''—!–¿()-[]{};:'"‘’“”‐\,<>./?@#$%^&=+|£€*_~®™©0123456789''')

def main(csv_path: str, json_path: str):
    json_path = Path(json_path)
    
    # Load organisation names into a set for O(1) lookup
    with open(json_path, encoding="utf-8") as f:
        orgs = json.load(f)
    
    if not isinstance(orgs, list):
        orgs = [orgs]
    
    known_names = {
        name.strip().lower()
        for org in orgs
        for name in org.get("name", [])
    }

    # Read funders from CSV
    with open(csv_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        raw_funders = set()
        for row in reader:
            cell = row.get("dc.contributor.funder", "").strip()
            if cell:
                for funder in cell.split(";"):
                    funder = funder.strip()
                    if funder:
                        raw_funders.add(funder)

    # Find funders not in the known organisations
    missing = [f for f in sorted(raw_funders) if f.lower() not in known_names]
    missing_filtered = set([f for f in missing if not any(char in punc for char in f)])

    if not missing:
        print("✅ All funders already exist in the organisation file.")
        return

    # Build output records
    records = [
        {
            "name": {"en_IE": funder},
            "type": {
                "uri": "/dk/atira/pure/ueoexternalorganisation/ueoexternalorganisationtypes/ueoexternalorganisation/researchFundingBody"
            },
            "visibility": {"key": "FREE"},
            "workflow": {"step": "forApproval"},
            "systemName": "ExternalOrganization"
        }
        for funder in missing_filtered
    ]

    output_path = json_path.parent / f"funders_to_upload_{date.today().isoformat()}.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(records, f, indent=2, ensure_ascii=False)

    print(f"✅ {len(records)} missing funder(s) written to: {output_path}")
    for f in missing_filtered:
        print(f"   - {f}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python script.py <path/to/data.csv> <path/to/organisations.json>")
        sys.exit(1)
    main(sys.argv[1], sys.argv[2])