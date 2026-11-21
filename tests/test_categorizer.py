import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from expense_analyzer.categorizer import (
    Categorizer,
    RulesError,
    add_rule,
    load_rules,
    normalize_merchant,
    save_rules,
    validate_rules,
)


def d(x: str) -> Decimal:
    return Decimal(x)


class CategorizeTests(unittest.TestCase):
    def setUp(self):
        self.c = Categorizer()

    def test_default_rules(self):
        self.assertEqual(self.c.categorize("NETFLIX.COM 866-579-7172 ON", d("-16.49")), "Subscriptions")
        self.assertEqual(self.c.categorize("SUPERSTORE #1234 WINNIPEG MB", d("-80")), "Groceries")
        self.assertEqual(self.c.categorize("PETRO-CANADA 10234", d("-50")), "Transport")

    def test_matching_ignores_case(self):
        self.assertEqual(self.c.categorize("tim hortons #12", d("-3")), "Dining")

    def test_longest_keyword_wins(self):
        self.assertEqual(self.c.categorize("UBER EATS ORDER", d("-20")), "Dining")
        self.assertEqual(self.c.categorize("UBER TRIP", d("-12")), "Transport")

    def test_keywords_must_start_a_word(self):
        # "rent" is a Housing keyword but must not match inside "CURRENT"
        self.assertEqual(self.c.categorize("CURRENT ACCOUNT TOPUP", d("-5")), "Uncategorized")
        self.assertEqual(self.c.categorize("RENT OAK APARTMENTS", d("-1150")), "Housing")

    def test_money_in_with_no_rule_is_income(self):
        self.assertEqual(self.c.categorize("MYSTERY CREDIT", d("25")), "Income")

    def test_money_out_with_no_rule_is_uncategorized(self):
        self.assertEqual(self.c.categorize("MYSTERY SHOP", d("-25")), "Uncategorized")

    def test_income_keywords_only_apply_to_money_in(self):
        self.assertEqual(self.c.categorize("PAYROLL DEPOSIT", d("1850")), "Income")
        self.assertEqual(self.c.categorize("PAYROLL SERVICE FEE", d("-5")), "Uncategorized")

    def test_transfers(self):
        self.assertEqual(self.c.categorize("VISA PAYMENT - THANK YOU", d("-400")), "Transfers")

    def test_user_rules_beat_default_rules(self):
        c = Categorizer(user_rules={"Gifts": ["tim hortons"]})
        self.assertEqual(c.categorize("TIM HORTONS #1", d("-3")), "Gifts")

    def test_regular_expression_rules(self):
        c = Categorizer(user_rules={"Shopping": [r"re:^amzn\s"]})
        self.assertEqual(c.categorize("AMZN MKTP", d("-9")), "Shopping")
        self.assertEqual(c.categorize("XAMZN MKTP", d("-9")), "Uncategorized")


class RulesFileTests(unittest.TestCase):
    def test_validate_accepts_good_rules_and_strips_text(self):
        self.assertEqual(validate_rules({" Food ": [" pizza "]}), {"Food": ["pizza"]})

    def test_validate_rejects_bad_shapes(self):
        for bad in [[], {"A": "pizza"}, {"A": [1]}, {"": ["x"]}, {"A": [""]}]:
            with self.subTest(bad=bad):
                with self.assertRaises(RulesError):
                    validate_rules(bad)

    def test_missing_file_means_no_rules(self):
        self.assertEqual(load_rules(Path(tempfile.mkdtemp()) / "none.json"), {})

    def test_broken_json_is_an_error(self):
        path = Path(tempfile.mkdtemp()) / "rules.json"
        path.write_text("{not json", encoding="utf-8")
        with self.assertRaises(RulesError):
            load_rules(path)

    def test_save_and_load_round_trip(self):
        path = Path(tempfile.mkdtemp()) / "sub" / "rules.json"
        save_rules(path, {"Food": ["pizza"]})
        self.assertEqual(load_rules(path), {"Food": ["pizza"]})
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"Food": ["pizza"]})

    def test_add_rule(self):
        rules = {}
        self.assertTrue(add_rule(rules, "Food", "Pizza"))
        self.assertFalse(add_rule(rules, "Food", "pizza"))  # same keyword, different case
        self.assertEqual(rules, {"Food": ["Pizza"]})

    def test_add_rule_rejects_empty_and_bad_regex(self):
        with self.assertRaises(RulesError):
            add_rule({}, "", "x")
        with self.assertRaises(RulesError):
            add_rule({}, "Food", "re:(")


class NormalizeMerchantTests(unittest.TestCase):
    def test_examples(self):
        cases = {
            "WALMART SUPERCENTER #1234 WINNIPEG MB": "walmart supercenter",
            "NETFLIX.COM 866-579-7172 ON": "netflix",
            "SAVE-ON-FOODS 6677": "saveonfoods",
            "POS PURCHASE TIM HORTONS #0721": "tim hortons",
            "PRE-AUTHORIZED DEBIT TELUS MOBILITY": "telus mobility",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(normalize_merchant(raw), expected)

    def test_same_merchant_different_store_numbers_group_together(self):
        self.assertEqual(normalize_merchant("SUPERSTORE #1"), normalize_merchant("SUPERSTORE #2222"))

    def test_falls_back_to_the_original_when_nothing_is_left(self):
        self.assertEqual(normalize_merchant("12345"), "12345")


if __name__ == "__main__":
    unittest.main()
