import os
import json
import sys
import argparse
from datetime import datetime


def search_records(records, title=None, uuid=None, pure_id=None):
    """Search records by title (case-insensitive partial match), exact UUID, or exact pure ID."""
    results = []
    for record in records:
        if uuid:
            if record.get("uuid", "").lower() == uuid.lower():
                results.append(record)
        elif pure_id is not None:
            if record.get("pureId") == pure_id:
                results.append(record)
        elif title:
            record_title = record.get("title", {}).get("value", "")
            if title.lower() in record_title.lower():
                results.append(record)
    return results


def main():
    parser = argparse.ArgumentParser(
        description="Query a Pure research output JSON file by title or UUID."
    )
    parser.add_argument("input", help="Path to the input JSON file.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--title", "-t", help="Title string to search for (case-insensitive, partial match).")
    group.add_argument("--uuid", "-u", help="Exact UUID to search for.")
    group.add_argument("--pure-id", "-p", type=int, help="Exact Pure ID to search for.")
    args = parser.parse_args()

    if not os.path.isfile(args.input):
        print(f"❌ File not found: {args.input}")
        sys.exit(1)

    print(f"Loading {args.input}...")
    with open(args.input, "r", encoding="utf-8") as f:
        records = json.load(f)

    if not isinstance(records, list):
        print("❌ Expected a JSON array at the top level.")
        sys.exit(1)

    print(f"✅ Loaded {len(records)} records. Searching...")

    results = search_records(records, title=args.title, uuid=args.uuid, pure_id=args.pure_id)

    if not results:
        print("⚠️  No matching records found.")
        sys.exit(0)

    print(f"✅ Found {len(results)} matching record(s).")

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    output_dir = os.path.dirname(os.path.abspath(args.input))
    output_path = os.path.join(output_dir, f"search_result_{timestamp}.json")

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    print(f"💾 Results saved to: {output_path}")


if __name__ == "__main__":
    main()