#!/usr/bin/env python3
"""Calculate attendance standing from teamembers.csv and the meeting exports."""

from __future__ import annotations

import argparse
import csv
import re
import tempfile
import unicodedata
from collections import Counter
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[1]
ABSENCE_ALLOWANCE = 3
ALIASES = {"Benjamin Hoang": "Ben Hoang", "Eddie F": "Eddie Fu"}
CLASS_YEARS = {
    "freshman": "Freshman", "freshmen": "Freshman",
    "sophomore": "Sophomore", "sophmores": "Sophomore", "sophomores": "Sophomore",
    "junior": "Junior", "senior": "Senior",
}


def normalize(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return " ".join(re.findall(r"[a-z0-9]+", value.casefold()))


@dataclass(frozen=True)
class Member:
    name: str
    email: str
    class_year: str

    @property
    def key(self) -> str:
        return normalize(self.name)

    @property
    def general_required(self) -> bool:
        return self.class_year in {"Freshman", "Sophomore"}


@dataclass
class Meeting:
    filename: str
    kind: str
    day: date
    attendees: set[str]
    date_source: str = "check-in timestamps"

    @property
    def week(self) -> date:
        return self.day - timedelta(days=self.day.weekday())


def read_csv(path: Path) -> list[list[str]]:
    with path.open(newline="", encoding="utf-8-sig") as source:
        return list(csv.reader(source))


def load_members(path: Path) -> list[Member]:
    members = []
    seen = set()
    for line, row in enumerate(read_csv(path), 1):
        if not row or not any(value.strip() for value in row):
            continue
        if normalize(row[0]) in {"first", "first name"}:
            continue
        if len(row) < 6:
            raise ValueError(f"{path}:{line}: expected first name, last name, email, major, link, class year")
        class_year = CLASS_YEARS.get(normalize(row[5]))
        if class_year is None:
            raise ValueError(f"{path}:{line}: unrecognized class year {row[5]!r}")
        name = " ".join(f"{row[0].strip()} {row[1].strip()}".split())
        member = Member(name, row[2].strip(), class_year)
        if not member.key or member.key in seen:
            raise ValueError(f"{path}:{line}: missing or duplicate member name {name!r}")
        members.append(member)
        seen.add(member.key)
    if not members:
        raise ValueError(f"No members found in {path}")
    return members


def attendance_rows(path: Path) -> tuple[str, list[tuple[str, str]]]:
    rows = read_csv(path)
    for index, row in enumerate(rows):
        columns = [normalize(cell) for cell in row]
        if "name" in columns and "time" in columns:
            name_column, time_column = columns.index("name"), columns.index("time")
            records = []
            for line, record in enumerate(rows[index + 1 :], index + 2):
                if not record or not any(cell.strip() for cell in record):
                    continue
                if len(record) <= max(name_column, time_column):
                    raise ValueError(f"{path}:{line}: incomplete attendance row")
                records.append((record[name_column].strip(), record[time_column].strip()))
            title = rows[0][0] if rows and rows[0] else path.stem
            return title, records
    raise ValueError(f"No Name,Time header found in {path}")


def parse_checkin_day(value: str, year: int) -> date | None:
    match = re.match(r"^\s*(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?(?=,|\s|$)", value)
    if not match:
        return None
    month, day, explicit_year = match.groups()
    meeting_year = int(explicit_year) if explicit_year else year
    if explicit_year and meeting_year < 100:
        meeting_year += 2000
    return date(meeting_year, int(month), int(day))


def modal_day(records: list[tuple[str, str]], year: int) -> date | None:
    counts = Counter(day for _, timestamp in records if (day := parse_checkin_day(timestamp, year)))
    if not counts:
        return None
    most = max(counts.values())
    candidates = sorted(day for day, count in counts.items() if count == most)
    if len(candidates) != 1:
        raise ValueError(f"Meeting date is ambiguous: {candidates}; set it in meeting_dates.csv")
    return candidates[0]


def infer_kind(title: str, filename: str) -> str:
    label = normalize(f"{title} {filename}")
    if "quant meeting" in label:
        return "quant"
    if "financial development program" in label:
        return "fdp"
    if "general meeting" in label or "first meeting" in label:
        return "general"
    raise ValueError(f"Unknown meeting type for {filename}; set it in meeting_dates.csv")


def load_meetings(attendance_dir: Path, members: list[Member], year: int, manifest: Path) -> list[Meeting]:
    overrides = {}
    if manifest.exists():
        with manifest.open(newline="", encoding="utf-8-sig") as source:
            for row in csv.DictReader(source):
                filename = row["filename"]
                if filename in overrides:
                    raise ValueError(f"Duplicate filename in {manifest}: {filename}")
                overrides[filename] = row

    member_keys = {member.key for member in members}
    aliases = {normalize(alias): normalize(name) for alias, name in ALIASES.items()}
    meetings = []
    files = sorted(attendance_dir.glob("*.csv"))
    if not files:
        raise ValueError(f"No attendance CSV files found in {attendance_dir}")
    for path in files:
        title, records = attendance_rows(path)
        override = overrides.get(path.name)
        if override:
            kind = override["meeting_type"].strip().lower()
            if kind == "ignore":
                continue
            day = date.fromisoformat(override["meeting_date"].strip())
            source = override.get("date_source") or "meeting_dates.csv"
        else:
            kind = infer_kind(title, path.name)
            day = modal_day(records, year)
            source = "check-in timestamps"
            if day is None:
                backups = attendance_dir / "attendance_cleaning_script" / "backups"
                for backup in sorted(backups.glob(f"*/{path.name}"), reverse=True):
                    _, original_records = attendance_rows(backup)
                    day = modal_day(original_records, year)
                    if day:
                        source = f"date recovered from {backup.relative_to(attendance_dir)}"
                        break
            if day is None:
                raise ValueError(f"No date available for {path.name}; add it to {manifest}")
        if kind not in {"general", "fdp", "quant"}:
            raise ValueError(f"Invalid meeting type {kind!r} for {path.name}")
        attendees = set()
        for name, _ in records:
            key = normalize(name)
            key = aliases.get(key, key)
            if key in member_keys:
                attendees.add(key)
        meetings.append(Meeting(path.name, kind, day, attendees, source))
    if not meetings:
        raise ValueError("No included meetings found")
    return sorted(meetings, key=lambda meeting: (meeting.day, meeting.kind, meeting.filename))


def calculate_stats(members: list[Member], meetings: list[Meeting]) -> list[dict]:
    general_weeks = {meeting.week for meeting in meetings if meeting.kind in {"general", "fdp"}}
    # Repeated exports of the same day's quant meeting count only once.
    quant_dates = {meeting.day for meeting in meetings if meeting.kind == "quant"}
    stats = []
    for member in members:
        general_attended = {meeting.week for meeting in meetings
                            if meeting.kind in {"general", "fdp"} and member.key in meeting.attendees}
        quant_attended = {meeting.day for meeting in meetings
                          if meeting.kind == "quant" and member.key in meeting.attendees}
        missed_weeks = general_weeks - general_attended
        missed_quant = quant_dates - quant_attended
        raw_absences = len(missed_weeks) if member.general_required else 0
        makeup_points = len(quant_attended) * 0.5 if member.general_required else 0.0
        absences_offset = min(raw_absences, len(quant_attended) // 2) if member.general_required else 0
        general_absences = raw_absences - absences_offset
        general_over = max(0, general_absences - ABSENCE_ALLOWANCE)
        quant_over = max(0, len(missed_quant) - ABSENCE_ALLOWANCE)
        stats.append({
            "name": member.name,
            "email": member.email,
            "class_year": member.class_year,
            "general_attendance_required": member.general_required,
            "general_weeks_recorded": len(general_weeks),
            "general_weeks_attended": len(general_attended),
            "general_points": len(general_attended) if member.general_required else 0,
            "general_absences_raw": raw_absences,
            "quant_meetings_recorded": len(quant_dates),
            "quant_meetings_attended": len(quant_attended),
            "quant_absences": len(missed_quant),
            "quant_makeup_points": makeup_points,
            "general_absences_offset": absences_offset,
            "quant_makeup_points_unused": makeup_points - absences_offset,
            "general_absences_remaining": general_absences,
            "general_absence_allowance": ABSENCE_ALLOWANCE if member.general_required else "exempt",
            "quant_absence_allowance": ABSENCE_ALLOWANCE,
            "general_absences_over_limit": general_over,
            "quant_absences_over_limit": quant_over,
            "general_requirement_met": not general_over,
            "quant_requirement_met": not quant_over,
            "standing": "within allowance" if not (general_over or quant_over) else "over allowance",
            "missed_general_week_starts": ";".join(day.isoformat() for day in sorted(missed_weeks)) if member.general_required else "",
            "missed_quant_dates": ";".join(day.isoformat() for day in sorted(missed_quant)),
        })
    return stats


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        raise ValueError(f"No rows to write to {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="", dir=path.parent, delete=False) as output:
            temporary_path = Path(output.name)
            writer = csv.DictWriter(output, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        temporary_path.replace(path)
    finally:
        if temporary_path:
            temporary_path.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int, default=date.today().year, help="Year used for check-ins without a year; existing manifest dates take precedence.")
    parser.add_argument("--members", type=Path, default=REPO_ROOT / "teamembers.csv")
    parser.add_argument("--attendance-dir", type=Path, default=REPO_ROOT / "attendance")
    parser.add_argument("--output-dir", type=Path, default=SCRIPT_DIR)
    parser.add_argument("--manifest", type=Path, help="Optional event dates/types CSV; defaults to output-dir/meeting_dates.csv.")
    args = parser.parse_args()
    manifest = args.manifest or args.output_dir / "meeting_dates.csv"
    members = load_members(args.members)
    meetings = load_meetings(args.attendance_dir, members, args.year, manifest)
    stats = calculate_stats(members, meetings)
    details = [{
        "name": member.name, "email": member.email, "class_year": member.class_year,
        "meeting_file": meeting.filename, "meeting_type": meeting.kind,
        "meeting_date": meeting.day.isoformat(), "week_start": meeting.week.isoformat(),
        "attended": member.key in meeting.attendees,
    } for member in members for meeting in meetings]
    dates = [{"filename": meeting.filename, "meeting_type": meeting.kind,
              "meeting_date": meeting.day.isoformat(), "date_source": meeting.date_source}
             for meeting in meetings]
    write_csv(args.output_dir / "attendance_stats.csv", stats)
    write_csv(args.output_dir / "meeting_attendance.csv", details)
    if not args.manifest:
        # Preserve explicit ignore rows so subsequent runs do not reinclude them.
        if manifest.exists():
            with manifest.open(newline="", encoding="utf-8-sig") as source:
                for row in csv.DictReader(source):
                    if row["meeting_type"].strip().lower() == "ignore":
                        dates.append({field: row.get(field, "") for field in dates[0]})
        write_csv(manifest, dates)
    print(f"Saved statistics for {len(members)} members to {args.output_dir / 'attendance_stats.csv'}")
    print(f"Recorded {stats[0]['general_weeks_recorded']} general/FDP weeks and {stats[0]['quant_meetings_recorded']} quant meetings.")
    print(f"{sum(row['standing'] == 'over allowance' for row in stats)} members exceed an absence allowance.")
    print("Week boundaries: Monday-Sunday. First Meeting counts as general. Quant makeups use full pairs; leftover half-points are carried in the CSV.")
    print("Only meetings with supplied CSV exports are counted; missing meeting numbers do not create absences.")


if __name__ == "__main__":
    main()
