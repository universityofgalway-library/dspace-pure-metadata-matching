import os
import json
import requests
import argparse
from datetime import date, datetime
from dotenv import load_dotenv
from tqdm import tqdm

TODAY = date.today().isoformat()

def fetch_all(data_type, api_key="", test=True):
    """
    Fetch all data from the Elsevier Pure API, handling pagination.
    Returns a list of all items.
    """
    all_data = []
    offset = 0
    page_size = 1000

    base_url = 'https://galway-staging.elsevierpure.com/ws/api/' if test == True else 'https://research.universityofgalway.ie/ws/api/'

    headers = {
        "accept": "application/json",
        "api-key": api_key
    }

    # First, get total count to initialize progress bar
    total_count = None
    try:
        first_url = f"{base_url}{data_type}?offset=0&size=1"
        first_response = requests.get(first_url, headers=headers, timeout=30)
        if first_response.status_code == 200:
            total_count = first_response.json().get("count", 0)
    except Exception as e:
        print(f"⚠️ Could not determine total count: {e}")

    # Initialize progress bar
    pbar = tqdm(total=total_count, desc=f"Fetching {data_type}", unit="item", disable=not total_count)

    while True:
        url = f"{base_url}{data_type}?offset={offset}&size={page_size}"

        try:
            response = requests.get(url, headers=headers, timeout=30)
            
            if response.status_code != 200:
                print(f"\n❌ API request failed: {response.status_code} - {response.text}")
                break

            data = response.json()

            # Extract items
            items = data.get("items", [])
            if not items:
                break  # No more data

            all_data.extend(items)

            # Update progress bar
            pbar.update(len(items))

            # Check if we've fetched all records
            if total_count is not None and len(all_data) >= total_count:
                break

            # Move to next page
            offset += page_size

        except requests.exceptions.RequestException as e:
            print(f"\n❌ API request failed at offset {offset}: {e}")
            break

    pbar.close()
    print(f"✅ Total {data_type} fetched: {len(all_data)}")
    return all_data

def save_research_outputs_by_type(items, output_dir, filename_prefix):
    """
    Split research outputs by type and save to separate files.
    """
    today = datetime.now().strftime("%Y-%m-%d")
    base_filename = f"{filename_prefix}_{today}"
    
    # Group by type
    type_groups = {}

    for item in items:
        type_uri = item.get("type", {}).get("uri", "")
        if not type_uri:
            continue

        # Extract type key from URI (e.g., "contributiontojournal")
        parts = type_uri.split("/")
        if len(parts) >= 2:
            type_key = parts[-2]
        else:
            type_key = "unknown"

        if type_key not in type_groups:
            type_groups[type_key] = []
        type_groups[type_key].append(item)

    # Save each type to separate file
    for type_key, type_items in type_groups.items():
        filepath = os.path.join(output_dir, f"{base_filename}_{type_key}.json")
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(type_items, f, indent=2, ensure_ascii=False)
        print(f"📄 Saved {len(type_items)} items of type '{type_key}' to: {filepath}")

    print("🎉 Done!")


if __name__ == "__main__":

    # Load environment variables from .env file
    load_dotenv()

    API_KEY = os.getenv("PURE_API_KEY", "")

    if not API_KEY:
        print("⚠️ WARNING: PURE_API_KEY not found in environment variables.")

    parser = argparse.ArgumentParser(description="Fetch data from Pure API")
    parser.add_argument(
        "--test", 
        type=bool, 
        default=True, 
        help="Get data from UAT (--test True) or Production (--test False)"
    )
    parser.add_argument(
        "--data", 
        required=True,
        choices=["research-outputs", "persons", "external-persons", "journals", 
                 "events", "awards", "organizations", "external-organizations", "publishers"], 
        help="What data to get from Pure"
    )
    parser.add_argument(
        "--output-dir",
        default="./pure_data",
        help="Output directory for saved files (default: ./pure_data)"
    )
    parser.add_argument(
        "--split-by-type",
        action="store_true",
        help="For research-outputs only: split into separate files by type"
    )
    parser.add_argument(
        "--filename-prefix",
        help="Custom filename prefix (default: pure_test_<data_type>)"
    )
    
    args = parser.parse_args()

    # Create output directory
    os.makedirs(args.output_dir, exist_ok=True)

    # Determine filename prefix
    if args.filename_prefix:
        filename_prefix = args.filename_prefix
    else:
        filename_prefix = f"pure_test_{args.data}"

    try:
        # Fetch all data
        all_data = fetch_all(args.data, api_key=API_KEY, test=args.test)
        
        # Save data
        if args.data == "research-outputs" and args.split_by_type:
            # Split research outputs by type
            save_research_outputs_by_type(all_data, args.output_dir, filename_prefix)
        else:
            # Save all in one file
            filepath = os.path.join(args.output_dir, f"{filename_prefix}_{TODAY}.json")
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(all_data, f, indent=2, ensure_ascii=False)
            print(f"📄 Saved {len(all_data)} items to: {filepath}")

    except Exception as e:
        print(f"❌ Error: {e}")