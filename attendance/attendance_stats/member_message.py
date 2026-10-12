#!/usr/bin/env python3
"""Return a standard attendance message for a member in attendance_stats.csv."""

from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path

from compute_attendance import ALIASES, CLASS_YEARS, normalize


STATS_CSV = Path(__file__).resolve().parent / "attendance_stats.csv"
CLASS_LABELS = {**CLASS_YEARS, "sophmore": "Sophomore"}
REQUIRED_COLUMNS = {
    "name", "class_year", "general_absences_raw", "general_absences_remaining",
    "general_absence_allowance", "quant_absences", "quant_absence_allowance",
}


def load_stats(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as source:
        reader = csv.DictReader(source)
        missing = REQUIRED_COLUMNS - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"Statistics CSV is missing columns: {', '.join(sorted(missing))}")
        rows = list(reader)
    if not rows:
        raise ValueError("Statistics CSV has no members. Run compute_attendance.py first.")
    return rows


def find_member(name: str, rows: list[dict[str, str]]) -> dict[str, str]:
    name = name.strip()
    qualifier = re.fullmatch(r"(.*?)\s*\(([^()]*)\)", name)
    expected_class = None
    if qualifier:
        name = qualifier.group(1).strip()
        expected_class = CLASS_LABELS.get(normalize(qualifier.group(2)))
        if expected_class is None:
            raise ValueError(f"Unknown class year: {qualifier.group(2)}")
    key = normalize(name)
    if not key:
        raise ValueError("Enter a member's first name or full name.")
    aliases = {normalize(alias): normalize(canonical) for alias, canonical in ALIASES.items()}
    # Support the same first-name variant as the attendance exports.
    aliases["benjamin"] = "ben"
    key = aliases.get(key, key)
    matches = [row for row in rows if normalize(row["name"]) == key]
    if not matches:
        # Match complete words so Abhi never matches Abhinav.
        matches = [row for row in rows if normalize(row["name"]).startswith(key + " ")]
    if not matches:
        raise ValueError(f"No member found for {name!r}. Available members: {', '.join(row['name'] for row in rows)}")
    if len(matches) > 1:
        raise ValueError(f"Name {name!r} is ambiguous. Enter a full name: {', '.join(row['name'] for row in matches)}")
    member = matches[0]
    if expected_class and expected_class != member["class_year"]:
        raise ValueError(f"{member['name']} is listed as a {member['class_year'].lower()}, not a {expected_class.lower()}.")
    return member


def count(row: dict[str, str], column: str) -> int:
    try:
        value = int(row[column])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"Invalid {column} for {row.get('name', 'member')}; recompute the statistics.") from exc
    if value < 0:
        raise ValueError(f"Negative {column} for {row['name']}; recompute the statistics.")
    return value


def absences(value: int) -> str:
    return f"{value} {'absence' if value == 1 else 'absences'}"


def format_message(member: dict[str, str]) -> str:
    greeting = member["name"].split()[0]
    class_year = member["class_year"]
    quant = count(member, "quant_absences")
    quant_max = count(member, "quant_absence_allowance")
    quant_sentence = f"You have {absences(quant)} from quant meetings (maximum {quant_max})."
    if class_year in {"Junior", "Senior"}:
        return (
            f"{greeting}, since you are a {class_year.lower()}, you are not required to attend "
            f"general/FDP meetings. {quant_sentence}"
        )
    if class_year not in {"Freshman", "Sophomore"}:
        raise ValueError(f"Unknown class year for {member['name']}: {class_year}")
    raw = count(member, "general_absences_raw")
    remaining = count(member, "general_absences_remaining")
    general_max = count(member, "general_absence_allowance")
    return (
        f"{greeting}, you have {absences(raw)} from general/FDP meetings. "
        f"After applying your quant attendance credit, you have {absences(remaining)} "
        f"remaining from general/FDP meetings (maximum {general_max}). {quant_sentence}"
    )


def get_member_message(name: str, stats_csv: Path = STATS_CSV) -> str:
    """Look up a first/full name and return the message as a string."""
    return format_message(find_member(name, load_stats(Path(stats_csv))))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("name", nargs="*", help="First or full name; omit to enter it interactively.")
    parser.add_argument("--stats-csv", type=Path, default=STATS_CSV)
    args = parser.parse_args()
    try:
        name = " ".join(args.name) if args.name else input("Member name: ")
        print(get_member_message(name, args.stats_csv))
    except (OSError, ValueError, csv.Error, EOFError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
