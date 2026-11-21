import contextlib
import csv
import io
import tempfile
import unittest
from pathlib import Path

from expense_analyzer.cli import main

SAMPLES = Path(__file__).resolve().parent.parent / "sample"
MAIN_SAMPLE = str(SAMPLES / "statement_debit_credit.csv")
OTHER_SAMPLE = str(SAMPLES / "statement_signed_amounts.csv")


class CliTestCase(unittest.TestCase):
    """Runs main() exactly as the command line would, with a fresh data folder each time."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.data = str(self.tmp / "data")

    def run_cli(self, *args: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["--data-dir", self.data, *args])
        return code, out.getvalue(), err.getvalue()

    def write(self, name: str, text: str) -> str:
        path = self.tmp / name
        path.write_text(text, encoding="utf-8")
        return str(path)


class ImportTests(CliTestCase):
    def test_import_sample_file(self):
        code, out, _ = self.run_cli("import", MAIN_SAMPLE)
        self.assertEqual(code, 0)
        self.assertIn("Added 255 new transactions; 0 already imported.", out)
        self.assertIn("4 transactions are Uncategorized", out)

    def test_importing_twice_adds_nothing_new(self):
        self.run_cli("import", MAIN_SAMPLE)
        _, out, _ = self.run_cli("import", MAIN_SAMPLE)
        self.assertIn("Added 0 new transactions; 255 already imported.", out)

    def test_dry_run_saves_nothing(self):
        self.run_cli("import", OTHER_SAMPLE, "--dry-run")
        _, out, _ = self.run_cli("summary")
        self.assertIn("No transactions yet", out)

    def test_other_statement_style_with_preamble_and_us_dates(self):
        code, out, _ = self.run_cli("import", OTHER_SAMPLE)
        self.assertEqual(code, 0)
        self.assertIn("(dates: %m/%d/%Y)", out)
        self.assertIn("Added 18 new transactions", out)

    def test_missing_file(self):
        code, _, err = self.run_cli("import", str(self.tmp / "nope.csv"))
        self.assertEqual(code, 1)
        self.assertIn("File not found", err)

    def test_unreadable_file_gives_a_helpful_error(self):
        code, _, err = self.run_cli("import", self.write("bad.csv", "foo,bar\n1,2\n"))
        self.assertEqual(code, 1)
        self.assertIn("header", err)

    def test_skipped_rows_are_reported(self):
        path = self.write("s.csv", "Date,Description,Amount\n2026-05-01,OK,-1\n2026-05-02,BAD,abc\n")
        _, out, _ = self.run_cli("import", path)
        self.assertIn("Skipped 1 unreadable rows", out)
        self.assertIn("line 3", out)

    def test_ambiguous_dates_produce_a_warning(self):
        path = self.write("a.csv", "Date,Description,Amount\n03/04/2026,SHOP,-5\n")
        _, out, _ = self.run_cli("import", path)
        self.assertIn("could be month-first or day-first", out)

    def test_column_override_and_expenses_positive(self):
        path = self.write("c.csv", "When,Who,Spent\n2026-05-01,SUPERSTORE,25.00\n")
        code, out, _ = self.run_cli(
            "import", path, "--date-col", "When", "--description-col", "Who", "--amount-col", "Spent", "--expenses-positive"
        )
        self.assertEqual(code, 0)
        _, listing, _ = self.run_cli("list")
        self.assertIn("-$25.00", listing)
        self.assertIn("Groceries", listing)


class AnalysisCommandTests(CliTestCase):
    def setUp(self):
        super().setUp()
        self.run_cli("import", MAIN_SAMPLE)

    def test_summary(self):
        code, out, _ = self.run_cli("summary")
        self.assertEqual(code, 0)
        for text in ["Monthly summary", "2026-08", "Housing", "$6,900.00", "Savings rate: 33%"]:
            self.assertIn(text, out)

    def test_summary_for_one_month(self):
        _, out, _ = self.run_cli("summary", "--month", "2026-08")
        self.assertIn("Spending by category in 2026-08", out)
        self.assertIn("Shopping", out)

    def test_recurring_finds_the_subscriptions_but_not_the_variable_hydro_bill(self):
        _, out, _ = self.run_cli("recurring")
        for name in ["netflix", "spotify", "goodlife clubs", "telus mobility"]:
            self.assertIn(name, out)
        self.assertNotIn("hydro", out)
        self.assertIn("per month", out)

    def test_anomalies_flags_the_big_purchase(self):
        _, out, _ = self.run_cli("anomalies")
        self.assertIn("BEST BUY", out)
        self.assertNotIn("TIM HORTONS", out)

    def test_merchants(self):
        _, out, _ = self.run_cli("merchants", "-n", "3")
        self.assertEqual(len(out.strip().splitlines()), 2 + 3)  # header, rule, 3 rows

    def test_list_filters_and_limit(self):
        _, out, _ = self.run_cli("list", "--category", "Housing", "--limit", "0")
        self.assertEqual(len(out.strip().splitlines()), 2 + 6)
        _, out, _ = self.run_cli("list", "--limit", "5")
        self.assertIn("Showing the latest 5 of 255", out)

    def test_report_file_is_written(self):
        target = self.tmp / "out.html"
        code, out, _ = self.run_cli("report", "-o", str(target))
        self.assertEqual(code, 0)
        self.assertIn("Expense report", target.read_text(encoding="utf-8"))

    def test_export_round_trips_and_blocks_formula_injection(self):
        path = self.write("evil.csv", 'Date,Description,Amount\n2026-05-01,"=HYPERLINK(""http://x"")",-5\n')
        self.run_cli("import", path)
        target = self.tmp / "export.csv"
        self.run_cli("export", str(target))
        with open(target, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(len(rows), 256)
        evil = [r for r in rows if "HYPERLINK" in r["description"]][0]
        self.assertTrue(evil["description"].startswith("'="))


class EmptyDatabaseTests(CliTestCase):
    def test_every_report_command_explains_what_to_do(self):
        for command in ["summary", "merchants", "recurring", "anomalies", "report"]:
            with self.subTest(command=command):
                code, out, _ = self.run_cli(command)
                self.assertEqual(code, 0)
                self.assertIn("No transactions yet", out)


class RulesWorkflowTests(CliTestCase):
    def test_teach_a_rule_then_recategorize(self):
        self.run_cli("import", MAIN_SAMPLE)
        _, before, _ = self.run_cli("list", "--category", "Uncategorized")
        self.assertIn("CORNER MARKET", before)

        _, out, _ = self.run_cli("rules", "add", "Groceries", "corner market")
        self.assertIn("Added", out)
        _, out, _ = self.run_cli("recategorize")
        self.assertIn("Updated 4 transactions", out)

        _, after, _ = self.run_cli("list", "--category", "Uncategorized")
        self.assertIn("No matching transactions", after)
        _, rules, _ = self.run_cli("rules", "list")
        self.assertIn("Groceries: corner market", rules)

    def test_duplicate_rule_and_empty_rules_list(self):
        _, out, _ = self.run_cli("rules", "list")
        self.assertIn("no custom rules", out)
        self.run_cli("rules", "add", "Food", "pizza")
        _, out, _ = self.run_cli("rules", "add", "Food", "PIZZA")
        self.assertIn("already in your rules", out)

    def test_bad_regex_rule_is_rejected(self):
        code, _, err = self.run_cli("rules", "add", "Food", "re:(")
        self.assertEqual(code, 1)
        self.assertIn("regular expression", err)

    def test_set_category_by_id_prefix_survives_recategorize(self):
        self.run_cli("import", MAIN_SAMPLE)
        _, listing, _ = self.run_cli("list", "--category", "Housing", "--limit", "1")
        short_id = listing.splitlines()[2].split()[0]
        code, out, _ = self.run_cli("set-category", short_id, "Home")
        self.assertEqual(code, 0)
        self.run_cli("recategorize")
        _, home, _ = self.run_cli("list", "--category", "Home")
        self.assertIn(short_id, home)

    def test_set_category_with_unknown_or_ambiguous_id(self):
        self.run_cli("import", MAIN_SAMPLE)
        code, _, err = self.run_cli("set-category", "zzzzzz", "X")
        self.assertEqual(code, 1)
        self.assertIn("No transaction", err)
        code, _, err = self.run_cli("set-category", "", "X")  # empty prefix matches everything
        self.assertEqual(code, 1)
        self.assertIn("match", err)


class ResetTests(CliTestCase):
    def test_reset_needs_confirmation(self):
        self.run_cli("import", MAIN_SAMPLE)
        code, _, err = self.run_cli("reset")
        self.assertEqual(code, 1)
        self.assertIn("--yes", err)
        _, out, _ = self.run_cli("summary")
        self.assertIn("Monthly summary", out)

    def test_reset_with_yes_clears_everything_but_keeps_rules(self):
        self.run_cli("import", MAIN_SAMPLE)
        self.run_cli("rules", "add", "Food", "pizza")
        self.run_cli("reset", "--yes")
        _, out, _ = self.run_cli("summary")
        self.assertIn("No transactions yet", out)
        _, rules, _ = self.run_cli("rules", "list")
        self.assertIn("Food: pizza", rules)


if __name__ == "__main__":
    unittest.main()
