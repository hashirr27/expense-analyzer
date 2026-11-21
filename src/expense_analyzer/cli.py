"""The command-line interface: reads the command you typed and calls the right code."""

from __future__ import annotations

import argparse
import csv
import dataclasses
import os
import sys
import webbrowser
from pathlib import Path

from . import __version__, analysis, report
from .categorizer import Categorizer, RulesError, add_rule, load_rules, save_rules
from .parser import ParseError, parse_csv_file
from .storage import Storage


class UserError(Exception):
    """Something the user can fix (wrong file name, unknown id...). Shown without a traceback."""


NO_DATA = "No transactions yet. Import a bank statement first:\n  expense-analyzer import path/to/statement.csv"


# ---------------------------------------------------------------- setup helpers


def data_dir(args: argparse.Namespace) -> Path:
    """Where the database and your rules live: --data-dir, else $EXPENSE_ANALYZER_HOME, else ~/.expense_analyzer"""
    chosen = args.data_dir or os.environ.get("EXPENSE_ANALYZER_HOME") or "~/.expense_analyzer"
    return Path(chosen).expanduser()


def make_categorizer(args: argparse.Namespace) -> Categorizer:
    return Categorizer(user_rules=load_rules(data_dir(args) / "rules.json"))


def open_storage(args: argparse.Namespace) -> Storage:
    return Storage(data_dir(args) / "transactions.db")


# ---------------------------------------------------------------- commands


def cmd_import(args: argparse.Namespace) -> int:
    path = Path(args.file)
    if not path.is_file():
        raise UserError(f"File not found: {path}")

    overrides = {
        "date": args.date_col, "description": args.description_col, "amount": args.amount_col,
        "debit": args.debit_col, "credit": args.credit_col,
    }
    result = parse_csv_file(
        path,
        date_format=args.date_format,
        expenses_positive=args.expenses_positive,
        column_overrides=overrides,
    )
    categorizer = make_categorizer(args)
    transactions = [dataclasses.replace(t, category=categorizer.categorize(t.description, t.amount)) for t in result.transactions]

    if args.dry_run:
        added, duplicates = len(transactions), 0
    else:
        with open_storage(args) as db:
            added, duplicates = db.add_many(transactions)

    print(f"Read {len(transactions) + len(result.skipped)} rows from {path.name} (dates: {result.date_format}).")
    print(f"{'Would add' if args.dry_run else 'Added'} {added} new transactions; {duplicates} already imported.")
    if result.date_format_assumed:
        print(f"Warning: every date could be month-first or day-first. Assumed {result.date_format}. "
              "If that is wrong, run again with --date-format (for example %d/%m/%Y).")
    if result.skipped:
        print(f"Skipped {len(result.skipped)} unreadable rows:")
        for line, reason in result.skipped[:5]:
            print(f"  line {line}: {reason}")
        if len(result.skipped) > 5:
            print(f"  ... and {len(result.skipped) - 5} more")
    unknown = sum(1 for t in transactions if t.category == "Uncategorized")
    if unknown:
        print(f"{unknown} transactions are Uncategorized. Teach the tool with:\n"
              "  expense-analyzer list --category Uncategorized\n"
              "  expense-analyzer rules add Groceries \"corner market\"\n"
              "  expense-analyzer recategorize")
    return 0


def cmd_summary(args: argparse.Namespace) -> int:
    with open_storage(args) as db:
        transactions = db.all()
    if not transactions:
        print(NO_DATA)
        return 0
    print("Monthly summary")
    print(report.monthly_table(transactions))
    print()
    print(f"Spending by category{' in ' + args.month if args.month else ''}")
    print(report.category_table(transactions, args.month))
    print()
    rate = analysis.savings_rate(transactions)
    print(f"Average spending per month: {report.money(analysis.average_monthly_spending(transactions))}")
    print(f"Savings rate: {rate * 100:.0f}% of income" if rate is not None else "Savings rate: n/a (no income found)")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    with open_storage(args) as db:
        transactions = db.all(month=args.month, category=args.category)
    if not transactions:
        print("No matching transactions.")
        return 0
    shown = transactions[-args.limit:] if args.limit else transactions
    print(report.transactions_table(shown))
    if len(shown) < len(transactions):
        print(f"\nShowing the latest {len(shown)} of {len(transactions)}. Use --limit 0 to show all.")
    return 0


def _with_data(args: argparse.Namespace):
    with open_storage(args) as db:
        return db.all()


def cmd_merchants(args: argparse.Namespace) -> int:
    transactions = _with_data(args)
    print(report.merchants_table(transactions, args.n) if transactions else NO_DATA)
    return 0


def cmd_recurring(args: argparse.Namespace) -> int:
    transactions = _with_data(args)
    if not transactions:
        print(NO_DATA)
        return 0
    found = analysis.find_recurring(transactions)
    if not found:
        print("No recurring charges found (they need at least 3 similar charges at a steady interval).")
        return 0
    print(report.recurring_table(transactions))
    total = sum((r.monthly_cost for r in found), analysis.ZERO)
    print(f"\nRecurring charges cost about {report.money(total)} per month ({report.money(total * 12)} per year).")
    return 0


def cmd_anomalies(args: argparse.Namespace) -> int:
    transactions = _with_data(args)
    if not transactions:
        print(NO_DATA)
        return 0
    if not analysis.find_anomalies(transactions):
        print("No unusually large purchases found.")
        return 0
    print(report.anomalies_table(transactions))
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    transactions = _with_data(args)
    if not transactions:
        print(NO_DATA)
        return 0
    out = Path(args.output)
    out.write_text(report.html_report(transactions), encoding="utf-8")
    print(f"Report written to {out.resolve()}")
    if args.open:
        webbrowser.open(out.resolve().as_uri())
    return 0


def _safe_cell(text: str) -> str:
    """Stop spreadsheet programs from treating text like '=SUM(...)' as a formula."""
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def cmd_export(args: argparse.Namespace) -> int:
    transactions = _with_data(args)
    with open(args.file, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["id", "date", "description", "amount", "category"])
        for t in transactions:
            writer.writerow([t.id, t.date.isoformat(), _safe_cell(t.description), f"{t.amount:.2f}", _safe_cell(t.category)])
    print(f"Exported {len(transactions)} transactions to {args.file}")
    return 0


def cmd_recategorize(args: argparse.Namespace) -> int:
    categorizer = make_categorizer(args)
    with open_storage(args) as db:
        changed = db.recategorize(categorizer.categorize)
    print(f"Updated {changed} transactions. (Categories you set by hand are never changed.)")
    return 0


def cmd_set_category(args: argparse.Namespace) -> int:
    with open_storage(args) as db:
        matches = db.find_by_prefix(args.id)
        if not matches:
            raise UserError(f"No transaction with an id starting with {args.id!r}. Use 'list' to see ids.")
        if len(matches) > 1:
            raise UserError(f"{len(matches)} transactions match {args.id!r}; type more of the id.")
        db.set_category(matches[0].id, args.category.strip())
    print(f"Set '{matches[0].description}' to {args.category.strip()}.")
    return 0


def cmd_rules(args: argparse.Namespace) -> int:
    path = data_dir(args) / "rules.json"
    rules = load_rules(path)
    if args.rules_command == "list":
        if not rules:
            print(f"You have no custom rules yet ({path}).\nAdd one with: expense-analyzer rules add Groceries \"corner market\"")
            return 0
        for category, keywords in rules.items():
            print(f"{category}: {', '.join(keywords)}")
        return 0
    added = add_rule(rules, args.category, args.keyword)
    if not added:
        print("That keyword is already in your rules.")
        return 0
    save_rules(path, rules)
    print(f"Added '{args.keyword}' to {args.category}. Run 'expense-analyzer recategorize' to apply it to saved transactions.")
    return 0


def cmd_reset(args: argparse.Namespace) -> int:
    if not args.yes:
        raise UserError("This deletes every imported transaction. Run again with --yes to confirm.")
    with open_storage(args) as db:
        db.clear()
    print("All transactions deleted. (Your rules were kept.)")
    return 0


# ---------------------------------------------------------------- argument parsing


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="expense-analyzer",
        description="Import bank statements (CSV), categorize spending, and find subscriptions and unusual purchases.",
    )
    parser.add_argument("--data-dir", help="where to keep the database and rules (default: ~/.expense_analyzer)")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True, metavar="command")

    p = sub.add_parser("import", help="import a bank statement CSV file")
    p.add_argument("file")
    p.add_argument("--date-format", help="how dates are written, e.g. %%Y-%%m-%%d or %%d/%%m/%%Y (default: detect it)")
    p.add_argument("--expenses-positive", action="store_true", help="the bank lists purchases as positive numbers")
    p.add_argument("--date-col", help="name of the date column")
    p.add_argument("--description-col", help="name of the description column")
    p.add_argument("--amount-col", help="name of the amount column")
    p.add_argument("--debit-col", help="name of the debit (money out) column")
    p.add_argument("--credit-col", help="name of the credit (money in) column")
    p.add_argument("--dry-run", action="store_true", help="show what would be imported without saving")
    p.set_defaults(func=cmd_import)

    p = sub.add_parser("summary", help="monthly totals and spending by category")
    p.add_argument("--month", help="limit the category table to one month, e.g. 2026-05")
    p.set_defaults(func=cmd_summary)

    p = sub.add_parser("list", help="list saved transactions")
    p.add_argument("--month", help="only this month, e.g. 2026-05")
    p.add_argument("--category", help="only this category")
    p.add_argument("--limit", type=int, default=30, help="how many to show (default 30, 0 = all)")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("merchants", help="where you spend the most")
    p.add_argument("-n", type=int, default=10, help="how many merchants (default 10)")
    p.set_defaults(func=cmd_merchants)

    p = sub.add_parser("recurring", help="find subscriptions and repeating bills")
    p.set_defaults(func=cmd_recurring)

    p = sub.add_parser("anomalies", help="find unusually large purchases")
    p.set_defaults(func=cmd_anomalies)

    p = sub.add_parser("report", help="write a full HTML report")
    p.add_argument("-o", "--output", default="report.html")
    p.add_argument("--open", action="store_true", help="open the report in your browser")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("export", help="export all transactions to a CSV file")
    p.add_argument("file")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("recategorize", help="apply your latest rules to saved transactions")
    p.set_defaults(func=cmd_recategorize)

    p = sub.add_parser("set-category", help="set one transaction's category by hand")
    p.add_argument("id", help="the id (or the start of it) shown by 'list'")
    p.add_argument("category")
    p.set_defaults(func=cmd_set_category)

    p = sub.add_parser("rules", help="view or add your own categorization rules")
    rules_sub = p.add_subparsers(dest="rules_command", required=True, metavar="action")
    rules_sub.add_parser("list", help="show your rules")
    add = rules_sub.add_parser("add", help="add a keyword to a category")
    add.add_argument("category")
    add.add_argument("keyword", help="text to look for in descriptions (start with re: for a regular expression)")
    p.set_defaults(func=cmd_rules)

    p = sub.add_parser("reset", help="delete all imported transactions")
    p.add_argument("--yes", action="store_true", help="confirm the deletion")
    p.set_defaults(func=cmd_reset)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (UserError, ParseError, RulesError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
