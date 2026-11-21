import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from expense_analyzer.storage import Storage

from helpers import tx


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.db = Storage(":memory:")
        self.addCleanup(self.db.close)

    def test_add_and_read_back(self):
        added, dupes = self.db.add_many([tx("2026-05-02", "B", "-2.00", "Dining"), tx("2026-05-01", "A", "-1.50", "Fees")])
        self.assertEqual((added, dupes), (2, 0))
        rows = self.db.all()
        self.assertEqual([r.description for r in rows], ["A", "B"])  # oldest first
        self.assertEqual(rows[0].amount, Decimal("-1.50"))
        self.assertEqual(rows[0].category, "Fees")

    def test_same_id_is_not_added_twice(self):
        t = tx("2026-05-01", "A", "-1.00")
        self.db.add_many([t])
        self.assertEqual(self.db.add_many([t]), (0, 1))
        self.assertEqual(self.db.count(), 1)

    def test_money_is_exact(self):
        self.db.add_many([tx("2026-05-01", "A", "-0.10"), tx("2026-05-02", "B", "-0.20")])
        self.assertEqual(sum(t.amount for t in self.db.all()), Decimal("-0.30"))

    def test_filters(self):
        self.db.add_many([
            tx("2026-05-01", "A", "-1", "Dining"),
            tx("2026-06-01", "B", "-2", "Dining"),
            tx("2026-06-02", "C", "-3", "Fees"),
        ])
        self.assertEqual([t.description for t in self.db.all(month="2026-06")], ["B", "C"])
        self.assertEqual([t.description for t in self.db.all(category="dining")], ["A", "B"])  # any letter case

    def test_recategorize_changes_only_non_manual_rows(self):
        a, b = tx("2026-05-01", "A", "-1", "Uncategorized"), tx("2026-05-02", "B", "-2", "Uncategorized")
        self.db.add_many([a, b])
        self.assertTrue(self.db.set_category(b.id, "Gifts"))
        changed = self.db.recategorize(lambda description, amount: "Food")
        self.assertEqual(changed, 1)
        by_name = {t.description: t.category for t in self.db.all()}
        self.assertEqual(by_name, {"A": "Food", "B": "Gifts"})

    def test_set_category_on_unknown_id(self):
        self.assertFalse(self.db.set_category("nope", "X"))

    def test_find_by_prefix(self):
        t = tx("2026-05-01", "A", "-1")
        self.db.add_many([t])
        self.assertEqual(len(self.db.find_by_prefix(t.id[:4])), 1)
        self.assertEqual(self.db.find_by_prefix("zzzz"), [])
        self.assertEqual(self.db.find_by_prefix("%"), self.db.find_by_prefix(""))  # wildcard characters are stripped

    def test_clear(self):
        self.db.add_many([tx("2026-05-01", "A", "-1")])
        self.db.clear()
        self.assertEqual(self.db.count(), 0)

    def test_data_survives_closing_the_file(self):
        path = Path(tempfile.mkdtemp()) / "nested" / "data.db"
        with Storage(path) as first:
            first.add_many([tx("2026-05-01", "A", "-1")])
        with Storage(path) as second:
            self.assertEqual(second.count(), 1)


if __name__ == "__main__":
    unittest.main()
