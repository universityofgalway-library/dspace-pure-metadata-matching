"""
prepare_uploads.py – Prepare entity records for database upload.

Supports four entity types as individual commands:
  authors     – Extract external persons from an author JSON file
  funders     – Find funders in a CSV missing from an organisations JSON
  journals    – Compare a journal CSV against an existing journals JSON
  publishers  – Find publishers in a CSV missing from an organisations JSON

Any subset can be run together with the `run` command, or all four at once
with the `all` command.

Run `python prepare_uploads.py <command> --help` for per-command usage.
"""

import argparse
import csv
import json
import sys
from datetime import date, datetime
from pathlib import Path
import logging

# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

TODAY_ISO = date.today().isoformat()
NOW_TS = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

_COMPARISON_STRIP = str.maketrans("", "", """—!–¿()-[]{};:'"''""‐\\,<>./?@#$%^&=+|£€*_~®™©0123456789""")


def _make_logger(label: str, output_dir: Path, log_dir: Path | None = None) -> logging.Logger:
    """
    Create a logger that writes to stdout AND a timestamped file under
    <log_dir>/<label>_<timestamp>.log.
    log_dir defaults to <output_dir>/logs.
    """
    log_dir = log_dir or (output_dir / "logs")
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{label}_{NOW_TS}.log"

    logger = logging.getLogger(f"{label}_{NOW_TS}")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    fmt = logging.Formatter("%(asctime)s  %(levelname)-8s  %(message)s",
                            datefmt="%Y-%m-%d %H:%M:%S")

    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logger.addHandler(sh)

    logger.info("Log file: %s", log_path)
    return logger


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _write_json(path: Path, data: list, label: str,
                logger: logging.Logger | None = None,
                sample: bool = False) -> None:
    """Serialise *data* to *path* as indented UTF-8 JSON and log a summary."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
    msg = f"{len(data)} {label} written to: {path}"
    if logger:
        logger.info("✅  %s", msg)
    else:
        print(f"✅  {msg}")
    if sample and data:
        sample_msg = f"Sample output (first record):\n{json.dumps(data[0], indent=2, ensure_ascii=False)}"
        if logger:
            logger.info(sample_msg)
        else:
            print(sample_msg)


def _detect_delimiter(path: Path) -> str:
    """
    Return the delimiter for a CSV file by sniffing the first non-empty line.
    Falls back to comma if the sniffer cannot decide.
    """
    with open(path, encoding="utf-8", newline="") as fh:
        for line in fh:
            if line.strip():
                try:
                    dialect = csv.Sniffer().sniff(line, delimiters=",\t")
                    return dialect.delimiter
                except csv.Error:
                    break
    return ","


def _read_csv(path: Path) -> list[dict]:
    """Read a CSV file with auto-detected delimiter and return rows as dicts."""
    delimiter = _detect_delimiter(path)
    with open(path, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh, delimiter=delimiter))


def _load_orgs_json(path: Path) -> list[dict]:
    """Load an organisations JSON file, always returning a list."""
    with open(path, encoding="utf-8") as fh:
        orgs = json.load(fh)
    return orgs if isinstance(orgs, list) else [orgs]


def _known_names_from_publishers(publishers: list[dict]) -> set[str]:
    """
    Build a normalised name lookup set from a Pure publishers JSON,
    where each record has a plain string 'name' field.
    """
    def _normalise(s: str) -> str:
        return s.strip().lower().translate(_COMPARISON_STRIP)

    return {
        _normalise(p["name"])
        for p in publishers
        if isinstance(p.get("name"), str)
    }


def _known_names_from_orgs(orgs: list[dict]) -> set[str]:
    """
    Build a normalised name lookup set from a list of organisation dicts.
    Names are lowercased and stripped of punctuation for comparison only.
    Handles both string names and localised dicts (e.g. {"en_IE": "Name"}).
    """
    def _normalise(s: str) -> str:
        return s.strip().lower().translate(_COMPARISON_STRIP)

    known: set[str] = set()
    for org in orgs:
        raw = org.get("name", [])
        if isinstance(raw, list):
            for n in raw:
                if isinstance(n, str):
                    known.add(_normalise(n))
                elif isinstance(n, dict):
                    for v in n.values():
                        if isinstance(v, str):
                            known.add(_normalise(v))
        elif isinstance(raw, dict):
            for v in raw.values():
                if isinstance(v, str):
                    known.add(_normalise(v))
        elif isinstance(raw, str):
            known.add(_normalise(raw))
    return known


def _extract_missing_org_names(
    rows: list[dict],
    column: str,
    known_names: set[str],
    logger: logging.Logger | None = None,
) -> list[str]:
    """
    Collect unique, non-empty values from *column* across *rows*, split on
    semicolons, and strip whitespace.

    Matching against known_names is case- and punctuation-insensitive.
    Original casing and punctuation are preserved in the returned names.

    Names longer than 10 whitespace-separated words are skipped with a warning.
    All other unmatched names are included regardless of punctuation.
    Returns a sorted list of new names.
    """
    def _normalise(s: str) -> str:
        return s.strip().lower().translate(_COMPARISON_STRIP)

    raw: set[str] = set()
    for row in rows:
        cell = row.get(column, "").strip()
        if cell:
            for item in cell.split(";"):
                item = item.strip()
                if item:
                    raw.add(item)

    missing: list[str] = []
    for name in sorted(raw):
        if _normalise(name) in known_names:
            continue
        word_count = len(name.split())
        if word_count > 10:
            msg = f"Skipping name longer than 10 words ({word_count} words): {name!r}"
            if logger:
                logger.warning(msg)
            else:
                print(f"⚠️  {msg}")
            continue
        missing.append(name)

    return sorted(missing)


# ---------------------------------------------------------------------------
# Authors
# ---------------------------------------------------------------------------


def run_authors(
    input_path: Path,
    output_path: Path | None = None,
    log_dir: Path | None = None,
    sample: bool = False,
) -> list[dict]:
    output_path = output_path or input_path.parent / f"authors_to_upload_{TODAY_ISO}.json"
    logger = _make_logger("authors", output_path.parent, log_dir)

    with open(input_path, encoding="utf-8") as fh:
        authors = json.load(fh)

    external_persons = []
    for author in authors:
        if not author.get("internal", True) and not author.get("external", True):
            external_persons.append({
                "name": {
                    "firstName": author.get("firstName", ""),
                    "lastName": author.get("lastName", ""),
                },
                "type": {
                    "uri": (
                        "/dk/atira/pure/externalperson/externalpersontypes/externalperson/externalperson"
                    ),
                    "term": {"en_IE": "External person"},
                },
                "workflow": {"step": "forApproval"},
                "systemName": "ExternalPerson",
            })

    logger.info("Processed %d authors", len(authors))
    _write_json(output_path, external_persons, "external persons", logger, sample)
    return external_persons


def cmd_authors(args: argparse.Namespace) -> None:
    run_authors(
        input_path=Path(args.input),
        output_path=Path(args.output) if args.output else None,
        log_dir=Path(args.log_dir) if getattr(args, "log_dir", None) else None,
        sample=args.sample,
    )


# ---------------------------------------------------------------------------
# Funders
# ---------------------------------------------------------------------------


def run_funders(
    csv_path: Path,
    orgs_path: Path,
    output_path: Path | None = None,
    log_dir: Path | None = None,
    column: str = "dc.contributor.funder",
    sample: bool = False,
) -> list[dict]:
    output_path = output_path or orgs_path.parent / f"funders_to_upload_{TODAY_ISO}.json"
    logger = _make_logger("funders", output_path.parent, log_dir)

    orgs = _load_orgs_json(orgs_path)
    known_names = _known_names_from_orgs(orgs)
    rows = _read_csv(csv_path)
    missing = _extract_missing_org_names(rows, column, known_names, logger)

    if not missing:
        logger.info("✅  All funders already exist in the organisation file.")
        return []

    records = [
        {
            "name": {"en_IE": name},
            "type": {
                "uri": (
                    "/dk/atira/pure/ueoexternalorganisation/ueoexternalorganisationtypes/ueoexternalorganisation/researchFundingBody"
                )
            },
            "visibility": {"key": "FREE"},
            "workflow": {"step": "forApproval"},
            "systemName": "ExternalOrganization",
        }
        for name in missing
    ]

    _write_json(output_path, records, "missing funders", logger, sample)
    for name in missing:
        logger.info("   - %s", name)
    return records


def cmd_funders(args: argparse.Namespace) -> None:
    run_funders(
        csv_path=Path(args.csv),
        orgs_path=Path(args.organisations),
        output_path=Path(args.output) if args.output else None,
        log_dir=Path(args.log_dir) if getattr(args, "log_dir", None) else None,
        sample=getattr(args, "sample", False),
    )


# ---------------------------------------------------------------------------
# Journals helpers
# ---------------------------------------------------------------------------


def _parse_issns(raw: str) -> list[str]:
    if not raw or not raw.strip():
        return []
    return [s.strip() for s in raw.split(";") if s.strip()]


def _journal_template(title: str, issns: list[str], publisher_uuid: str) -> dict:
    journal: dict = {
        "titles": [{"title": title}],
        "type": {
            "uri": "/dk/atira/pure/journal/journaltypes/journal/journal",
            "term": {"en_IE": "Journal"},
        },
        "workflow": {
            "step": "forApproval",
            "description": {"en_IE": "For approval"},
        },
        "systemName": "Journal",
    }
    if issns:
        journal["issns"] = [{"issn": issns[0]}]
        if len(issns) >= 2:
            journal["additionalSearchableIssns"] = [
                {"typeDiscriminator": "ElectronicISSN", "issn": issns[1]}
            ]
    if publisher_uuid:
        journal["publisher"] = {"systemName": "Publisher", "uuid": publisher_uuid}
    return journal


def _existing_issns(journal: dict) -> set[str]:
    issns: set[str] = set()
    for obj in journal.get("issns", []):
        if "issn" in obj:
            issns.add(obj["issn"])
    for obj in journal.get("additionalSearchableIssns", []):
        if "issn" in obj:
            issns.add(obj["issn"])
    return issns


def _journal_updates(
    existing: dict, csv_issns: list[str], publisher_uuid: str
) -> dict | None:
    updates: dict = {}

    existing_pub_uuid = existing.get("publisher", {}).get("uuid")
    if publisher_uuid and existing_pub_uuid != publisher_uuid:
        updates["publisher"] = {"systemName": "Publisher", "uuid": publisher_uuid}

    new_issns = [i for i in csv_issns if i not in _existing_issns(existing)]
    if new_issns:
        has_primary = bool(existing.get("issns"))
        has_electronic = bool(existing.get("additionalSearchableIssns"))
        for issn in new_issns:
            if not has_primary:
                updates["issns"] = [{"issn": issn}]
                has_primary = True
            elif not has_electronic:
                updates["additionalSearchableIssns"] = [
                    {"typeDiscriminator": "ElectronicISSN", "issn": issn}
                ]
                has_electronic = True

    return updates or None


# ---------------------------------------------------------------------------
# Journals
# ---------------------------------------------------------------------------


def run_journals(
    csv_path: Path,
    existing_path: Path,
    output_create: Path | None = None,
    output_update: Path | None = None,
    log_dir: Path | None = None,
    sample: bool = False,
) -> tuple[list[dict], list[dict]]:
    output_create = output_create or existing_path.parent / f"journals_to_create_{NOW_TS}.json"
    output_update = output_update or existing_path.parent / f"journals_to_update_{NOW_TS}.json"
    logger = _make_logger("journals", output_create.parent, log_dir)

    try:
        with open(existing_path, encoding="utf-8") as fh:
            existing_journals = json.load(fh)
    except FileNotFoundError:
        sys.exit(f"Error: existing journals file '{existing_path}' not found.")
    except json.JSONDecodeError as exc:
        sys.exit(f"Error: invalid JSON in '{existing_path}': {exc}")

    uuid_lookup: dict[str, dict] = {}
    title_lookup: dict[str, dict] = {}
    for j in existing_journals:
        if "uuid" in j:
            uuid_lookup[j["uuid"]] = j
        for t in j.get("titles", []):
            if "title" in t:
                title_lookup[t["title"].lower().strip()] = j

    logger.info("Loaded %d existing journals", len(existing_journals))

    to_create: list[dict] = []
    to_update: list[dict] = []

    try:
        rows = _read_csv(csv_path)
        for row in rows:
            title = row.get("journal_title", "").strip()
            if not title:
                continue
            issns = _parse_issns(row.get("journal_issn", ""))
            journal_uuid = row.get("journal_uuid", "").strip()
            publisher_uuid = row.get("publisher_uuid", "").strip()

            if journal_uuid:
                if journal_uuid in uuid_lookup:
                    upd = _journal_updates(uuid_lookup[journal_uuid], issns, publisher_uuid)
                    if upd:
                        to_update.append({"uuid": journal_uuid, **upd})
                else:
                    logger.warning("Journal UUID %s not found in existing journals", journal_uuid)
            else:
                existing = title_lookup.get(title.lower().strip())
                if existing:
                    upd = _journal_updates(existing, issns, publisher_uuid)
                    if upd:
                        to_update.append({"uuid": existing["uuid"], **upd})
                else:
                    to_create.append(_journal_template(title, issns, publisher_uuid))

    except FileNotFoundError:
        sys.exit(f"Error: CSV file '{csv_path}' not found.")
    except Exception as exc:
        import traceback
        traceback.print_exc()
        sys.exit(f"Error processing CSV: {exc}")

    _write_json(output_create, to_create, "journals to create", logger, sample)
    _write_json(output_update, to_update, "journals to update", logger, sample)

    return to_create, to_update


def cmd_journals(args: argparse.Namespace) -> None:
    run_journals(
        csv_path=Path(args.csv),
        existing_path=Path(args.existing),
        output_create=Path(args.output_create) if args.output_create else None,
        output_update=Path(args.output_update) if args.output_update else None,
        log_dir=Path(args.log_dir) if getattr(args, "log_dir", None) else None,
        sample=getattr(args, "sample", False),
    )


# ---------------------------------------------------------------------------
# Publishers
# ---------------------------------------------------------------------------


def run_publishers(
    csv_path: Path,
    publishers_path: Path,
    output_path: Path | None = None,
    log_dir: Path | None = None,
    column: str = "dc.publisher",
    sample: bool = False,
) -> list[dict]:
    output_path = output_path or publishers_path.parent / f"publishers_to_upload_{TODAY_ISO}.json"
    logger = _make_logger("publishers", output_path.parent, log_dir)

    with open(publishers_path, encoding="utf-8") as fh:
        publishers = json.load(fh)
    publishers = publishers if isinstance(publishers, list) else [publishers]

    known_names = _known_names_from_publishers(publishers)
    rows = _read_csv(csv_path)
    missing = _extract_missing_org_names(rows, column, known_names, logger)

    if not missing:
        logger.info("✅  All publishers already exist in the publishers file.")
        return []

    records = [
        {
            "name": name,
            "type": {
                "uri": "/dk/atira/pure/publisher/publishertypes/publisher/publisher",
                "term": {"en_IE": "Publisher"},
            },
            "workflow": {"step": "forApproval"},
            "systemName": "Publisher",
        }
        for name in missing
    ]

    _write_json(output_path, records, "missing publishers", logger, sample)
    for name in missing:
        logger.info("   - %s", name)
    return records


def cmd_publishers(args: argparse.Namespace) -> None:
    run_publishers(
        csv_path=Path(args.csv),
        publishers_path=Path(args.publishers),
        output_path=Path(args.output) if args.output else None,
        log_dir=Path(args.log_dir) if getattr(args, "log_dir", None) else None,
        sample=getattr(args, "sample", False),
    )

# ---------------------------------------------------------------------------
# run / all  (multi-command dispatch)
# ---------------------------------------------------------------------------


def cmd_run(args: argparse.Namespace) -> None:
    commands = args.commands
    required: dict[str, list[tuple[str, str]]] = {
        "authors":    [("authors_input",    "--authors-input")],
        "funders":    [("csv",              "--csv"),
                       ("organisations",    "--organisations")],
        "publishers": [("csv",              "--csv"),
                       ("publishers",       "--publishers")],
        "journals":   [("csv",              "--csv"),
                       ("journals","--journals")],
    }
    errors = []
    for cmd in commands:
        for attr, flag in required.get(cmd, []):
            if not getattr(args, attr, None):
                errors.append(f"  '{cmd}' requires {flag}")
    if errors:
        sys.exit("Missing arguments for requested commands:\n" + "\n".join(errors))

    log_dir = Path(args.log_dir) if getattr(args, "log_dir", None) else None

    if "authors" in commands:
        run_authors(
            input_path=Path(args.authors_input),
            output_path=Path(args.authors_output) if args.authors_output else None,
            log_dir=log_dir,
            sample=getattr(args, "sample", False),
        )
    if "funders" in commands:
        run_funders(
            csv_path=Path(args.csv),
            orgs_path=Path(args.organisations),
            output_path=Path(args.funders_output) if args.funders_output else None,
            log_dir=log_dir,
            sample=getattr(args, "sample", False),
        )
    if "publishers" in commands:
        run_publishers(
            csv_path=Path(args.csv),
            publishers_path=Path(args.publishers),
            output_path=Path(args.publishers_output) if args.publishers_output else None,
            log_dir=log_dir,
            sample=getattr(args, "sample", False),
        )
    if "journals" in commands:
        run_journals(
            csv_path=Path(args.csv),
            existing_path=Path(args.journals),
            output_create=Path(args.journals_output_create) if args.journals_output_create else None,
            output_update=Path(args.journals_output_update) if args.journals_output_update else None,
            log_dir=log_dir,
            sample=getattr(args, "sample", False),
        )


def cmd_all(args: argparse.Namespace) -> None:
    """Shortcut: run all four commands at once."""
    args.commands = ["authors", "funders", "publishers", "journals"]
    cmd_run(args)


# ---------------------------------------------------------------------------
# CLI parser
# ---------------------------------------------------------------------------

def _add_log_dir_arg(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--log-dir", dest="log_dir",
        help="Directory for log files (default: <output_dir>/logs).",
    )


def _add_csv_arg(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "csv",
        help="Path to the input CSV file (comma or tab separator auto-detected).",
    )


def _add_orgs_arg(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "organisations",
        help=(
            "Path to a Pure organisations JSON file (internal or external) "
            "used for name matching."
        ),
    )


def _add_sample_arg(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--sample", action="store_true",
        help="Print the first output record to stdout after writing.",
    )


def _add_multi_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--csv", help="CSV file used by funders, publishers, and/or journals.")
    p.add_argument("--organisations", help="Organisations JSON used by funders.")
    p.add_argument("--publishers", help="Publishers JSON used by publishers command.")  # ← changed
    p.add_argument("--authors-input",  dest="authors_input",  help="Input authors JSON.")
    p.add_argument("--authors-output", dest="authors_output", help="Output path for authors.")
    p.add_argument("--funders-output",    dest="funders_output",    help="Output path for funders.")
    p.add_argument("--publishers-output", dest="publishers_output", help="Output path for publishers.")
    p.add_argument("--journals",      dest="journals",      help="Existing journals JSON.")
    p.add_argument("--journals-output-create", dest="journals_output_create", help="Output path for journals to create.")
    p.add_argument("--journals-output-update", dest="journals_output_update", help="Output path for journals to update.")
    _add_log_dir_arg(p)
    _add_sample_arg(p)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="prepare_uploads",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # ------------------------------------------------------------------ authors
    p_auth = sub.add_parser(
        "authors",
        help="Extract external-person records from an author JSON file.",
        description=(
            "Reads a JSON array of author objects and extracts those where both "
            "'internal' and 'external' flags are False, formatting them as "
            "ExternalPerson records ready for upload."
        ),
    )
    _add_log_dir_arg(p_auth)
    _add_sample_arg(p_auth)
    p_auth.add_argument("input", help="Path to the input authors JSON file.")
    p_auth.add_argument(
        "-o", "--output",
        help="Output path (default: <input_dir>/authors_to_upload_<today>.json).",
    )
    p_auth.set_defaults(func=cmd_authors)

    # ------------------------------------------------------------------ funders
    p_fund = sub.add_parser(
        "funders",
        help="Find funders in a CSV that are missing from an organisations JSON.",
        description=(
            "Reads funder names from a CSV column dc.contributor.funder (semicolon-separated within cells), "
            "compares them case-insensitively against an organisations JSON, strips names "
            "containing punctuation, and writes missing ones as ExternalOrganization "
            "(researchFundingBody) records. The CSV delimiter is auto-detected."
        ),
    )
    _add_csv_arg(p_fund)
    _add_orgs_arg(p_fund)
    _add_log_dir_arg(p_fund)
    _add_sample_arg(p_fund)
    p_fund.add_argument(
        "-o", "--output",
        help="Output path (default: <organisations_dir>/funders_to_upload_<today>.json).",
    )
    p_fund.set_defaults(func=cmd_funders)

    # ----------------------------------------------------------------- journals
    p_jour = sub.add_parser(
        "journals",
        help="Compare a journal CSV against existing journals and produce create/update files.",
        description=(
            "Reads a journal CSV (delimiter auto-detected), matches rows against an "
            "existing journals JSON by UUID then by title, and produces two output files: "
            "one for new journals to create and one for existing journals that need updates."
        ),
    )
    _add_csv_arg(p_jour)
    _add_log_dir_arg(p_jour)
    _add_sample_arg(p_jour)
    p_jour.add_argument("existing", help="Path to the existing journals JSON file.")
    p_jour.add_argument(
        "--output-create", dest="output_create",
        help="Output for new journals (default: ./unmatched_records/journals_to_create_<ts>.json).",
    )
    p_jour.add_argument(
        "--output-update", dest="output_update",
        help="Output for updates (default: ./unmatched_records/journals_to_update_<ts>.json).",
    )
    p_jour.set_defaults(func=cmd_journals)

    # --------------------------------------------------------------- publishers
    p_pub = sub.add_parser(
        "publishers",
        help="Find publishers in a CSV that are missing from a publishers JSON.",
        description=(
            "Reads publisher names from a CSV column dc.publisher (semicolon-separated within cells), "
            "compares them case-insensitively against a Pure publishers JSON, "
            "and writes missing ones as Publisher records ready for upload. "
            "The CSV delimiter is auto-detected."
        ),
    )
    _add_csv_arg(p_pub)
    p_pub.add_argument(
        "publishers",
        help="Path to the existing Pure publishers JSON file used for name matching.",
    )
    _add_log_dir_arg(p_pub)
    _add_sample_arg(p_pub)
    p_pub.add_argument(
        "-o", "--output",
        help="Output path (default: <publishers_dir>/publishers_to_upload_<today>.json).",
    )
    p_pub.set_defaults(func=cmd_publishers)

    # ---------------------------------------------------------------------- run
    p_run = sub.add_parser(
        "run",
        help="Run any combination of commands in one invocation.",
        description=(
            "Run any subset of commands together.\n\n"
            "Example – funders and publishers from the same CSV:\n"
            "  python prepare_uploads.py run \\\n"
            "      --commands funders publishers \\\n"
            "      --csv items.csv --organisations orgs.json\n\n"
            "Example – all four at once:\n"
            "  python prepare_uploads.py run \\\n"
            "      --commands authors funders publishers journals \\\n"
            "      --authors-input authors.json \\\n"
            "      --csv items.csv --organisations orgs.json \\\n"
            "      --journals journals.json"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_run.add_argument(
        "--commands", nargs="+",
        choices=["authors", "funders", "publishers", "journals"],
        required=True,
        metavar="COMMAND",
        help="One or more of: authors funders publishers journals.",
    )
    _add_multi_args(p_run)
    p_run.set_defaults(func=cmd_run)

    # ---------------------------------------------------------------------- all
    p_all = sub.add_parser(
        "all",
        help="Run all four commands at once.",
        description=(
            "Shortcut for `run --commands authors funders publishers journals`.\n\n"
            "Example:\n"
            "  python prepare_uploads.py all \\\n"
            "      --authors-input authors.json \\\n"
            "      --csv items.csv --organisations orgs.json \\\n"
            "      --journals journals.json"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _add_multi_args(p_all)
    p_all.set_defaults(func=cmd_all)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()