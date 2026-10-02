"""Rule-based transaction categorisation.

Rules are evaluated top-to-bottom; the first match wins. The user's `rules.yaml` is evaluated
first, then the built-in Danish rule set shipped with the add-on (`rules.builtin.yaml`), so the
user's rules always take precedence and built-in improvements arrive with add-on updates.

Re-categorisation runs entirely over already-stored fields (see `store.py`'s `raw_json`), so
editing `rules.yaml` never re-hits the bank.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from .store import Store

UNCATEGORISED = "Uncategorised"
BUILTIN_RULES_PATH = Path(__file__).parent / "rules.builtin.yaml"

# Payment-method noise Danish banks prepend to the merchant ("Dankort-nota NETTO 1234 KBH",
# "VISA/DANKORT 12.09 SUMUP *CAFE X"). Stripped repeatedly, so stacked prefixes go too.
_PREFIX = re.compile(
    r"^(dankort-?nota|dk-?nota|visa/dankort|visa\s*dankort|visa-?k[øo]b|kortk[øo]b|visa|"
    r"mastercard|maestro|mc|nota|k[øo]b|mobile\s*pay|mobilepay|betalingsservice|bs|"
    r"apple\s*pay|google\s*pay|paypal|sumup|zettle|izettle|nets|pbs|"
    # Transfer wording, so "Overførsel til Louise" groups by recipient, not as one "OVERFØRSEL".
    r"overf[øo]rsel(\s+(til|fra))?|overf[øo]rt(\s+(til|fra))?|indbetaling(\s+fra)?)\b[\s:.*_/-]*",
    re.IGNORECASE,
)
_NOISE = [
    re.compile(r"\b[x*]{2,}\d+\b", re.IGNORECASE),  # masked card numbers
    re.compile(r"\b\d{1,2}[./-]\d{1,2}([./-]\d{2,4})?\b"),  # dates
    re.compile(r"\b\d{1,2}[:.]\d{2}\b"),  # times
    re.compile(r"\b\d{3,}\b"),  # terminal ids, store numbers, references
    re.compile(r"(?<=\s)\d{1,2}\b"),  # short trailing numbers ("GOLFKLUBBEN 88"), not a leading "7"
    re.compile(r"[*_#]+"),
]


@dataclass
class CategorizableTransaction:
    counterparty_name: str | None
    remittance_text: str | None
    bank_transaction_code: str | None
    merchant_category_code: str | None
    credit_debit_indicator: str
    amount: Decimal

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> CategorizableTransaction:
        return cls(
            counterparty_name=row["counterparty_name"],
            remittance_text=row["remittance_text"],
            bank_transaction_code=row["bank_transaction_code"],
            merchant_category_code=row["merchant_category_code"],
            credit_debit_indicator=row["credit_debit_indicator"],
            amount=Decimal(row["amount"]),
        )


def normalise_merchant(text: str) -> str:
    """The merchant part of a counterparty/remittance text, without payment-method prefixes,
    card masks, dates or reference numbers; uppercased with whitespace collapsed."""
    previous = None
    text = " ".join(text.split())
    while previous != text:  # noise can hide a prefix ("12.09 SUMUP *CAFE X") and vice versa
        previous = text
        text = _PREFIX.sub("", text)
        for pattern in _NOISE:
            text = pattern.sub(" ", text)
        text = " ".join(text.split())
    return text.upper()


def merchant_key(txn: CategorizableTransaction, words: int = 2) -> str:
    """Grouping key for the Categorise page: the first `words` words of the merchant."""
    raw = txn.counterparty_name or txn.remittance_text or ""
    normalised = normalise_merchant(raw)
    return " ".join(normalised.split()[:words]) or raw.strip().upper() or "?"


def text_pattern(text: str) -> str:
    """A case-insensitive, whole-word regex matching `text` with flexible whitespace."""
    words = [re.escape(w) for w in text.split()]
    return r"(?i)(?<!\w)" + r"\s+".join(words) + r"(?!\w)"


@dataclass
class Rule:
    category: str
    match: dict[str, Any]

    def matches(self, txn: CategorizableTransaction) -> bool:
        m = self.match

        if "counterparty_regex" in m:
            if not txn.counterparty_name or not re.search(
                m["counterparty_regex"], txn.counterparty_name
            ):
                return False

        if "remittance_regex" in m:
            if not txn.remittance_text or not re.search(
                m["remittance_regex"], txn.remittance_text
            ):
                return False

        if "text_regex" in m:
            # Either raw field, or the normalised merchant (so "NETTO KBH" also matches
            # "Dankort-nota NETTO 1234 KBH", where a reference number sits between the words).
            candidates = [txn.counterparty_name, txn.remittance_text]
            candidates += [normalise_merchant(c) for c in candidates if c]
            if not any(c and re.search(m["text_regex"], c) for c in candidates):
                return False

        if "mcc" in m and txn.merchant_category_code not in m["mcc"]:
            return False

        if "bank_transaction_code" in m:
            codes = m["bank_transaction_code"]
            codes = codes if isinstance(codes, list) else [codes]
            if txn.bank_transaction_code not in codes:
                return False

        if "credit_debit_indicator" in m and txn.credit_debit_indicator != m[
            "credit_debit_indicator"
        ]:
            return False

        if "amount_min" in m and abs(txn.amount) < Decimal(str(m["amount_min"])):
            return False

        if "amount_max" in m and abs(txn.amount) > Decimal(str(m["amount_max"])):
            return False

        return True


def _read_rules(path: Path) -> list[Rule]:
    raw = yaml.safe_load(Path(path).read_text()) or []
    return [Rule(category=item["category"], match=item.get("match", {})) for item in raw]


def load_rules(path: Path, builtin: bool = True) -> list[Rule]:
    """The user's rules (if the file exists), followed by the built-in rule set."""
    rules = _read_rules(path) if Path(path).is_file() else []
    if builtin:
        rules += _read_rules(BUILTIN_RULES_PATH)
    return rules


def known_categories(rules: list[Rule]) -> list[str]:
    return sorted({r.category for r in rules if r.category != UNCATEGORISED})


def append_rule(path: Path, category: str, match_text: str) -> None:
    """Appends a `text_regex` rule to the user's rules.yaml, keeping its comments intact.

    Appended at the end: earlier, more specific user rules keep winning, and it still runs
    before every built-in rule. Rolled back if the result no longer parses.
    """
    path = Path(path)
    original = path.read_text() if path.is_file() else ""
    current = original
    if not yaml.safe_load(current or "[]"):
        current = ""  # "[]" or empty: a block-style item can't be appended to a flow list
    if current and not current.endswith("\n"):
        current += "\n"
    current += (
        f"\n- category: {json.dumps(category, ensure_ascii=False)}\n"
        f"  match: {{ text_regex: {json.dumps(text_pattern(match_text), ensure_ascii=False)} }}\n"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(current)
    try:
        _read_rules(path)
    except Exception:
        path.write_text(original)
        raise


def categorize(txn: CategorizableTransaction, rules: list[Rule]) -> tuple[str, str]:
    """Returns (category, source). source is "rule" on a match, "none" otherwise."""
    for rule in rules:
        if rule.matches(txn):
            return rule.category, "rule"
    return UNCATEGORISED, "none"


def suggest_rule_stub(txn: CategorizableTransaction) -> str:
    """A copy-pasteable rules.yaml stub for an uncategorised transaction."""
    pattern = json.dumps(text_pattern(merchant_key(txn)), ensure_ascii=False)
    return f"- category: TODO\n  match: {{text_regex: {pattern}}}"


def recategorize_all(store: Store, rules: list[Rule]) -> int:
    """Re-runs categorisation over every stored transaction. Returns the number changed."""
    changed = 0
    for row in store.all_transactions():
        txn = CategorizableTransaction.from_row(row)
        category, source = categorize(txn, rules)
        if row["category"] != category:
            store.set_category(row["dedupe_key"], category, source)
            changed += 1
    return changed
