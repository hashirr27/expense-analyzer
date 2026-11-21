"""Reads a bank CSV file and turns it into Transaction objects.

Every bank exports a slightly different CSV, so this module has to work out:
  - which row is the header (some banks put a few lines of text above it),
  - which column is the date / description / amount (or separate debit and credit columns),
  - how dates are written (2026-05-01, 05/01/2026, 01/05/2026, "May 1, 2026"...),
  - how amounts are written ($1,234.56, (45.10), -45.10, 45.10-, 1.234,56).
Rows that cannot be read are skipped and reported, instead of crashing the whole import.
"""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import io
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .models import Transaction


class ParseError(ValueError):
    """The file as a whole cannot be understood (no header, no date column, ...)."""


@dataclass
class ParseResult:
    transactions: list[Transaction]
    skipped: list[tuple[int, str]] = field(default_factory=list)  # (line number, reason)
    date_format: str = ""           # the strptime format that was used
    date_format_assumed: bool = False  # True when day/month order was a guess


# ---------------------------------------------------------------- amounts


def parse_amount(text: str) -> Decimal:
    """Turn text like '$1,234.56', '(45.10)', '-45.10' or '45.10-' into a Decimal.

    Raises ValueError if the text is not a number.
    """
    original = text
    s = text.strip()
    if not s:
        raise ValueError("empty amount")

    negative = False
    if s.startswith("(") and s.endswith(")"):  # accounting style: (45.10) means -45.10
        negative = True
        s = s[1:-1]
    s = re.sub(r"[^\d.,\-+]", "", s)  # drop $, spaces, letters like CAD
    if "-" in s:
        negative = True
        s = s.replace("-", "")
    s = s.replace("+", "")

    # Work out which of "," and "." is the decimal separator.
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):  # 1.234,56 (European)
            s = s.replace(".", "").replace(",", ".")
        else:  # 1,234.56
            s = s.replace(",", "")
    elif "," in s:
        head, _, tail = s.rpartition(",")
        if len(tail) == 2 and "," not in head:  # 12,50 means 12.50
            s = head + "." + tail
        else:  # 1,234 or 1,234,567 are thousands separators
            s = s.replace(",", "")

    if not re.fullmatch(r"\d+\.?\d*|\.\d+", s):
        raise ValueError(f"not a valid amount: {original!r}")
    try:
        value = Decimal(s)
    except InvalidOperation:  # pragma: no cover - the regex above already guards this
        raise ValueError(f"not a valid amount: {original!r}") from None
    return -value if negative else value


# ---------------------------------------------------------------- dates

_NAMED_FORMATS = ["%b %d, %Y", "%B %d, %Y", "%d %b %Y", "%d %B %Y", "%d-%b-%Y", "%d-%b-%y", "%b %d %Y"]
_NUMERIC = re.compile(r"^(\d{1,4})([/\-.])(\d{1,2})\2(\d{1,4})$")


def _clean_date_text(text: str) -> str:
    """Remove a time of day if the bank included one ('2026-05-01 14:30:00')."""
    s = text.strip()
    s = re.sub(r"[T\s]+\d{1,2}:\d{2}(:\d{2})?(\.\d+)?\s*(AM|PM|Z)?$", "", s, flags=re.IGNORECASE)
    return s.strip()


def detect_date_format(samples: list[str]) -> tuple[str, bool]:
    """Work out the date format from every date in the file.

    Returns (strptime_format, assumed). `assumed` is True when the file is
    ambiguous (every date like 03/04/2026 could be March 4 or April 3).
    """
    cleaned = [_clean_date_text(s) for s in samples if s.strip()]
    if not cleaned:
        raise ParseError("The file has no dates.")

    matches = [_NUMERIC.match(s) for s in cleaned]
    if all(matches):
        seps = {m.group(2) for m in matches}  # type: ignore[union-attr]
        if len(seps) == 1:
            sep = seps.pop()
            first = [m.group(1) for m in matches]  # type: ignore[union-attr]
            if all(len(a) == 4 for a in first):  # 2026-05-01
                return f"%Y{sep}%m{sep}%d", False
            last = [m.group(4) for m in matches]  # type: ignore[union-attr]
            year = "%Y" if all(len(c) == 4 for c in last) else "%y"
            a_values = [int(m.group(1)) for m in matches]  # type: ignore[union-attr]
            b_values = [int(m.group(3)) for m in matches]  # type: ignore[union-attr]
            if any(a > 12 for a in a_values):  # first number can only be a day
                return f"%d{sep}%m{sep}{year}", False
            if any(b > 12 for b in b_values):  # second number can only be a day
                return f"%m{sep}%d{sep}{year}", False
            return f"%m{sep}%d{sep}{year}", True  # truly ambiguous: assume month first

    for fmt in _NAMED_FORMATS:
        try:
            for s in cleaned:
                dt.datetime.strptime(s, fmt)
            return fmt, False
        except ValueError:
            continue
    raise ParseError(
        "Could not work out the date format. Try passing it with --date-format, "
        "for example --date-format %Y-%m-%d"
    )


def parse_date(text: str, fmt: str) -> dt.date:
    return dt.datetime.strptime(_clean_date_text(text), fmt).date()


# ---------------------------------------------------------------- columns

ROLE_SYNONYMS: dict[str, set[str]] = {
    "date": {"date", "transaction date", "trans date", "posted date", "posting date", "post date", "booking date"},
    "description": {
        "description", "details", "memo", "payee", "merchant", "name", "narrative",
        "transaction description", "transaction details", "particulars", "reference",
    },
    "amount": {"amount", "amt", "transaction amount", "value"},
    "debit": {"debit", "debits", "withdrawal", "withdrawals", "money out", "paid out", "cad$ debit"},
    "credit": {"credit", "credits", "deposit", "deposits", "money in", "paid in", "cad$ credit"},
}


def _norm(header: str) -> str:
    return re.sub(r"\s+", " ", header.strip().lower())


def detect_columns(header: list[str], overrides: dict[str, str] | None = None) -> dict[str, int]:
    """Map each role (date, description, amount, debit, credit) to a column index.

    `overrides` lets the user name a column explicitly: {"date": "Posted On"}.
    Returns only the roles that were found. Raises ParseError for an unknown override.
    """
    normalized = [_norm(h) for h in header]
    found: dict[str, int] = {}

    for role, wanted in (overrides or {}).items():
        if wanted is None:
            continue
        target = _norm(wanted)
        if target not in normalized:
            raise ParseError(f"Column {wanted!r} not found. The file has: {', '.join(h.strip() for h in header)}")
        found[role] = normalized.index(target)

    for role, synonyms in ROLE_SYNONYMS.items():
        if role in found:
            continue
        for i, name in enumerate(normalized):
            if name in synonyms and i not in found.values():
                found[role] = i
                break
    # Last resort for the date column: any header that contains the word "date".
    if "date" not in found:
        for i, name in enumerate(normalized):
            if "date" in name and i not in found.values():
                found["date"] = i
                break
    return found


def _is_usable(columns: dict[str, int]) -> bool:
    return "date" in columns and "description" in columns and ("amount" in columns or "debit" in columns or "credit" in columns)


# ---------------------------------------------------------------- ids


def transaction_id(date: dt.date, description: str, cents: int, occurrence: int) -> str:
    """A short stable fingerprint for a transaction.

    `occurrence` counts identical rows on the same day (two $4.50 coffees), so they
    stay separate, while importing the same file twice produces the same ids.
    """
    key = f"{date.isoformat()}|{' '.join(description.lower().split())}|{cents}|{occurrence}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]


def to_cents(amount: Decimal) -> int:
    return int((amount * 100).to_integral_value())


# ---------------------------------------------------------------- main entry points


def _split_rows(text: str) -> list[list[str]]:
    text = text.lstrip("﻿")  # some programs add an invisible byte-order mark
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    return list(csv.reader(io.StringIO(text, newline=""), dialect))


def parse_csv_text(
    text: str,
    *,
    date_format: str | None = None,
    expenses_positive: bool = False,
    column_overrides: dict[str, str] | None = None,
) -> ParseResult:
    """Parse the text of a CSV statement.

    expenses_positive: set this for banks (often credit cards) that list purchases as
    positive numbers. They are flipped so expenses are negative, like everywhere else.
    """
    rows = _split_rows(text)

    # 1. Find the header row (it might not be the first line).
    header_index = None
    columns: dict[str, int] = {}
    for i, row in enumerate(rows[:15]):
        if not any(cell.strip() for cell in row):
            continue
        candidate = detect_columns(row, column_overrides)
        if _is_usable(candidate):
            header_index, columns = i, candidate
            break
    if header_index is None:
        first = ", ".join(c.strip() for c in next((r for r in rows if any(c.strip() for c in r)), []))
        raise ParseError(
            "Could not find the header row with a date, a description and an amount "
            f"(or debit/credit) column. The first line was: {first or '(empty file)'}. "
            "You can name columns with --date-col, --description-col, --amount-col, --debit-col, --credit-col."
        )

    # 2. Collect the data rows, remembering their line numbers for error messages.
    needed = max(columns.values())
    data: list[tuple[int, list[str]]] = []
    for line_offset, row in enumerate(rows[header_index + 1:], start=header_index + 2):
        if not any(cell.strip() for cell in row):
            continue  # blank line
        data.append((line_offset, row))

    # 3. Work out the date format from all the dates together.
    date_texts = [row[columns["date"]] for _, row in data if len(row) > columns["date"]]
    assumed = False
    if date_format is None:
        date_format, assumed = detect_date_format(date_texts)

    # 4. Convert each row.
    transactions: list[Transaction] = []
    skipped: list[tuple[int, str]] = []
    seen: dict[tuple[str, str, int], int] = {}
    for line_no, row in data:
        if len(row) <= needed:
            skipped.append((line_no, "row has too few columns"))
            continue
        try:
            when = parse_date(row[columns["date"]], date_format)
        except ValueError:
            skipped.append((line_no, f"bad date {row[columns['date']]!r}"))
            continue
        description = " ".join(row[columns["description"]].split())
        if not description:
            skipped.append((line_no, "missing description"))
            continue
        try:
            if "amount" in columns:
                amount = parse_amount(row[columns["amount"]])
                if expenses_positive:
                    amount = -amount
            else:
                debit = parse_amount(row[columns["debit"]]) if "debit" in columns and row[columns["debit"]].strip() else Decimal(0)
                credit = parse_amount(row[columns["credit"]]) if "credit" in columns and row[columns["credit"]].strip() else Decimal(0)
                amount = abs(credit) - abs(debit)
        except ValueError as exc:
            skipped.append((line_no, str(exc)))
            continue
        if amount == 0:
            skipped.append((line_no, "zero amount"))
            continue

        cents = to_cents(amount)
        key = (when.isoformat(), " ".join(description.lower().split()), cents)
        occurrence = seen.get(key, 0)
        seen[key] = occurrence + 1
        transactions.append(
            Transaction(when, description, amount.quantize(Decimal("0.01")), id=transaction_id(when, description, cents, occurrence))
        )

    return ParseResult(transactions, skipped, date_format, assumed)


def parse_csv_file(path: str | Path, **options) -> ParseResult:
    """Read a CSV file from disk (handles UTF-8 with or without a BOM, and Windows-1252)."""
    raw = Path(path).read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1252")  # common for Excel exports on Windows
    return parse_csv_text(text, **options)
