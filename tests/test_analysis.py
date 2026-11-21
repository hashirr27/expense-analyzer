import datetime as dt
import unittest
from decimal import Decimal

from expense_analyzer import analysis

from helpers import tx


class MonthlySummaryTests(unittest.TestCase):
    def test_income_spending_and_net(self):
        rows = [
            tx("2026-05-01", "PAY", "1000", "Income"),
            tx("2026-05-02", "RENT", "-400", "Housing"),
            tx("2026-05-03", "FOOD", "-100.50", "Groceries"),
        ]
        (m,) = analysis.monthly_summary(rows)
        self.assertEqual((m.month, m.income, m.spending, m.net), ("2026-05", Decimal("1000"), Decimal("500.50"), Decimal("499.50")))

    def test_transfers_are_not_spending_or_income(self):
        rows = [tx("2026-05-01", "VISA PAYMENT", "-400", "Transfers"), tx("2026-05-02", "FRIEND", "60", "Transfers")]
        (m,) = analysis.monthly_summary(rows)
        self.assertEqual((m.income, m.spending), (Decimal("0"), Decimal("0")))

    def test_refund_reduces_spending(self):
        rows = [tx("2026-05-01", "SHOP", "-100", "Shopping"), tx("2026-05-05", "SHOP REFUND", "30", "Shopping")]
        self.assertEqual(analysis.monthly_summary(rows)[0].spending, Decimal("70"))

    def test_months_without_transactions_are_included(self):
        rows = [tx("2026-11-01", "A", "-1", "Fees"), tx("2027-02-01", "B", "-1", "Fees")]
        self.assertEqual([m.month for m in analysis.monthly_summary(rows)], ["2026-11", "2026-12", "2027-01", "2027-02"])

    def test_empty(self):
        self.assertEqual(analysis.monthly_summary([]), [])
        self.assertEqual(analysis.average_monthly_spending([]), Decimal("0"))
        self.assertIsNone(analysis.savings_rate([]))

    def test_average_and_savings_rate(self):
        rows = [
            tx("2026-05-01", "PAY", "1000", "Income"), tx("2026-05-02", "X", "-200", "Fees"),
            tx("2026-06-01", "PAY", "1000", "Income"), tx("2026-06-02", "Y", "-400", "Fees"),
        ]
        self.assertEqual(analysis.average_monthly_spending(rows), Decimal("300.00"))
        self.assertAlmostEqual(analysis.savings_rate(rows), 0.7)

    def test_savings_rate_is_none_without_income(self):
        self.assertIsNone(analysis.savings_rate([tx("2026-05-01", "X", "-5", "Fees")]))


class BreakdownTests(unittest.TestCase):
    ROWS = [
        tx("2026-05-01", "A", "-60", "Groceries"),
        tx("2026-05-02", "B", "-30", "Dining"),
        tx("2026-06-01", "C", "-10", "Dining"),
        tx("2026-06-02", "PAY", "500", "Income"),
    ]

    def test_sorted_with_shares_that_add_to_100(self):
        result = analysis.category_breakdown(self.ROWS)
        self.assertEqual([c.category for c in result], ["Groceries", "Dining"])
        self.assertEqual(result[1].count, 2)
        self.assertAlmostEqual(sum(c.share for c in result), 100)

    def test_month_filter(self):
        result = analysis.category_breakdown(self.ROWS, "2026-06")
        self.assertEqual([(c.category, c.total) for c in result], [("Dining", Decimal("10.00"))])

    def test_fully_refunded_category_disappears(self):
        rows = [tx("2026-05-01", "S", "-20", "Shopping"), tx("2026-05-02", "R", "20", "Shopping")]
        self.assertEqual(analysis.category_breakdown(rows), [])

    def test_top_merchants_groups_store_numbers(self):
        rows = [
            tx("2026-05-01", "SUPERSTORE #1", "-50", "Groceries"),
            tx("2026-05-08", "SUPERSTORE #22", "-70", "Groceries"),
            tx("2026-05-09", "TIM HORTONS #1", "-5", "Dining"),
        ]
        top = analysis.top_merchants(rows, limit=1)
        self.assertEqual((top[0].merchant, top[0].total, top[0].count), ("superstore", Decimal("120.00"), 2))


def monthly_series(description: str, amount: str, months, day: int = 5, category: str = "Subscriptions"):
    return [tx(f"2026-{m:02d}-{day:02d}", description, amount, category) for m in months]


class RecurringTests(unittest.TestCase):
    def test_monthly_subscription_is_found(self):
        found = analysis.find_recurring(monthly_series("NETFLIX.COM 866", "-16.49", range(5, 11)))
        self.assertEqual(len(found), 1)
        r = found[0]
        self.assertEqual((r.merchant, r.period, r.amount, r.occurrences), ("netflix", "monthly", Decimal("16.49"), 6))
        self.assertEqual(r.monthly_cost, Decimal("16.49"))
        self.assertEqual(r.last_date, dt.date(2026, 10, 5))
        self.assertGreater(r.next_expected, r.last_date)

    def test_variable_bills_are_not_reported(self):
        amounts = ["-60", "-110", "-75", "-140", "-55", "-95"]
        rows = [tx(f"2026-{m:02d}-22", "MANITOBA HYDRO", a, "Utilities") for m, a in zip(range(5, 11), amounts)]
        self.assertEqual(analysis.find_recurring(rows), [])

    def test_two_charges_are_not_enough_for_monthly(self):
        self.assertEqual(analysis.find_recurring(monthly_series("GYM", "-30", [5, 6])), [])

    def test_weekly_charges(self):
        rows = [tx((dt.date(2026, 5, 1) + dt.timedelta(days=7 * i)).isoformat(), "CLASS", "-12", "Fitness") for i in range(6)]
        (r,) = analysis.find_recurring(rows)
        self.assertEqual(r.period, "weekly")
        self.assertEqual(r.monthly_cost, Decimal("52.00"))  # 12 * 52 / 12

    def test_yearly_needs_only_two_charges(self):
        rows = [tx("2024-03-05", "ADOBE", "-240", "Subscriptions"), tx("2025-03-05", "ADOBE", "-240", "Subscriptions")]
        (r,) = analysis.find_recurring(rows)
        self.assertEqual((r.period, r.monthly_cost), ("yearly", Decimal("20.00")))

    def test_irregular_timing_is_not_recurring(self):
        days = ["2026-05-01", "2026-05-04", "2026-07-20", "2026-07-22", "2026-10-30"]
        self.assertEqual(analysis.find_recurring([tx(d, "RANDOM SHOP", "-20", "Shopping") for d in days]), [])

    def test_income_and_transfers_are_ignored(self):
        rows = monthly_series("PAYROLL", "1000", range(5, 11), category="Income")
        rows += monthly_series("VISA PAYMENT", "-400", range(5, 11), category="Transfers")
        self.assertEqual(analysis.find_recurring(rows), [])

    def test_sorted_by_monthly_cost(self):
        rows = monthly_series("SMALL", "-5", range(5, 11)) + monthly_series("BIG", "-50", range(5, 11), day=9)
        self.assertEqual([r.merchant for r in analysis.find_recurring(rows)], ["big", "small"])


class AnomalyTests(unittest.TestCase):
    def shopping(self, amounts):
        return [tx(f"2026-05-{i + 1:02d}", f"STORE{chr(65 + i)}", f"-{a}", "Shopping") for i, a in enumerate(amounts)]

    def test_a_huge_purchase_is_flagged(self):
        rows = self.shopping([20, 25, 30, 28, 22, 35, 1200])
        (a,) = analysis.find_anomalies(rows)
        self.assertEqual(a.transaction.amount, Decimal("-1200"))
        self.assertEqual(a.typical, Decimal("28.00"))

    def test_normal_variation_is_not_flagged(self):
        self.assertEqual(analysis.find_anomalies(self.shopping([20, 25, 30, 28, 22, 35, 40])), [])

    def test_small_absolute_amounts_are_ignored(self):
        # 10x the usual, but only $40, below the $50 floor
        self.assertEqual(analysis.find_anomalies(self.shopping([4, 4, 4, 5, 4, 4, 40])), [])

    def test_too_few_purchases_to_judge(self):
        self.assertEqual(analysis.find_anomalies(self.shopping([20, 25, 900])), [])

    def test_judged_against_the_same_merchant_when_there_is_history(self):
        # Coffee at $4 and a restaurant at about $45: the restaurant meals are normal FOR THAT RESTAURANT.
        coffee = [tx(f"2026-05-{i + 1:02d}", "COFFEE BAR", "-4", "Dining") for i in range(10)]
        dinners = [tx(f"2026-06-{i + 1:02d}", "BIG RESTAURANT", a, "Dining") for i, a in enumerate(["-45", "-52", "-48", "-50", "-47", "-55"])]
        self.assertEqual(analysis.find_anomalies(coffee + dinners), [])

    def test_identical_history_then_a_jump(self):
        rows = [tx(f"2026-05-{i + 1:02d}", "STREAMING", "-20", "Subscriptions") for i in range(6)]
        rows.append(tx("2026-06-01", "STREAMING", "-200", "Subscriptions"))
        (a,) = analysis.find_anomalies(rows)
        self.assertEqual(a.transaction.amount, Decimal("-200"))

    def test_income_is_never_flagged(self):
        rows = [tx(f"2026-05-{i + 1:02d}", "PAY", "1000", "Income") for i in range(6)] + [tx("2026-06-01", "PAY", "9000", "Income")]
        self.assertEqual(analysis.find_anomalies(rows), [])


if __name__ == "__main__":
    unittest.main()
