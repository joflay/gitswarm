import csv
import tempfile
import unittest
from pathlib import Path

from member_message import find_member, format_message, get_member_message


def member(name="Bryce Millington", year="Sophomore", raw=2, remaining=1, quant=1):
    return {
        "name": name, "class_year": year, "general_absences_raw": str(raw),
        "general_absences_remaining": str(remaining),
        "general_absence_allowance": "exempt" if year in {"Junior", "Senior"} else "3",
        "quant_absences": str(quant), "quant_absence_allowance": "3",
    }


class MemberMessageTests(unittest.TestCase):
    def test_underclassman_message_uses_before_and_after_counts(self):
        self.assertEqual(format_message(member()),
            "Bryce, you have 2 absences from general/FDP meetings. After applying your quant attendance credit, "
            "you have 1 absence remaining from general/FDP meetings (maximum 3). "
            "You have 1 absence from quant meetings (maximum 3).")

    def test_both_upperclass_years_are_exempt(self):
        for year in ("Junior", "Senior"):
            with self.subTest(year=year):
                message = format_message(member("Abhi Avula", year, quant=2))
                self.assertIn(f"since you are a {year.lower()}", message)
                self.assertIn("not required to attend general/FDP meetings", message)
                self.assertIn("2 absences from quant meetings (maximum 3)", message)
                self.assertNotIn("remaining from general/FDP", message)

    def test_first_name_uses_whole_words(self):
        rows = [member("Abhinav Vasudevan", "Senior"), member("Abhi Avula", "Junior")]
        self.assertEqual(find_member(" abHI ", rows)["name"], "Abhi Avula")
        self.assertEqual(find_member("Abhinav", rows)["name"], "Abhinav Vasudevan")

    def test_ambiguous_name_requires_full_name(self):
        rows = [member("Bryce Millington"), member("Bryce Smith")]
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            find_member("Bryce", rows)
        self.assertEqual(find_member("Bryce Smith", rows)["name"], "Bryce Smith")

    def test_optional_class_qualifier_accepts_typo_and_checks_roster(self):
        self.assertEqual(find_member("Bryce (sophmore)", [member()])["name"], "Bryce Millington")
        with self.assertRaisesRegex(ValueError, "listed as a sophomore"):
            find_member("Bryce (junior)", [member()])

    def test_aliases_and_missing_members(self):
        rows = [member("Ben Hoang", "Junior"), member("Eddie Fu")]
        self.assertEqual(find_member("Benjamin", rows)["name"], "Ben Hoang")
        self.assertEqual(find_member("Eddie F", rows)["name"], "Eddie Fu")
        with self.assertRaisesRegex(ValueError, "No member found"):
            find_member("Someone Else", rows)

    def test_reads_csv_and_returns_string(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "stats.csv"
            row = member()
            with path.open("w", newline="") as output:
                writer = csv.DictWriter(output, fieldnames=list(row))
                writer.writeheader()
                writer.writerow(row)
            self.assertEqual(get_member_message("Bryce", path), format_message(row))

    def test_invalid_counts_are_not_printed_as_attendance(self):
        row = member()
        row["quant_absences"] = "missing"
        with self.assertRaisesRegex(ValueError, "Invalid quant_absences"):
            format_message(row)


if __name__ == "__main__":
    unittest.main()
