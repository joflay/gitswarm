import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from compute_attendance import Member, Meeting, calculate_stats, load_meetings, modal_day


class AttendanceRulesTests(unittest.TestCase):
    freshman = Member("Eddie Fu", "eddie@example.com", "Freshman")

    def schedule(self, member, general_count=4, quant_count=4, quant_attended=0):
        start = date(2026, 9, 14)
        meetings = [Meeting(f"general-{i}.csv", "general", start + timedelta(weeks=i), set())
                    for i in range(general_count)]
        meetings += [Meeting(f"quant-{i}.csv", "quant", start + timedelta(weeks=i, days=3),
                             {member.key} if i < quant_attended else set())
                     for i in range(quant_count)]
        return meetings

    def test_general_and_fdp_same_week_award_one_point(self):
        member = self.freshman
        meetings = [Meeting("general.csv", "general", date(2026, 9, 22), {member.key}),
                    Meeting("fdp.csv", "fdp", date(2026, 9, 23), {member.key}),
                    Meeting("next.csv", "general", date(2026, 9, 29), set())]
        row = calculate_stats([member], meetings)[0]
        self.assertEqual((row["general_weeks_recorded"], row["general_points"], row["general_absences_raw"]), (2, 1, 1))

    def test_one_quant_half_point_does_not_erase_a_whole_absence(self):
        row = calculate_stats([self.freshman], self.schedule(self.freshman, quant_attended=1))[0]
        self.assertEqual(row["general_absences_remaining"], 4)
        self.assertEqual(row["quant_makeup_points_unused"], 0.5)
        self.assertFalse(row["general_requirement_met"])

    def test_quant_pairs_stack_and_keep_leftover_half_point(self):
        row = calculate_stats([self.freshman], self.schedule(self.freshman, quant_count=5, quant_attended=5))[0]
        self.assertEqual(row["quant_makeup_points"], 2.5)
        self.assertEqual(row["general_absences_offset"], 2)
        self.assertEqual(row["general_absences_remaining"], 2)
        self.assertEqual(row["quant_makeup_points_unused"], 0.5)
        self.assertTrue(row["general_requirement_met"])

    def test_three_absences_allowed_four_exceed_limit(self):
        for count, allowed in [(3, True), (4, False)]:
            with self.subTest(absences=count):
                row = calculate_stats([self.freshman], self.schedule(self.freshman, general_count=count, quant_count=count))[0]
                self.assertEqual(row["general_requirement_met"], allowed)
                self.assertEqual(row["quant_requirement_met"], allowed)

    def test_upperclassmen_exempt_but_quant_limit_still_applies(self):
        for year in ("Junior", "Senior"):
            member = Member("Richard Ngo", "richard@example.com", year)
            row = calculate_stats([member], self.schedule(member, general_count=10, quant_count=4))[0]
            self.assertFalse(row["general_attendance_required"])
            self.assertEqual(row["general_absences_remaining"], 0)
            self.assertTrue(row["general_requirement_met"])
            self.assertFalse(row["quant_requirement_met"])

    def test_duplicate_quant_exports_do_not_double_credit(self):
        member = self.freshman
        meetings = [Meeting("quant-1.csv", "quant", date(2026, 9, 24), {member.key}),
                    Meeting("quant-duplicate.csv", "quant", date(2026, 9, 24), {member.key}),
                    Meeting("Quant Meeting(4).csv", "quant", date(2026, 10, 8), set())]
        row = calculate_stats([member], meetings)[0]
        self.assertEqual(row["quant_meetings_recorded"], 2)
        self.assertEqual(row["quant_meetings_attended"], 1)
        self.assertEqual(row["quant_makeup_points"], 0.5)

    def test_empty_event_recovers_date_without_using_backup_attendees(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            filename = "Financial Development Program.csv"
            (root / filename).write_text("Financial Development Program | 0 Checked-In\nName,Time\n")
            backup = root / "attendance_cleaning_script" / "backups" / "original"
            backup.mkdir(parents=True)
            (backup / filename).write_text('Financial Development Program | 1 Checked-In\nName,Time\nEddie F,"9/23, 7:00pm"\n')
            meetings = load_meetings(root, [self.freshman], 2026, root / "missing-manifest.csv")
            self.assertEqual(meetings[0].day, date(2026, 9, 23))
            self.assertEqual(meetings[0].attendees, set())
            row = calculate_stats([self.freshman], meetings)[0]
            self.assertEqual(row["general_absences_raw"], 1)

    def test_name_aliases_and_nonmembers(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "Quant Meeting.csv").write_text('Quant Meeting | 3 Checked-In\nName,Time\nEddie F,"9/24, 7:00pm"\nEddie Fu,"9/24, 7:01pm"\nOther Person,"9/24, 7:02pm"\n')
            meetings = load_meetings(root, [self.freshman], 2026, root / "missing-manifest.csv")
            self.assertEqual(meetings[0].attendees, {self.freshman.key})

    def test_conflicting_meeting_dates_require_a_manifest_override(self):
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            modal_day([("one", "9/22, 7:00pm"), ("two", "9/23, 7:00pm")], 2026)


if __name__ == "__main__":
    unittest.main()
