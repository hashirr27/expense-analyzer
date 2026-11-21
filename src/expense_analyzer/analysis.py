"""The calculations: monthly totals, category breakdown, recurring charges, unusual purchases.

These are plain functions that take a list of Transactions and return results.
They never touch the database or the screen, which makes them easy to test.

What counts as what:
  income   = transactions in the "Income" category
  spending = everything else except "Transfers" (moving your own money around is not spending)
  A refund (positive amount in a spending category) reduces that category's spending.
"""

from __future__ import annotations

import datetime as dt
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from decimal import Decimal

from .categorizer import normalize_merchant
from .models import INCOME, TRANSFERS, Transaction

ZERO = Decimal("0")
CENT = Decimal("0.01")


def is_spending(t: Transaction) -> bool:
    return t.category not in (INCOME, TRANSFERS)


def _q(value: Decimal) -> Decimal:
    return value.quantize(CENT)


# ---------------------------------------------------------------- monthly summary


@dataclass(frozen=True)
class MonthSummary:
    month: str  # 'YYYY-MM'
    income: Decimal
    spending: Decimal
    net: Decimal  # income - spending (positive means you saved money)


def _month_range(first: str, last: str) -> list[str]:
    """Every month from first to last inclusive: ('2026-01', '2026-03') -> 3 months."""
    y, m = int(first[:4]), int(first[5:7])
    end_y, end_m = int(last[:4]), int(last[5:7])
    months = []
    while (y, m) <= (end_y, end_m):
        months.append(f"{y:04d}-{m:02d}")
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return months


def monthly_summary(transactions: list[Transaction]) -> list[MonthSummary]:
    """Income, spending and net per month. Months with no transactions are included as zeros."""
    if not transactions:
        return []
    income: dict[str, Decimal] = defaultdict(lambda: ZERO)
    spending: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for t in transactions:
        if t.category == INCOME:
            income[t.month] += t.amount
        elif is_spending(t):
            spending[t.month] -= t.amount  # amounts are negative for expenses, so flip the sign
    months = sorted({t.month for t in transactions})
    return [
        MonthSummary(m, _q(income[m]), _q(spending[m]), _q(income[m] - spending[m]))
        for m in _month_range(months[0], months[-1])
    ]


def average_monthly_spending(transactions: list[Transaction]) -> Decimal:
    summary = monthly_summary(transactions)
    if not summary:
        return ZERO
    return _q(sum((s.spending for s in summary), ZERO) / len(summary))


def savings_rate(transactions: list[Transaction]) -> float | None:
    """Fraction of income that was not spent, or None if there was no income."""
    summary = monthly_summary(transactions)
    income = sum((s.income for s in summary), ZERO)
    if income <= 0:
        return None
    spent = sum((s.spending for s in summary), ZERO)
    return float((income - spent) / income)


# ---------------------------------------------------------------- categories and merchants


@dataclass(frozen=True)
class CategoryTotal:
    category: str
    total: Decimal
    share: float  # percent of all spending, 0-100
    count: int


def category_breakdown(transactions: list[Transaction], month: str | None = None) -> list[CategoryTotal]:
    """Spending per category, biggest first. Pass month='2026-05' to look at one month."""
    totals: dict[str, Decimal] = defaultdict(lambda: ZERO)
    counts: Counter[str] = Counter()
    for t in transactions:
        if not is_spending(t) or (month and t.month != month):
            continue
        totals[t.category] -= t.amount
        counts[t.category] += 1
    totals = {c: v for c, v in totals.items() if v > 0}  # a category fully refunded disappears
    grand = sum(totals.values(), ZERO)
    result = [
        CategoryTotal(c, _q(v), float(v / grand * 100) if grand else 0.0, counts[c])
        for c, v in totals.items()
    ]
    return sorted(result, key=lambda r: r.total, reverse=True)


@dataclass(frozen=True)
class MerchantTotal:
    merchant: str
    total: Decimal
    count: int


def top_merchants(transactions: list[Transaction], limit: int = 10) -> list[MerchantTotal]:
    """The merchants you spent the most at (names are grouped with normalize_merchant)."""
    totals: dict[str, Decimal] = defaultdict(lambda: ZERO)
    counts: Counter[str] = Counter()
    for t in transactions:
        if t.amount < 0 and is_spending(t):
            key = normalize_merchant(t.description)
            totals[key] -= t.amount
            counts[key] += 1
    ranked = sorted(totals.items(), key=lambda kv: kv[1], reverse=True)[:limit]
    return [MerchantTotal(name, _q(total), counts[name]) for name, total in ranked]


# ---------------------------------------------------------------- recurring charges


@dataclass(frozen=True)
class RecurringCharge:
    merchant: str
    period: str            # weekly, biweekly, monthly or yearly
    amount: Decimal        # the typical charge
    occurrences: int
    last_date: dt.date
    next_expected: dt.date
    monthly_cost: Decimal  # what it costs per month on average


# period name, (min days, max days) for the typical gap, and how many charges fit in a month
_PERIODS = [
    ("weekly", 6, 8, Decimal(52) / 12),
    ("biweekly", 12, 16, Decimal(26) / 12),
    ("monthly", 27, 34, Decimal(1)),
    ("yearly", 355, 375, Decimal(1) / 12),
]


def _classify_gap(days: float) -> tuple[str, int, int, Decimal] | None:
    for period in _PERIODS:
        if period[1] <= days <= period[2]:
            return period
    return None


def find_recurring(transactions: list[Transaction], min_occurrences: int = 3) -> list[RecurringCharge]:
    """Spot subscriptions and other repeating bills.

    A merchant is "recurring" when its charges are about the same amount (within 15%)
    and arrive at a steady interval (weekly, every two weeks, monthly or yearly).
    Variable bills such as electricity are intentionally not reported because their
    amounts change a lot.
    """
    by_merchant: dict[str, list[Transaction]] = defaultdict(list)
    for t in transactions:
        if t.amount < 0 and is_spending(t):
            by_merchant[normalize_merchant(t.description)].append(t)

    found: list[RecurringCharge] = []
    for merchant, items in by_merchant.items():
        items.sort(key=lambda t: t.date)
        if len(items) < 2:
            continue
        gaps = [(b.date - a.date).days for a, b in zip(items, items[1:])]
        typical_gap = statistics.median(gaps)
        period = _classify_gap(typical_gap)
        if period is None:
            continue
        name, low, high, per_month = period
        needed = 2 if name == "yearly" else min_occurrences
        if len(items) < needed:
            continue
        steady = sum(1 for g in gaps if low <= g <= high) / len(gaps)
        amounts = [-t.amount for t in items]
        typical_amount = statistics.median(amounts)
        close = sum(1 for a in amounts if abs(a - typical_amount) <= max(typical_amount * Decimal("0.15"), Decimal("1.00")))
        if steady < 0.7 or close / len(amounts) < 0.7:
            continue
        last = items[-1].date
        found.append(
            RecurringCharge(
                merchant=merchant,
                period=name,
                amount=_q(typical_amount),
                occurrences=len(items),
                last_date=last,
                next_expected=last + dt.timedelta(days=round(typical_gap)),
                monthly_cost=_q(typical_amount * per_month),
            )
        )
    return sorted(found, key=lambda r: r.monthly_cost, reverse=True)


# ---------------------------------------------------------------- unusual purchases


@dataclass(frozen=True)
class Anomaly:
    transaction: Transaction
    typical: Decimal  # the median purchase in that category
    score: float      # how unusual it is (higher = more unusual)


def find_anomalies(
    transactions: list[Transaction],
    min_samples: int = 5,
    threshold: float = 3.5,
    min_amount: Decimal = Decimal("50"),
) -> list[Anomaly]:
    """Find purchases far larger than usual.

    Each purchase is compared with its "peers": other purchases at the same merchant if
    there are at least `min_samples` of them (so a $45 restaurant meal is judged against
    that restaurant, not against $4 coffees), otherwise other purchases in the same category.

    The score is the "modified z-score", based on the median and the median absolute
    deviation (MAD). Unlike the plain average and standard deviation, these are not
    distorted by the very outliers we are trying to find. To be reported, a purchase must
    also be at least `min_amount` and at least 1.5 times its peers' median.
    """
    spending = [t for t in transactions if t.amount < 0 and is_spending(t)]
    by_merchant: dict[str, list[Transaction]] = defaultdict(list)
    by_category: dict[str, list[Transaction]] = defaultdict(list)
    for t in spending:
        by_merchant[normalize_merchant(t.description)].append(t)
        by_category[t.category].append(t)

    stats_cache: dict[tuple[str, str], tuple[float, float] | None] = {}

    def peer_stats(t: Transaction) -> tuple[float, float] | None:
        merchant = normalize_merchant(t.description)
        key = ("merchant", merchant) if len(by_merchant[merchant]) >= min_samples else ("category", t.category)
        if key not in stats_cache:
            group = by_merchant[merchant] if key[0] == "merchant" else by_category[t.category]
            if len(group) < min_samples:
                stats_cache[key] = None
            else:
                amounts = [float(-x.amount) for x in group]
                median = statistics.median(amounts)
                stats_cache[key] = (median, statistics.median(abs(a - median) for a in amounts))
        return stats_cache[key]

    anomalies: list[Anomaly] = []
    for t in spending:
        stats = peer_stats(t)
        if stats is None:
            continue
        median, mad = stats
        amount = float(-t.amount)
        if amount < float(min_amount) or amount < 1.5 * median:
            continue
        if mad == 0:
            # Peers are almost all identical, so MAD gives no scale: use a plain rule instead.
            if amount >= 3 * median:
                anomalies.append(Anomaly(t, _q(Decimal(str(median))), amount / median))
            continue
        score = 0.6745 * (amount - median) / mad
        if score >= threshold:
            anomalies.append(Anomaly(t, _q(Decimal(str(median))), score))
    return sorted(anomalies, key=lambda a: a.score, reverse=True)
