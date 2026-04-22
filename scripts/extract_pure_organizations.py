import json
import os
from datetime import date

# --- CONFIGURATION ---
TODAY = date.today().isoformat()

INTERNAL_ORG_JSON = "./pure_entities/pure_organizations_2026-04-22.json"
EXTERNAL_ORG_JSON = "./pure_entities/pure_external-organizations_2026-04-22.json"
OUTPUT_JSON = f"./pure_entities/organizations_mapping_{TODAY}.json"

def extract_names(org_data, is_internal):
    """
    Extract all name variations from an organization record.
    Returns a list of unique names.
    """
    names = []
    
    # Get primary name
    if "name" in org_data:
        name_obj = org_data["name"]
        if isinstance(name_obj, dict):
            # Extract all language variants
            for lang, name_value in name_obj.items():
                if name_value and name_value.strip():
                    names.append(name_value.strip())
        elif isinstance(name_obj, str):
            if name_obj.strip():
                names.append(name_obj.strip())
    
    # For internal organizations, also get name variants
    if is_internal and "nameVariants" in org_data:
        for variant in org_data["nameVariants"]:
            value_obj = variant.get("value", {})
            if isinstance(value_obj, dict):
                for lang, name_value in value_obj.items():
                    if name_value and name_value.strip():
                        names.append(name_value.strip())
            elif isinstance(value_obj, str):
                if value_obj.strip():
                    names.append(value_obj.strip())
    
    # Remove duplicates while preserving order
    seen = set()
    unique_names = []
    for name in names:
        if name not in seen:
            seen.add(name)
            unique_names.append(name)
    
    return unique_names


def process_organization(org_data, is_internal):
    """
    Process a single organization record and extract required fields.
    Returns a dict with the mapping structure.
    """
    # Extract all names
    all_names = extract_names(org_data, is_internal)
    
    # Get visibility
    visibility = org_data.get("visibility", {})
    visibility_key = visibility.get("key", "") if isinstance(visibility, dict) else ""
    
    mapping = {
        "pureId": org_data.get("pureId"),
        "uuid": org_data.get("uuid"),
        "name": all_names,
        "internal": is_internal,
        "external": not is_internal,
        "visibility": visibility_key
    }

    return mapping


def main():
    print("=" * 60)
    print("Organization Mapping Generator")
    print("=" * 60)
    
    # Load internal organizations
    print(f"\n📂 Loading internal organizations from: {INTERNAL_ORG_JSON}")
    try:
        with open(INTERNAL_ORG_JSON, 'r', encoding='utf-8') as f:
            internal_orgs = json.load(f)
        print(f"✅ Loaded {len(internal_orgs)} internal organizations")
    except FileNotFoundError:
        print(f"⚠️  File not found: {INTERNAL_ORG_JSON}")
        internal_orgs = []
    except json.JSONDecodeError as e:
        print(f"❌ Error parsing JSON: {e}")
        internal_orgs = []
    
    # Load external organizations
    print(f"\n📂 Loading external organizations from: {EXTERNAL_ORG_JSON}")
    try:
        with open(EXTERNAL_ORG_JSON, 'r', encoding='utf-8') as f:
            external_orgs = json.load(f)
        print(f"✅ Loaded {len(external_orgs)} external organizations")
    except FileNotFoundError:
        print(f"⚠️  File not found: {EXTERNAL_ORG_JSON}")
        external_orgs = []
    except json.JSONDecodeError as e:
        print(f"❌ Error parsing JSON: {e}")
        external_orgs = []
    
    # Process all organizations
    print(f"\n🔄 Processing organizations...")
    mapping = []
    
    # Process internal organizations
    for org in internal_orgs:
        try:
            mapped_org = process_organization(org, is_internal=True)
            mapping.append(mapped_org)
        except Exception as e:
            print(f"⚠️  Error processing internal org {org.get('uuid', '')}: {e}")
    
    # Process external organizations
    for org in external_orgs:
        try:
            mapped_org = process_organization(org, is_internal=False)
            mapping.append(mapped_org)
        except Exception as e:
            print(f"⚠️  Error processing external org {org.get('uuid', '')}: {e}")
    
    print(f"✅ Processed {len(mapping)} total organizations")
    print(f"   - Internal: {sum(1 for org in mapping if org['internal'])}")
    print(f"   - External: {sum(1 for org in mapping if org['external'])}")
    
    # Create output directory if needed
    output_dir = os.path.dirname(OUTPUT_JSON)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
    
    # Write output
    print(f"\n💾 Writing mapping to: {OUTPUT_JSON}")
    with open(OUTPUT_JSON, 'w', encoding='utf-8') as f:
        json.dump(mapping, f, indent=2, ensure_ascii=False)
    
    print(f"✅ Done! Mapping saved successfully.")
    
    # Print statistics
    print("\n" + "=" * 60)
    print("Statistics:")
    print("=" * 60)
    
    # Visibility breakdown
    visibility_counts = {}
    for org in mapping:
        vis = org.get("visibility", "")
        visibility_counts[vis] = visibility_counts.get(vis, 0) + 1
    
    print("\nVisibility breakdown:")
    for vis, count in sorted(visibility_counts.items(), key=lambda x: x[1], reverse=True):
        print(f"  {vis}: {count}")

    
    # Sample entries
    print("\n" + "=" * 60)
    print("Sample entries (first 3):")
    print("=" * 60)
    for i, org in enumerate(mapping[:3], 1):
        print(f"\n{i}. {org.get('name', '')}")
        print(f"   UUID: {org.get('uuid', '')}")
        print(f"   Internal: {org.get('internal', False)}")
        print(f"   Visibility: {org.get('visibility', '')}")


if __name__ == "__main__":
    main()