import unittest
from decimal import Decimal

from expense_analyzer.parser import (
    ParseError,
    detect_columns,
    detect_date_format,
    parse_amount,
    parse_csv_file,
    parse_csv_text,
    parse_date,
)


class ParseAmountTests(unittest.TestCase):
    def test_plain_and_currency(self):
        self.assertEqual(parse_amount("12.50"), Decimal("12.50"))
        self.assertEqual(parse_amount("$1,234.56"), Decimal("1234.56"))
        self.assertEqual(parse_amount(" 7 "), Decimal("7"))
        self.assertEqual(parse_amount("CAD 19.99"), Decimal("19.99"))

    def test_negative_styles(self):
        self.assertEqual(parse_amount("-45.10"), Decimal("-45.10"))
        self.assertEqual(parse_amount("(45.10)"), Decimal("-45.10"))
        self.assertEqual(parse_amount("45.10-"), Decimal("-45.10"))
        self.assertEqual(parse_amount("-$1,150.00"), Decimal("-1150.00"))

    def test_european_and_thousands_separators(self):
        self.assertEqual(parse_amount("1.234,56"), Decimal("1234.56"))
        self.assertEqual(parse_amount("12,50"), Decimal("12.50"))
        self.assertEqual(parse_amount("1,234"), Decimal("1234"))
        self.assertEqual(parse_amount("1,234,567"), Decimal("1234567"))

    def test_invalid(self):
        for bad in ["", "   ", "abc", "1.2.3", "--"]:
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    parse_amount(bad)


class DateTests(unittest.TestCase):
    def test_iso(self):
        self.assertEqual(detect_date_format(["2026-05-01", "2026-06-15"]), ("%Y-%m-%d", False))

    def test_day_first_is_proven_by_a_large_first_number(self):
        self.assertEqual(detect_date_format(["13/01/2026", "05/02/2026"]), ("%d/%m/%Y", False))

    def test_month_first_is_proven_by_a_large_second_number(self):
        self.assertEqual(detect_date_format(["01/13/2026", "05/02/2026"]), ("%m/%d/%Y", False))

    def test_ambiguous_dates_are_flagged_as_assumed(self):
        self.assertEqual(detect_date_format(["03/04/2026", "05/06/2026"]), ("%m/%d/%Y", True))

    def test_two_digit_year_and_other_separators(self):
        self.assertEqual(detect_date_format(["01/13/26"]), ("%m/%d/%y", False))
        self.assertEqual(detect_date_format(["13.01.2026"]), ("%d.%m.%Y", False))

    def test_named_months(self):
        self.assertEqual(detect_date_format(["May 1, 2026", "Jun 15, 2026"])[0], "%b %d, %Y")
        self.assertEqual(detect_date_format(["01 May 2026"])[0], "%d %b %Y")

    def test_unrecognised(self):
        with self.assertRaises(ParseError):
            detect_date_format(["yesterday"])
        with self.assertRaises(ParseError):
            detect_date_format([])

    def test_time_of_day_is_ignored(self):
        self.assertEqual(parse_date("2026-05-01 14:30:00", "%Y-%m-%d").isoformat(), "2026-05-01")
        self.assertEqual(parse_date("2026-05-01T09:00:00Z", "%Y-%m-%d").isoformat(), "2026-05-01")


class ColumnTests(unittest.TestCase):
    def test_synonyms(self):
        cols = detect_columns(["Posted Date", "Details", "Withdrawals", "Deposits"])
        self.assertEqual(cols, {"date": 0, "description": 1, "debit": 2, "credit": 3})

    def test_override(self):
        cols = detect_columns(["When", "Who", "How much"], {"date": "when", "description": "WHO", "amount": "How much"})
        self.assertEqual(cols, {"date": 0, "description": 1, "amount": 2})

    def test_unknown_override_is_an_error(self):
        with self.assertRaises(ParseError):
            detect_columns(["Date", "Description", "Amount"], {"amount": "Total"})


class ParseCsvTests(unittest.TestCase):
    def test_debit_and_credit_columns(self):
        text = "Date,Description,Debit,Credit\n2026-05-01,COFFEE,4.50,\n2026-05-02,PAYROLL,,1000.00\n"
        result = parse_csv_text(text)
        self.assertEqual([t.amount for t in result.transactions], [Decimal("-4.50"), Decimal("1000.00")])
        self.assertEqual(result.skipped, [])

    def test_signed_amount_with_preamble_lines(self):
        text = "Account: 1234\nPeriod: May\n\nDate,Description,Amount\n05/20/2026,COFFEE,(4.50)\n"
        result = parse_csv_text(text)
        self.assertEqual(len(result.transactions), 1)
        self.assertEqual(result.transactions[0].amount, Decimal("-4.50"))
        self.assertEqual(result.transactions[0].date.isoformat(), "2026-05-20")

    def test_semicolon_delimiter_and_decimal_comma(self):
        text = "Date;Description;Amount\n2026-05-01;SHOP;-1.234,56\n"
        self.assertEqual(parse_csv_text(text).transactions[0].amount, Decimal("-1234.56"))

    def test_expenses_positive_flips_the_sign(self):
        text = "Date,Description,Amount\n2026-05-01,SHOP,25.00\n2026-05-02,REFUND,-5.00\n"
        amounts = [t.amount for t in parse_csv_text(text, expenses_positive=True).transactions]
        self.assertEqual(amounts, [Decimal("-25.00"), Decimal("5.00")])

    def test_bad_rows_are_skipped_and_reported(self):
        text = (
            "Date,Description,Amount\n"
            "2026-05-01,OK,-1.00\n"
            "not-a-date,BAD DATE,-1.00\n"
            "2026-05-03,BAD AMOUNT,abc\n"
            "2026-05-04,ZERO,0.00\n"
            "2026-05-05,,-1.00\n"
            "2026-05-06,SHORT\n"
        )
        result = parse_csv_text(text, date_format="%Y-%m-%d")
        self.assertEqual([t.description for t in result.transactions], ["OK"])
        self.assertEqual(len(result.skipped), 5)
        self.assertEqual(sorted(line for line, _ in result.skipped), [3, 4, 5, 6, 7])

    def test_identical_same_day_rows_stay_separate_but_ids_are_stable(self):
        text = "Date,Description,Amount\n2026-05-01,COFFEE,-4.50\n2026-05-01,COFFEE,-4.50\n"
        first = parse_csv_text(text).transactions
        second = parse_csv_text(text).transactions
        self.assertNotEqual(first[0].id, first[1].id)
        self.assertEqual([t.id for t in first], [t.id for t in second])

    def test_missing_header_is_a_clear_error(self):
        with self.assertRaises(ParseError) as ctx:
            parse_csv_text("foo,bar\n1,2\n")
        self.assertIn("header", str(ctx.exception))

    def test_byte_order_mark_is_ignored(self):
        text = "﻿Date,Description,Amount\n2026-05-01,SHOP,-1.00\n"
        self.assertEqual(len(parse_csv_text(text).transactions), 1)

    def test_description_whitespace_is_cleaned(self):
        text = "Date,Description,Amount\n2026-05-01,  TIM   HORTONS  ,-3.00\n"
        self.assertEqual(parse_csv_text(text).transactions[0].description, "TIM HORTONS")

    def test_reads_windows_1252_files(self):
        import tempfile, os
        path = os.path.join(tempfile.mkdtemp(), "bank.csv")
        with open(path, "wb") as f:
            f.write("Date,Description,Amount\n2026-05-01,CAFÉ PARIS,-3.00\n".encode("cp1252"))
        self.assertEqual(parse_csv_file(path).transactions[0].description, "CAFÉ PARIS")


if __name__ == "__main__":
    unittest.main()
