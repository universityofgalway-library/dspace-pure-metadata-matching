#!/usr/bin/env python3
"""
extract_errors_for_xml_reupload.py

Given a Pure "publication import" XML file and a Pure upload error-log CSV,
build a new XML file containing only the top-level research-output entries
(book, chapterInBook, contributionToJournal, etc.) whose id matches an id
found in the error log. This new file is meant to be re-uploaded after the
underlying issue that caused the errors has been fixed.

Matching logic
---------------
- XML entries are matched by the `id` attribute on each direct child of the
  <publications> root element, e.g. <book id="5407170" subType="book">.
- Error-log entries are matched by extracting the id from the free-text
  log/description column, where it always appears in a phrase like:
      Importing content with source id '5418807'
  A regex is used to find this pattern; it does not depend on column
  position, so it works even if the CSV column order changes.

The script does not invent or guess any ids: it only ever copies XML
elements whose id was both present in the XML file AND found via that exact
regex in the error log. Any error-log id that has no matching XML element,
and any XML element whose id never appears in the error log, are reported
but never fabricated or silently dropped.

Usage
-----
    python extract_errors_for_xml_reupload.py \
        --xml temp_pure_import_2026-07-22.xml \
        --errors "Errors_-_Research_output_29238750-1784815497120.csv" \
        --output reupload.xml \
        [--status-filter ERROR] \
        [--column "Title and description"]

If --status-filter is given, only rows whose Status column contains that
string (case-insensitive) are considered. By default all rows in the error
file are considered, since an "errors" export is normally already filtered
to error rows.

If --column is given, the id-extraction regex is only applied to that
column (by header name). By default all columns of each row are searched,
which is robust to unknown/changing column layouts.
"""

import argparse
import csv
import io
import os
import re
import sys

from lxml import etree

ID_PATTERN = re.compile(r"Importing content with source id '(\d+)'")


def read_csv_text(path):
    """Read the CSV file, tolerating non-UTF-8 encodings (common in Pure
    exports on Windows, which are frequently cp1252/Latin-1)."""
    with open(path, "rb") as f:
        raw = f.read()
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    # Last resort: decode with replacement so the script never crashes,
    # but warn loudly since some characters may be corrupted.
    print(
        "WARNING: could not decode CSV with utf-8/cp1252/latin-1 cleanly; "
        "falling back to utf-8 with replacement characters.",
        file=sys.stderr,
    )
    return raw.decode("utf-8", errors="replace")


def extract_error_ids(csv_path, status_filter=None, column=None):
    """Return (ordered list of unique ids found, number of matching rows,
    number of matching rows with no id found)."""
    text = read_csv_text(csv_path)
    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration:
        return [], 0, 0

    status_idx = None
    column_idx = None
    for i, name in enumerate(header):
        if name.strip().lower() == "status":
            status_idx = i
        if column is not None and name.strip() == column:
            column_idx = i

    if column is not None and column_idx is None:
        raise SystemExit(
            f"ERROR: --column '{column}' not found in CSV header: {header}"
        )

    ids_in_order = []
    seen = set()
    rows_considered = 0
    rows_without_id = 0

    for row in reader:
        if not row:
            continue
        if status_filter is not None and status_idx is not None:
            status_val = row[status_idx] if status_idx < len(row) else ""
            if status_filter.lower() not in status_val.lower():
                continue

        rows_considered += 1

        if column_idx is not None:
            haystack = row[column_idx] if column_idx < len(row) else ""
        else:
            haystack = "\n".join(row)

        m = ID_PATTERN.search(haystack)
        if m:
            found_id = m.group(1)
            if found_id not in seen:
                seen.add(found_id)
                ids_in_order.append(found_id)
        else:
            rows_without_id += 1

    return ids_in_order, rows_considered, rows_without_id


def build_reupload_xml(xml_path, error_ids):
    parser = etree.XMLParser(remove_blank_text=False, strip_cdata=False)
    tree = etree.parse(xml_path, parser)
    root = tree.getroot()

    # Map id -> element, only for direct children of the root that carry an
    # id attribute (i.e. the research-output entries themselves).
    id_to_element = {}
    for child in root:
        if not isinstance(child.tag, str):
            continue  # skip comments/PIs
        elem_id = child.get("id")
        if elem_id is not None:
            id_to_element[elem_id] = child

    # Build a new root with the same tag/namespace/nsmap/attributes as the
    # original, but no children yet.
    new_root = etree.Element(root.tag, attrib=root.attrib, nsmap=root.nsmap)

    matched_ids = []
    unmatched_ids = []
    for error_id in error_ids:
        elem = id_to_element.get(error_id)
        if elem is not None:
            # Deep copy to avoid mutating the original tree.
            import copy

            new_root.append(copy.deepcopy(elem))
            matched_ids.append(error_id)
        else:
            unmatched_ids.append(error_id)

    new_tree = etree.ElementTree(new_root)
    return new_tree, matched_ids, unmatched_ids, len(id_to_element)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--xml", required=True, help="Path to the original Pure import XML file")
    ap.add_argument("--errors", required=True, help="Path to the error-log CSV file")
    ap.add_argument(
        "--output",
        default=None,
        help="Path to write the filtered reupload XML file. Defaults to the input "
        "XML's directory/filename with 'reupload_' prefixed.",
    )
    ap.add_argument(
        "--status-filter",
        default=None,
        help="Only consider rows whose Status column contains this text (case-insensitive). "
        "By default all rows are considered.",
    )
    ap.add_argument(
        "--column",
        default=None,
        help="Only search this column (by exact header name) for the id pattern. "
        "By default all columns are searched.",
    )
    args = ap.parse_args()

    if args.output is None:
        xml_dir, xml_name = os.path.split(args.xml)
        args.output = os.path.join(xml_dir, f"reupload_{xml_name}")

    error_ids, rows_considered, rows_without_id = extract_error_ids(
        args.errors, status_filter=args.status_filter, column=args.column
    )

    if rows_without_id:
        print(
            f"WARNING: {rows_without_id} row(s) in the error log matched the status filter "
            f"but did not contain a recognizable \"Importing content with source id '...'\" phrase; "
            "they were skipped.",
            file=sys.stderr,
        )

    new_tree, matched_ids, unmatched_ids, total_xml_entries = build_reupload_xml(
        args.xml, error_ids
    )

    new_tree.write(
        args.output,
        xml_declaration=True,
        encoding="UTF-8",
        standalone=True,
        pretty_print=False,
    )

    print(f"Error log rows considered:      {rows_considered}")
    print(f"Unique ids found in error log:  {len(error_ids)}")
    print(f"Entries in source XML:          {total_xml_entries}")
    print(f"Entries copied to reupload XML: {len(matched_ids)}")
    if unmatched_ids:
        print(
            f"Ids from the error log with NO matching entry in the XML "
            f"({len(unmatched_ids)}):",
            file=sys.stderr,
        )
        for uid in unmatched_ids:
            print(f"  - {uid}", file=sys.stderr)
    print(f"Wrote {len(matched_ids)} entries to {args.output}")


if __name__ == "__main__":
    main()