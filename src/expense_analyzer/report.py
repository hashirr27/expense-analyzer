"""Turns analysis results into text for the terminal and a self-contained HTML report."""

from __future__ import annotations

import html
from decimal import Decimal

from . import analysis
from .models import Transaction


def money(value: Decimal | int | float) -> str:
    """Format money like $1,234.56 or -$12.30."""
    d = Decimal(str(value)).quantize(Decimal("0.01"))
    sign = "-" if d < 0 else ""
    return f"{sign}${abs(d):,.2f}"


def render_table(headers: list[str], rows: list[list[str]], right_align: set[int] | None = None) -> str:
    """Draw a plain-text table. Columns listed in right_align are right-aligned (use for numbers)."""
    right_align = right_align or set()
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))

    def fmt(row: list[str]) -> str:
        cells = [(c.rjust(widths[i]) if i in right_align else c.ljust(widths[i])) for i, c in enumerate(row)]
        return "  ".join(cells).rstrip()

    lines = [fmt(headers), "  ".join("-" * w for w in widths)]
    lines += [fmt(r) for r in rows]
    return "\n".join(lines)


def bar(value: float, maximum: float, width: int = 24) -> str:
    """A text bar chart segment: ####........"""
    if maximum <= 0:
        return ""
    filled = round(width * value / maximum)
    return "#" * max(0, min(width, filled))


# ---------------------------------------------------------------- terminal output


def monthly_table(transactions: list[Transaction]) -> str:
    summary = analysis.monthly_summary(transactions)
    peak = max((float(s.spending) for s in summary), default=0)
    rows = [[s.month, money(s.income), money(s.spending), money(s.net), bar(float(s.spending), peak)] for s in summary]
    return render_table(["Month", "Income", "Spending", "Net", "Spending chart"], rows, {1, 2, 3})


def category_table(transactions: list[Transaction], month: str | None = None) -> str:
    breakdown = analysis.category_breakdown(transactions, month)
    peak = max((float(c.total) for c in breakdown), default=0)
    rows = [[c.category, money(c.total), f"{c.share:.1f}%", str(c.count), bar(float(c.total), peak)] for c in breakdown]
    return render_table(["Category", "Total", "Share", "Count", ""], rows, {1, 2, 3})


def merchants_table(transactions: list[Transaction], limit: int) -> str:
    rows = [[m.merchant, money(m.total), str(m.count)] for m in analysis.top_merchants(transactions, limit)]
    return render_table(["Merchant", "Total", "Charges"], rows, {1, 2})


def recurring_table(transactions: list[Transaction]) -> str:
    rows = [
        [r.merchant, r.period, money(r.amount), str(r.occurrences), money(r.monthly_cost), r.next_expected.isoformat()]
        for r in analysis.find_recurring(transactions)
    ]
    return render_table(["Merchant", "How often", "Amount", "Seen", "Per month", "Next expected"], rows, {2, 3, 4})


def anomalies_table(transactions: list[Transaction]) -> str:
    rows = [
        [a.transaction.date.isoformat(), a.transaction.description[:40], a.transaction.category,
         money(-a.transaction.amount), money(a.typical)]
        for a in analysis.find_anomalies(transactions)
    ]
    return render_table(["Date", "Description", "Category", "Amount", "Usual"], rows, {3, 4})


def transactions_table(transactions: list[Transaction]) -> str:
    rows = [[t.id[:8], t.date.isoformat(), t.description[:44], t.category, money(t.amount)] for t in transactions]
    return render_table(["Id", "Date", "Description", "Category", "Amount"], rows, {4})


# ---------------------------------------------------------------- HTML report

_CSS = """
:root { --bg:#fff; --fg:#1f2937; --muted:#6b7280; --card:#f3f4f6; --line:#e5e7eb; --in:#0f766e; --out:#c2410c; --bar:#2563eb; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#111827; --fg:#e5e7eb; --muted:#9ca3af; --card:#1f2937; --line:#374151; --in:#2dd4bf; --out:#fb923c; --bar:#60a5fa; }
}
body { font-family: system-ui, sans-serif; background: var(--bg); color: var(--fg); max-width: 860px; margin: 0 auto; padding: 24px 16px 48px; }
h1 { font-size: 1.6rem; margin: 0 0 4px; } h2 { font-size: 1.1rem; margin: 32px 0 10px; }
.sub { color: var(--muted); margin: 0 0 20px; }
.cards { display: flex; gap: 12px; flex-wrap: wrap; }
.card { flex: 1; min-width: 150px; background: var(--card); border-radius: 10px; padding: 12px 14px; }
.card span { display: block; font-size: .8rem; color: var(--muted); } .card strong { font-size: 1.3rem; }
table { width: 100%; border-collapse: collapse; font-size: .92rem; }
th, td { text-align: left; padding: 7px 8px; border-bottom: 1px solid var(--line); } td.num, th.num { text-align: right; }
.row { display: flex; align-items: center; gap: 10px; margin: 6px 0; font-size: .92rem; }
.label { width: 130px; flex: none; } .track { flex: 1; background: var(--card); border-radius: 4px; height: 14px; }
.fill { background: var(--bar); height: 100%; border-radius: 4px; } .val { width: 150px; text-align: right; flex: none; }
.legend span { margin-right: 14px; font-size: .85rem; color: var(--muted); }
.empty { color: var(--muted); }
"""


def _e(value: object) -> str:
    return html.escape(str(value))


def _table(headers: list[str], rows: list[list[str]], numeric: set[int]) -> str:
    if not rows:
        return '<p class="empty">Nothing to show.</p>'
    head = "".join(f'<th class="{"num" if i in numeric else ""}">{_e(h)}</th>' for i, h in enumerate(headers))
    body = "".join(
        "<tr>" + "".join(f'<td class="{"num" if i in numeric else ""}">{_e(c)}</td>' for i, c in enumerate(r)) + "</tr>"
        for r in rows
    )
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def _monthly_svg(summary: list[analysis.MonthSummary]) -> str:
    """Grouped bars: income and spending for each month, drawn as inline SVG (no libraries)."""
    if not summary:
        return '<p class="empty">No data.</p>'
    width, height, pad_left, pad_bottom = 820, 240, 10, 28
    top = max(max(float(s.income), float(s.spending)) for s in summary) or 1
    group = (width - pad_left) / len(summary)
    bar_w = min(28, group / 3)
    parts = [f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="Income and spending per month" width="100%">']
    for i, s in enumerate(summary):
        x = pad_left + i * group + group / 2
        for offset, value, colour, name in ((-bar_w, float(s.income), "var(--in)", "Income"), (0, float(s.spending), "var(--out)", "Spending")):
            h = (height - pad_bottom - 10) * value / top
            parts.append(
                f'<rect x="{x + offset:.1f}" y="{height - pad_bottom - h:.1f}" width="{bar_w - 2:.1f}" height="{h:.1f}" '
                f'fill="{colour}"><title>{_e(s.month)} {name}: {_e(money(value))}</title></rect>'
            )
        parts.append(
            f'<text x="{x:.1f}" y="{height - 8}" text-anchor="middle" font-size="12" fill="currentColor">{_e(s.month)}</text>'
        )
    parts.append("</svg>")
    return "".join(parts)


def html_report(transactions: list[Transaction]) -> str:
    """Build the whole report as one HTML string. Everything user-supplied is escaped."""
    summary = analysis.monthly_summary(transactions)
    breakdown = analysis.category_breakdown(transactions)
    rate = analysis.savings_rate(transactions)
    total_income = sum((s.income for s in summary), Decimal(0))
    total_spending = sum((s.spending for s in summary), Decimal(0))
    period = f"{transactions[0].date.isoformat()} to {transactions[-1].date.isoformat()}" if transactions else "no data"

    category_rows = "".join(
        f'<div class="row"><span class="label">{_e(c.category)}</span>'
        f'<span class="track"><span class="fill" style="display:block;width:{c.share:.1f}%"></span></span>'
        f'<span class="val">{_e(money(c.total))} ({c.share:.0f}%)</span></div>'
        for c in breakdown
    ) or '<p class="empty">No spending found.</p>'

    recurring = analysis.find_recurring(transactions)
    anomalies = analysis.find_anomalies(transactions)

    cards = [
        ("Income", money(total_income)),
        ("Spending", money(total_spending)),
        ("Average per month", money(analysis.average_monthly_spending(transactions))),
        ("Savings rate", f"{rate * 100:.0f}%" if rate is not None else "n/a"),
    ]
    cards_html = "".join(f'<div class="card"><span>{_e(k)}</span><strong>{_e(v)}</strong></div>' for k, v in cards)

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Expense report</title><style>{_CSS}</style></head><body>
<h1>Expense report</h1>
<p class="sub">{_e(period)} &middot; {len(transactions)} transactions</p>
<div class="cards">{cards_html}</div>
<h2>Income and spending by month</h2>
<div class="legend"><span style="color:var(--in)">&#9632; Income</span><span style="color:var(--out)">&#9632; Spending</span></div>
{_monthly_svg(summary)}
<h2>Where the money went</h2>
{category_rows}
<h2>Recurring charges</h2>
{_table(["Merchant", "How often", "Amount", "Per month", "Next expected"],
        [[r.merchant, r.period, money(r.amount), money(r.monthly_cost), r.next_expected.isoformat()] for r in recurring], {2, 3})}
<h2>Unusually large purchases</h2>
{_table(["Date", "Description", "Category", "Amount", "Usual"],
        [[a.transaction.date.isoformat(), a.transaction.description, a.transaction.category,
          money(-a.transaction.amount), money(a.typical)] for a in anomalies], {3, 4})}
<h2>Top merchants</h2>
{_table(["Merchant", "Total", "Charges"], [[m.merchant, money(m.total), str(m.count)] for m in analysis.top_merchants(transactions, 10)], {1, 2})}
</body></html>
"""
