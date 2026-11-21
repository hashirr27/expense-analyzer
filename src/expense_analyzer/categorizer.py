"""Decides which category a transaction belongs to, using keyword rules.

Rules are a JSON file that maps a category to a list of keywords:
    {"Groceries": ["superstore", "costco"], "Dining": ["tim hortons"]}
A keyword matches when it appears in the description (ignoring upper/lower case).
A keyword starting with "re:" is a regular expression instead.

When several rules match, the winner is picked like this:
  1. your own rules beat the built-in default rules,
  2. then the longest keyword wins ("uber eats" beats "uber").
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from .models import INCOME, UNCATEGORIZED

DEFAULT_RULES_PATH = Path(__file__).with_name("default_rules.json")

Rules = dict[str, list[str]]


class RulesError(ValueError):
    """A rules file is not in the expected shape."""


def validate_rules(data: object, source: str = "rules") -> Rules:
    """Check that `data` looks like {"Category": ["keyword", ...]} and return it cleaned up."""
    if not isinstance(data, dict):
        raise RulesError(f"{source}: expected a JSON object like {{\"Groceries\": [\"costco\"]}}")
    cleaned: Rules = {}
    for category, keywords in data.items():
        if not isinstance(category, str) or not category.strip():
            raise RulesError(f"{source}: category names must be non-empty text")
        if not isinstance(keywords, list) or not all(isinstance(k, str) and k.strip() for k in keywords):
            raise RulesError(f"{source}: the keywords for {category!r} must be a list of non-empty text")
        cleaned[category.strip()] = [k.strip() for k in keywords]
    return cleaned


def load_rules(path: str | Path) -> Rules:
    """Load a rules file. A missing file means "no rules"; a broken file is an error."""
    p = Path(path)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RulesError(f"{p} is not valid JSON: {exc}") from None
    return validate_rules(data, str(p))


def save_rules(path: str | Path, rules: Rules) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(rules, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def add_rule(rules: Rules, category: str, keyword: str) -> bool:
    """Add a keyword to a category. Returns False if it was already there."""
    category, keyword = category.strip(), keyword.strip()
    if not category or not keyword:
        raise RulesError("category and keyword must not be empty")
    if keyword.startswith("re:"):
        try:
            re.compile(keyword[3:])
        except re.error as exc:
            raise RulesError(f"invalid regular expression: {exc}") from None
    existing = rules.setdefault(category, [])
    if keyword.lower() in (k.lower() for k in existing):
        return False
    existing.append(keyword)
    return True


@dataclass(frozen=True)
class _Rule:
    category: str
    keyword: str
    pattern: re.Pattern[str]
    is_user: bool


def _compile(keyword: str) -> re.Pattern[str]:
    if keyword.startswith("re:"):
        return re.compile(keyword[3:], re.IGNORECASE)
    # The keyword must start at the beginning of a word, so "rent" does not match "current".
    return re.compile(r"(?<![a-z0-9])" + re.escape(keyword.lower()), re.IGNORECASE)


class Categorizer:
    """Turns a description (and amount) into a category name."""

    def __init__(self, user_rules: Rules | None = None, default_rules: Rules | None = None):
        if default_rules is None:
            default_rules = validate_rules(json.loads(DEFAULT_RULES_PATH.read_text(encoding="utf-8")), "default rules")
        self._rules: list[_Rule] = []
        for rules, is_user in ((user_rules or {}, True), (default_rules, False)):
            for category, keywords in rules.items():
                for keyword in keywords:
                    self._rules.append(_Rule(category, keyword, _compile(keyword), is_user))

    def categorize(self, description: str, amount: Decimal) -> str:
        best: _Rule | None = None
        for rule in self._rules:
            if not rule.pattern.search(description):
                continue
            # "Income" keywords only apply to money coming in (a "payroll fee" is not income).
            if rule.category == INCOME and amount <= 0:
                continue
            if best is None or (rule.is_user, len(rule.keyword)) > (best.is_user, len(best.keyword)):
                best = rule
        if best is not None:
            return best.category
        # No rule matched: money coming in is treated as income, money going out is unknown.
        return INCOME if amount > 0 else UNCATEGORIZED


# ---------------------------------------------------------------- merchant names

_NOISE_PREFIXES = re.compile(
    r"^(pos purchase|point of sale|debit card purchase|debit purchase|purchase|visa debit|pre-?authorized debit|"
    r"pre-?authorized payment|recurring payment|online purchase|card purchase|pad|sq \*|tst\*|pp\*)\s*[-:*]*\s*",
    re.IGNORECASE,
)
_STOP_WORDS = {"com", "www", "inc", "ltd", "corp", "co", "llc", "the", "ca", "us"}
# Province codes are only removed from the END of a description ("... WINNIPEG MB"),
# because "ON" can also be part of a name ("Save-On-Foods").
_PROVINCES = {"on", "bc", "ab", "mb", "sk", "qc", "ns", "nb", "nl", "pe", "yt", "nt", "nu"}


def normalize_merchant(description: str) -> str:
    """Boil a messy bank description down to a short merchant name, for grouping.

    'WALMART SUPERCENTER #1234 WINNIPEG MB' -> 'walmart supercenter'
    'NETFLIX.COM 866-579-7172 ON'           -> 'netflix'
    'SAVE-ON-FOODS 6677'                    -> 'saveonfoods'
    A heuristic, not perfect, but good enough to group repeat purchases together.
    """
    s = _NOISE_PREFIXES.sub("", description.strip()).lower()
    s = re.sub(r"(?<=[a-z])-(?=[a-z])", "", s)  # keep hyphenated names whole: save-on-foods -> saveonfoods
    s = re.sub(r"[^a-z&\s0-9']", " ", s)
    tokens = [t for t in s.split() if not any(ch.isdigit() for ch in t) and t not in _STOP_WORDS]
    while len(tokens) > 1 and tokens[-1] in _PROVINCES:
        tokens.pop()
    return " ".join(tokens[:2]) or description.strip().lower()
