"""Creates the FAKE sample statements in this folder. Nothing here is real bank data.

Run it again to regenerate:  python sample/generate_sample.py
"""

import csv
import datetime as dt
import random
from decimal import Decimal
from pathlib import Path

random.seed(2026)  # same output every time
OUT = Path(__file__).parent
START, END = dt.date(2026, 5, 1), dt.date(2026, 10, 31)

rows: list[tuple[dt.date, str, Decimal]] = []  # (date, description, amount) negative = money out


def add(day: dt.date, description: str, amount: float | Decimal) -> None:
    if START <= day <= END:
        rows.append((day, description, Decimal(str(amount)).quantize(Decimal("0.01"))))


def monthly(day_of_month: int, description: str, amount) -> None:
    for month in range(5, 11):
        add(dt.date(2026, month, day_of_month), description, amount() if callable(amount) else amount)


# Pay every second Friday
day = dt.date(2026, 5, 1)
while day <= END:
    add(day, "PAYROLL DEPOSIT ACME CORP", 1850.00)
    day += dt.timedelta(days=14)

# Fixed monthly bills and subscriptions
monthly(1, "PRE-AUTHORIZED DEBIT RENT - OAK APARTMENTS", -1150.00)
monthly(1, "GOODLIFE CLUBS 0456 WINNIPEG MB", -49.99)
monthly(5, "NETFLIX.COM 866-579-7172 ON", -16.49)
monthly(12, "SPOTIFY P1A2B3 STOCKHOLM", -11.99)
monthly(14, "APPLE.COM/BILL 866-712-7753 ON", -3.99)
monthly(20, "TELUS MOBILITY PAD", -75.00)
monthly(22, "MANITOBA HYDRO PAD", lambda: -random.uniform(55, 120))
monthly(28, "VISA PAYMENT - THANK YOU", -400.00)
add(dt.date(2026, 6, 9), "E-TRANSFER RECEIVED FRIEND", 60.00)
monthly(30, "INTEREST PAID", lambda: random.uniform(0.2, 0.6))

# Everyday spending on random days
def spread(per_month: int, descriptions: list[str], low: float, high: float) -> None:
    for month in range(5, 11):
        for _ in range(per_month):
            add(dt.date(2026, month, random.randint(1, 28)), random.choice(descriptions), -random.uniform(low, high))


spread(8, ["SUPERSTORE #1234 WINNIPEG MB", "SAFEWAY #4410 WINNIPEG MB", "COSTCO WHOLESALE W123", "SAVE-ON-FOODS 6677"], 28, 140)
spread(10, ["TIM HORTONS #0721 WINNIPEG MB", "STARBUCKS COFFEE 12044", "MCDONALD'S #8821"], 3.5, 11)
spread(3, ["DOORDASH*PIZZA PLACE", "SKIPTHEDISHES ORDER 55120", "THE FORKS GRILL WINNIPEG MB"], 22, 55)
spread(3, ["PETRO-CANADA 10234 WINNIPEG", "SHELL C14455 WINNIPEG"], 38, 68)
spread(4, ["DOLLARAMA #811", "AMAZON.CA*MK12AB3", "CANADIAN TIRE #321"], 6, 48)
spread(1, ["CINEPLEX ODEON POLO PARK", "STEAM PURCHASE 425-952-2985"], 12, 32)
spread(1, ["SHOPPERS DRUG MART #1122", "LONDON DRUGS #77"], 9, 46)
for month in (5, 7, 9, 10):  # a local shop the default rules don't know
    add(dt.date(2026, month, random.randint(2, 27)), "CORNER MARKET 55 ELLICE AVE", -random.uniform(8, 30))

# Two big one-off purchases (the tool should flag these as unusual)
add(dt.date(2026, 8, 14), "BEST BUY #123 WINNIPEG MB", -1299.99)
add(dt.date(2026, 9, 3), "AIR CANADA 0142379", -486.50)

rows.sort(key=lambda r: (r[0], r[1]))

# File 1: ISO dates, separate Debit and Credit columns
with open(OUT / "statement_debit_credit.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["Date", "Description", "Debit", "Credit"])
    for d, desc, amt in rows:
        w.writerow([d.isoformat(), desc, f"{-amt:.2f}" if amt < 0 else "", f"{amt:.2f}" if amt > 0 else ""])

# File 2: a different style: lines above the header, US dates, one signed Amount column
subset = [r for r in rows if r[0].month == 10][:18]
with open(OUT / "statement_signed_amounts.csv", "w", newline="", encoding="utf-8") as f:
    f.write("Account: Chequing XXXX\nStatement period: October 2026\n\n")
    w = csv.writer(f)
    w.writerow(["Posted Date", "Details", "Amount"])
    for i, (d, desc, amt) in enumerate(subset):
        text = f"({-amt:,.2f})" if (amt < 0 and i % 3 == 0) else f"{amt:.2f}"  # mix of styles
        w.writerow([d.strftime("%m/%d/%Y"), desc, text if amt > 0 or i % 3 == 0 else f"-${-amt:,.2f}"])

print(f"{len(rows)} rows written")
