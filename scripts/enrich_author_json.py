import json
import argparse
from pathlib import Path


def build_log_lookup(log_data: list, entity_type: str) -> dict:
    """
    Build a lookup dict from log entries filtered by entity type.

    Key  : "lastname, firstname" lowercased exactly as it appears in the log
    Value: uuid string
    """
    lookup = {}
    for entry in log_data:
        if not entry.get("success") or not entry.get("uuid"):
            continue
        if entry.get("data") != entity_type:
            continue
        name = entry.get("name", "")
        if name:
            lookup[name.lower()] = entry["uuid"]
    return lookup


def enrich_authors(authors_file: str, log_file: str, entity_type: str) -> None:
    print(f"Loading authors file : {authors_file}")
    with open(authors_file, "r", encoding="utf-8") as fh:
        authors: list[dict] = json.load(fh)

    print(f"Loading log file     : {log_file}")
    with open(log_file, "r", encoding="utf-8") as fh:
        log_data: list[dict] = json.load(fh)

    log_lookup = build_log_lookup(log_data, entity_type)
    print(f"Log entries loaded   : {len(log_lookup)} (type='{entity_type}', success=True)\n")

    matched = 0
    unmatched = 0
    skipped = 0

    for author in authors:
        if author.get("internal", False) or author.get("external", False):
            skipped += 1
            continue

        # Build "lastName, firstName" exactly as the log stores it
        author_key = f"{author.get('lastName', '')}, {author.get('firstName', '')}".lower()

        uuid = log_lookup.get(author_key)
        display = f"{author.get('firstName', '')} {author.get('lastName', '')}".strip()

        if uuid:
            author["external"] = True
            if "externalUUIDs" not in author:
                author["externalUUIDs"] = []
            if uuid not in author["externalUUIDs"]:
                author["externalUUIDs"].append(uuid)
            matched += 1
            print(f"  ✓  {display:40s}  →  {uuid}")
        else:
            unmatched += 1
            print(f"  ✗  {display}  (no match found)")

    output_path = Path(authors_file).parent / f"updated_{Path(authors_file).name}"
    with open(output_path, "w", encoding="utf-8") as fh:
        json.dump(authors, fh, indent=2, ensure_ascii=False)

    print(f"\n{'=' * 60}")
    print("SUMMARY")
    print(f"{'=' * 60}")
    print(f"  Total authors         : {len(authors)}")
    print(f"  Matched & updated     : {matched}")
    print(f"  No match found        : {unmatched}")
    print(f"  Skipped (already i/e) : {skipped}")
    print(f"\n  Output written to     : {output_path}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Enrich an authors JSON file with UUIDs from a creation log."
    )
    parser.add_argument("--authors-file", required=True)
    parser.add_argument("--log-file", required=True)
    parser.add_argument("--entity-type", default="external-persons")
    args = parser.parse_args()
    enrich_authors(args.authors_file, args.log_file, args.entity_type)


if __name__ == "__main__":
    main()