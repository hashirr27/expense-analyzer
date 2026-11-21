"""Small helpers shared by the tests."""

import datetime as dt
from decimal import Decimal

from expense_analyzer.models import Transaction


def tx(date: str, description: str, amount: str, category: str = "Uncategorized") -> Transaction:
    """Build a Transaction quickly: tx('2026-05-01', 'NETFLIX', '-16.49', 'Subscriptions')."""
    d = dt.date.fromisoformat(date)
    return Transaction(d, description, Decimal(amount), category, id=f"{date}-{description}-{amount}")
