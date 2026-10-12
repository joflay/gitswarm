#!/usr/bin/env python3
"""Keep attendance rows only for members listed in the repository's team CSV."""

from __future__ import annotations

import argparse
import csv
import re
import shutil
import tempfile
import unicodedata
from datetime import datetime
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[1]
ATTENDANCE_DIR = REPO_ROOT / "attendance"
MEMBERS_CSV = REPO_ROOT / "teamembers.csv"
BACKUP_DIR = SCRIPT_DIR / "backups"

# Known name variants in the attendance exports for members in teamembers.csv.
MEMBER_NAME_ALIASES = {
    "Benjamin Hoang": "Ben Hoang",
    "Eddie F": "Eddie Fu",
}


def normalize(value: str) -> str:
    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return " ".join(re.findall(r"[a-z0-9]+", ascii_value.casefold()))


def load_member_names() -> set[str]:
    names: set[str] = set()
    with MEMBERS_CSV.open("r", newline="", encoding="utf-8-sig") as source:
        for row in csv.reader(source):
            if len(row) < 2:
                continue
            name = " ".join(part.strip() for part in row[:2] if part.strip())
            if name:
                names.add(normalize(name))
    if not names:
        raise RuntimeError(f"No member names were found in {MEMBERS_CSV}")
    for alias, canonical in MEMBER_NAME_ALIASES.items():
        if normalize(canonical) in names:
            names.add(normalize(alias))
    return names


def clean_file(path: Path, member_names: set[str]) -> tuple[bytes, int, int]:
    raw = path.read_bytes()
    had_bom = raw.startswith(b"\xef\xbb\xbf")
    text = raw.decode("utf-8-sig")
    rows = list(csv.reader(text.splitlines()))
    header_index = next(
        (index for index, row in enumerate(rows) if row and normalize(row[0]) == "name"),
        None,
    )
    if header_index is None:
        raise RuntimeError(f"Could not find the Name header in {path}")
    if header_index == 0:
        raise RuntimeError(f"Expected an event title row before the Name header in {path}")

    kept_rows = rows[: header_index + 1]
    kept = 0
    removed = 0
    for row in rows[header_index + 1 :]:
        if not row or not any(value.strip() for value in row):
            kept_rows.append(row)
            continue
        attendee_name = row[0].strip() if row else ""
        if normalize(attendee_name) in member_names:
            kept_rows.append(row)
            kept += 1
        else:
            removed += 1

    title = kept_rows[0]
    if title:
        title[0] = re.sub(
            r"\b\d+(?=\s+Checked-In\b)",
            str(kept),
            title[0],
            count=1,
            flags=re.IGNORECASE,
        )

    from io import StringIO

    output = StringIO(newline="")
    csv.writer(output, lineterminator="\r\n").writerows(kept_rows)
    encoded = output.getvalue().encode("utf-8")
    if had_bom:
        encoded = b"\xef\xbb\xbf" + encoded
    return encoded, kept, removed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Back up and replace the attendance CSV files (default is a dry run).",
    )
    args = parser.parse_args()

    member_names = load_member_names()
    files = sorted(ATTENDANCE_DIR.glob("*.csv"))
    if not files:
        raise RuntimeError(f"No attendance CSV files found in {ATTENDANCE_DIR}")

    results = [(path, *clean_file(path, member_names)) for path in files]
    for path, _, kept, removed in results:
        print(f"{path.name}: keep {kept}, remove {removed}")

    total_kept = sum(kept for _, _, kept, _ in results)
    total_removed = sum(removed for _, _, _, removed in results)
    print(f"Total: keep {total_kept}, remove {total_removed} attendee rows across {len(results)} files")
    if not args.apply:
        print("Dry run only. Re-run with --apply to back up and update the files.")
        return

    backup_path = BACKUP_DIR / datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path.mkdir(parents=True, exist_ok=False)
    for path, _, _, _ in results:
        shutil.copy2(path, backup_path / path.name)

    replacements: list[tuple[Path, Path]] = []
    try:
        for path, content, _, _ in results:
            with tempfile.NamedTemporaryFile(
                mode="wb", dir=path.parent, prefix=f".{path.name}.", delete=False
            ) as temp_file:
                temp_file.write(content)
                replacements.append((path, Path(temp_file.name)))
        for path, temporary_path in replacements:
            temporary_path.replace(path)
    finally:
        for _, temporary_path in replacements:
            temporary_path.unlink(missing_ok=True)

    print(f"Updated attendance CSVs. Originals backed up in {backup_path}")


if __name__ == "__main__":
    main()
