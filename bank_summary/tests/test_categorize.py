from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from bank_summary.categorize import (
    UNCATEGORISED,
    CategorizableTransaction,
    categorize,
    load_rules,
)

RULES_YAML = """
- category: Groceries
  match: { counterparty_regex: "(?i)netto|rema 1000" }
- category: Salary
  match: { credit_debit_indicator: CRDT, counterparty_regex: "(?i)laerdal" }
- category: BigTransfer
  match: { amount_min: 5000 }
"""


def _txn(**overrides: object) -> CategorizableTransaction:
    base = dict(
        counterparty_name="Netto Amager",
        remittance_text=None,
        bank_transaction_code=None,
        merchant_category_code=None,
        credit_debit_indicator="DBIT",
        amount=Decimal("-100.00"),
    )
    base.update(overrides)
    return CategorizableTransaction(**base)  # type: ignore[arg-type]


def _write_rules(tmp_path: Path) -> Path:
    path = tmp_path / "rules.yaml"
    path.write_text(RULES_YAML)
    return path


def test_first_matching_rule_wins(tmp_path: Path) -> None:
    rules = load_rules(_write_rules(tmp_path))
    category, source = categorize(_txn(), rules)
    assert category == "Groceries"
    assert source == "rule"


def test_credit_debit_indicator_and_regex_combine(tmp_path: Path) -> None:
    rules = load_rules(_write_rules(tmp_path))
    txn = _txn(
        counterparty_name="Laerdal Medical",
        credit_debit_indicator="CRDT",
        amount=Decimal("30000.00"),
    )
    category, _ = categorize(txn, rules)
    assert category == "Salary"


def test_credit_debit_mismatch_falls_through_to_next_rule(tmp_path: Path) -> None:
    rules = load_rules(_write_rules(tmp_path))
    # Same counterparty as the Salary rule, but wrong direction (DBIT) — doesn't match Salary,
    # falls through to the amount-only rule.
    txn = _txn(
        counterparty_name="Laerdal Medical",
        credit_debit_indicator="DBIT",
        amount=Decimal("-6000.00"),
    )
    category, _ = categorize(txn, rules)
    assert category == "BigTransfer"


def test_no_match_is_uncategorised(tmp_path: Path) -> None:
    rules = load_rules(_write_rules(tmp_path))
    txn = _txn(counterparty_name="Some Random Shop", amount=Decimal("-10.00"))
    category, source = categorize(txn, rules)
    assert category == UNCATEGORISED
    assert source == "none"
