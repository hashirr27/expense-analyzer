"""Saves transactions in a local SQLite database (a single file, no server needed)."""

from __future__ import annotations

import datetime as dt
import sqlite3
from decimal import Decimal
from pathlib import Path
from typing import Iterable

from .models import Transaction
from .parser import to_cents

SCHEMA = """
CREATE TABLE IF NOT EXISTS transactions (
    id          TEXT PRIMARY KEY,
    date        TEXT NOT NULL,          -- ISO format, so sorting as text sorts by date
    description TEXT NOT NULL,
    cents       INTEGER NOT NULL,       -- money as whole cents: no floating point rounding
    category    TEXT NOT NULL,
    manual      INTEGER NOT NULL DEFAULT 0  -- 1 if the user chose the category by hand
);
CREATE INDEX IF NOT EXISTS idx_transactions_date ON transactions(date);
"""


class Storage:
    """A thin wrapper around the SQLite file. Use it as a context manager:

        with Storage("data.db") as db:
            db.add_many(transactions)
    """

    def __init__(self, path: str | Path = ":memory:"):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path))
        self._conn.executescript(SCHEMA)

    def __enter__(self) -> "Storage":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def close(self) -> None:
        self._conn.close()

    # -------------------------------------------------------------- writing

    def add_many(self, transactions: Iterable[Transaction]) -> tuple[int, int]:
        """Insert transactions. Returns (added, duplicates). Existing ids are left untouched."""
        added = duplicates = 0
        with self._conn:  # one transaction: all rows are saved, or none if something fails
            for t in transactions:
                cursor = self._conn.execute(
                    "INSERT OR IGNORE INTO transactions (id, date, description, cents, category) VALUES (?, ?, ?, ?, ?)",
                    (t.id, t.date.isoformat(), t.description, to_cents(t.amount), t.category),
                )
                if cursor.rowcount:
                    added += 1
                else:
                    duplicates += 1
        return added, duplicates

    def set_category(self, transaction_id: str, category: str) -> bool:
        """Set a category by hand. Manual choices are never overwritten by recategorize()."""
        with self._conn:
            cursor = self._conn.execute(
                "UPDATE transactions SET category = ?, manual = 1 WHERE id = ?", (category, transaction_id)
            )
        return cursor.rowcount == 1

    def recategorize(self, categorize) -> int:
        """Re-run categorization on every transaction not set by hand.

        `categorize` is a function (description, amount) -> category.
        Returns how many transactions changed category.
        """
        changed = 0
        with self._conn:
            rows = self._conn.execute("SELECT id, description, cents, category FROM transactions WHERE manual = 0").fetchall()
            for tid, description, cents, old in rows:
                new = categorize(description, Decimal(cents) / 100)
                if new != old:
                    self._conn.execute("UPDATE transactions SET category = ? WHERE id = ?", (new, tid))
                    changed += 1
        return changed

    # -------------------------------------------------------------- reading

    def all(self, *, month: str | None = None, category: str | None = None) -> list[Transaction]:
        """Return transactions oldest first, optionally for one month ('2026-05') or category."""
        sql = "SELECT id, date, description, cents, category FROM transactions"
        clauses, params = [], []
        if month:
            clauses.append("substr(date, 1, 7) = ?")
            params.append(month)
        if category:
            clauses.append("lower(category) = lower(?)")
            params.append(category)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY date, id"
        return [
            Transaction(dt.date.fromisoformat(d), desc, Decimal(cents) / 100, category=cat, id=tid)
            for tid, d, desc, cents, cat in self._conn.execute(sql, params)
        ]

    def find_by_prefix(self, prefix: str) -> list[Transaction]:
        """Find transactions whose id starts with `prefix` (so users can type a short id)."""
        like = prefix.replace("%", "").replace("_", "") + "%"
        return [
            Transaction(dt.date.fromisoformat(d), desc, Decimal(cents) / 100, category=cat, id=tid)
            for tid, d, desc, cents, cat in self._conn.execute(
                "SELECT id, date, description, cents, category FROM transactions WHERE id LIKE ?", (like,)
            )
        ]

    def count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]

    def clear(self) -> None:
        with self._conn:
            self._conn.execute("DELETE FROM transactions")
