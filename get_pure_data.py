
import os
import json
import requests
import argparse
from dotenv import load_dotenv

def fetch_all(data_type, api_key="", test=True):
    """
    Fetch all persons from the Elsevier Pure API, handling pagination.
    Returns a list of all person items.
    """
    all_data = []
    offset = 0
    page_size = 1000

    base_url = 'https://galway-staging.elsevierpure.com/ws/api/' if test == True else 'https://research.universityofgalway.ie/ws/api/'

    headers = {
        "accept": "application/json",
        "api-key": api_key
    }

    while True:
        url = f"{base_url}{data_type}?offset={offset}&size={page_size}"
        print(f"Fetching {data_type} from offset {offset}...")

        response = requests.get(url, headers=headers)

        if response.status_code != 200:
            raise Exception(f"API request failed: {response.status_code} - {response.text}")

        data = response.json()

        # Extract items
        items = data.get("items", [])
        if not items:
            break  # No more data

        all_data.extend(items)

        # Check if we've fetched all records
        total_count = data.get("count", 0)
        if len(all_data) >= total_count:
            break

        # Move to next page
        offset += page_size

    print(f"Total {data_type} fetched: {len(all_data)}")
    return all_data

# Example usage:
if __name__ == "__main__":

    # Load environment variables from .env file
    load_dotenv()

    API_KEY = os.getenv("PURE_API_KEY", "")

    if not API_KEY:
        print("⚠️ WARNING: PURE_API_KEY not found in environment variables.")

    parser = argparse.ArgumentParser(description="Upload matched/unmatched JSONs to Pure")
    parser.add_argument("--test", type=bool, default=True, help="Get data from UAT (--test True) or Production (--test False)")
    parser.add_argument("--data", choices=["persons", "external-persons", "journals", "events", "awards",
                                           "organizations", "external-organizations", "publishers"], 
                        help="What data to get from Pure")
    args = parser.parse_args()

    if args.data:
        try:
            all_data = fetch_all(args.data, api_key=API_KEY, test=args.test)
            # Optionally save to file
            with open(f"./pure_entities/pure_test_{args.data}.json", "w", encoding="utf-8") as f:
                json.dump(all_data, f, indent=2, ensure_ascii=False)

        except Exception as e:
            print(f"Error: {e}")