"""The data types shared by every other module."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal

# Special category names the analysis treats differently.
UNCATEGORIZED = "Uncategorized"
INCOME = "Income"        # money coming in (pay, refunds of income, etc.)
TRANSFERS = "Transfers"  # moving money between your own accounts: not real spending or income


@dataclass(frozen=True)
class Transaction:
    """One line from a bank statement.

    Money convention used everywhere in this project:
      negative amount = money out (an expense), positive amount = money in.
    Amounts are Decimal, not float, because floats cannot represent cents exactly.
    """

    date: dt.date
    description: str
    amount: Decimal
    category: str = UNCATEGORIZED
    id: str = ""  # stable fingerprint, used to avoid importing the same row twice

    @property
    def month(self) -> str:
        """The month as 'YYYY-MM', handy for grouping."""
        return self.date.strftime("%Y-%m")
