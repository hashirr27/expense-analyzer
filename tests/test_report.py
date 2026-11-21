import unittest
from decimal import Decimal

from expense_analyzer import report

from helpers import tx


class MoneyTests(unittest.TestCase):
    def test_formatting(self):
        self.assertEqual(report.money(Decimal("1234.5")), "$1,234.50")
        self.assertEqual(report.money(Decimal("-12.3")), "-$12.30")
        self.assertEqual(report.money(0), "$0.00")
        self.assertEqual(report.money(Decimal("2.005")), "$2.00")  # rounds half to even, like Decimal's default


class TableTests(unittest.TestCase):
    def test_alignment(self):
        out = report.render_table(["Name", "Total"], [["a", "$5.00"], ["longer", "$10.00"]], {1})
        lines = out.splitlines()
        # Column 1 is 6 wide, column 2 is 6 wide (right-aligned), with 2 spaces between columns.
        self.assertEqual(lines[0], "Name     Total")
        self.assertEqual(lines[1], "------  ------")
        self.assertEqual(lines[2], "a        $5.00")
        self.assertEqual(lines[3], "longer  $10.00")

    def test_bar(self):
        self.assertEqual(report.bar(5, 10, width=10), "#####")
        self.assertEqual(report.bar(0, 10), "")
        self.assertEqual(report.bar(5, 0), "")
        self.assertEqual(report.bar(50, 10, width=10), "##########")  # never longer than the width


class HtmlReportTests(unittest.TestCase):
    ROWS = [
        tx("2026-05-01", "PAYROLL", "1000", "Income"),
        tx("2026-05-02", "<script>alert(1)</script> SHOP", "-20", "Shopping"),
        tx("2026-06-02", "NETFLIX", "-16.49", "Subscriptions"),
    ]

    def test_contains_the_main_sections(self):
        page = report.html_report(self.ROWS)
        for text in ["Expense report", "Where the money went", "Recurring charges", "Unusually large purchases", "Top merchants", "<svg"]:
            self.assertIn(text, page)

    def test_user_text_is_escaped(self):
        # A hostile description and category must not become real HTML. Both appear in the
        # "unusually large purchases" table and the category chart.
        evil = "<script>alert(1)</script> SHOP"
        rows = [tx(f"2026-05-{i + 1:02d}", f"STORE{i}", "-25", "<b>Odd</b>") for i in range(6)]
        rows.append(tx("2026-06-01", evil, "-900", "<b>Odd</b>"))
        page = report.html_report(rows)
        self.assertNotIn("<script>alert(1)</script>", page)
        self.assertNotIn("<b>Odd</b>", page)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt; SHOP", page)
        self.assertIn("&lt;b&gt;Odd&lt;/b&gt;", page)

    def test_empty_data_does_not_crash(self):
        page = report.html_report([])
        self.assertIn("no data", page)

    def test_is_self_contained(self):
        page = report.html_report(self.ROWS)
        self.assertNotIn("http://", page)
        self.assertNotIn("https://", page)


if __name__ == "__main__":
    unittest.main()
