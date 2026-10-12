# Attendance statistics

Run from any directory:

```bash
python3 /home/joflay/gitswarm/attendance/attendance_stats/compute_attendance.py --year 2026
```

The script reads the headerless `teamembers.csv` roster and attendance CSVs directly under `attendance/`. It produces:

- `attendance_stats.csv`: each member's class year, attendance counts, points, absences, allowance checks, and missed weeks/dates.
- `meeting_attendance.csv`: one row per member per recorded meeting, for auditing the totals.
- `meeting_dates.csv`: the dated meeting list. Edit dates or meeting types here to correct the schedule; this list takes precedence over timestamp inference on subsequent runs. Types are `general`, `fdp`, `quant`, or `ignore`.

## Rules

- Freshmen and sophomores earn one point for each Monday–Sunday week in which they attend at least one general or FDP meeting. Attending both gives one point total.
- A week with a recorded general/FDP meeting and no attendance is one general absence. Three general absences are allowed.
- Each quant attendance earns 0.5 general make-up points for freshmen and sophomores. Every complete pair of quant attendances removes one general absence. Half-points and any excess credit remain in `quant_makeup_points_unused`. The script recomputes these totals from all supplied exports on every run.
- All members are allowed three quant absences. General make-up credits do not forgive quant absences.
- Juniors and seniors have no general/FDP attendance requirement; their standing depends only on quant absences.
- General and quant allowances are checked independently. A member exceeds an allowance only when they have more than three charged absences.

`First Meeting.csv` counts as a general meeting. Quant exports on the same date count as one meeting, so repeated exports do not double attendance credit. Only exported meetings count; a missing filename/meeting number does not establish that a meeting happened. No meetings or absences are inferred between exports or after the latest recorded meeting.

Dates without a year use `--year` (the current calendar year by default). If a cleaned file has no attendees, the script can recover its date from the originals in `attendance_cleaning_script/backups`, using only their timestamps. Check the generated `meeting_dates.csv` for inferred dates, especially the year. Adding an empty meeting requires a dated manifest row or an original backup with check-in timestamps.

The name variants `Benjamin Hoang` → `Ben Hoang` and `Eddie F` → `Eddie Fu` are recognized. Nonmembers are ignored, and repeated attendee rows earn attendance credit once. Every roster member appears in the report, including members with no attendance.

## Member messages

Look up a first name or full name to print a standard message using the latest saved statistics:

```bash
python3 /home/joflay/gitswarm/attendance/attendance_stats/member_message.py Bryce
python3 /home/joflay/gitswarm/attendance/attendance_stats/member_message.py Abhi
```

Run without a name to get an interactive prompt. Names are case-insensitive; ambiguous first names require a full name. An optional class qualifier, such as `"Bryce (sophmore)"`, is checked against the saved class year. The message reports the original and adjusted general/FDP absences for freshmen and sophomores, and the general/FDP exemption for juniors and seniors. All messages show the quant absence count and maximum.

The Python function `get_member_message(name)` returns the message as a string. Re-run `compute_attendance.py` after changing attendance records, then generate messages from the updated CSV.

Run the focused rule checks:

```bash
python3 -m unittest discover -s /home/joflay/gitswarm/attendance/attendance_stats -p 'test_*.py'
```
