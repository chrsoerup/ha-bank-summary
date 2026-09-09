"""Rule-based transaction categorisation.

Rules are evaluated top-to-bottom; the first match wins. Re-categorisation runs entirely over
already-stored fields (see `store.py`'s `raw_json`), so editing `rules.yaml` never re-hits the
bank.
"""

from __future__ import annotations

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


def load_rules(path: Path) -> list[Rule]:
    raw = yaml.safe_load(Path(path).read_text()) or []
    return [Rule(category=item["category"], match=item.get("match", {})) for item in raw]


def categorize(txn: CategorizableTransaction, rules: list[Rule]) -> tuple[str, str]:
    """Returns (category, source). source is "rule" on a match, "none" otherwise."""
    for rule in rules:
        if rule.matches(txn):
            return rule.category, "rule"
    return UNCATEGORISED, "none"


def suggest_rule_stub(txn: CategorizableTransaction) -> str:
    """A copy-pasteable rules.yaml stub for an uncategorised transaction."""
    name = txn.counterparty_name or txn.remittance_text or "???"
    escaped = re.escape(name)
    return f'- category: TODO\n  match: {{counterparty_regex: "(?i){escaped}"}}'


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
