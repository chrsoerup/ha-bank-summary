from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from bank_summary.enablebanking.models import Amount, Party, Transaction
from bank_summary.report import aggregate_month, render_month
from bank_summary.store import Store


def _txn(booking_date: str, amount: str, indicator: str, entry_reference: str) -> Transaction:
    return Transaction(
        entry_reference=entry_reference,
        booking_date=booking_date,
        value_date=booking_date,
        transaction_amount=Amount(amount=amount, currency="DKK"),
        credit_debit_indicator=indicator,
        status="BOOK",
        debtor=Party(name="Christian") if indicator == "DBIT" else Party(name="Employer"),
        creditor=Party(name="Shop") if indicator == "DBIT" else Party(name="Christian"),
    )


def _store(tmp_path: Path) -> Store:
    return Store(tmp_path / "bank.db")


def test_month_boundary_first_of_month_included(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.upsert_transaction("acc-1", _txn("2026-02-01", "10.00", "DBIT", "e1"))
    store.upsert_transaction("acc-1", _txn("2026-01-31", "20.00", "DBIT", "e2"))

    february = aggregate_month(store, 2026, 2)
    january = aggregate_month(store, 2026, 1)

    assert len(february.rows) == 1
    assert february.rows[0]["booking_date"] == "2026-02-01"
    assert len(january.rows) == 1
    assert january.rows[0]["booking_date"] == "2026-01-31"


def test_month_with_zero_income_has_no_zero_division(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.upsert_transaction("acc-1", _txn("2026-03-05", "-50.00", "DBIT", "e1"))
    store.upsert_transaction("acc-1", _txn("2026-03-06", "-25.00", "DBIT", "e2"))

    agg = aggregate_month(store, 2026, 3)
    assert agg.income == Decimal("0")
    assert agg.expenditure == Decimal("75.00")
    assert agg.net == Decimal("-75.00")

    # must not raise despite zero income / no positive categories
    report = render_month(store, 2026, 3)
    assert "Bank summary — 2026-03" in report


def test_empty_month_renders_without_error(tmp_path: Path) -> None:
    store = _store(tmp_path)
    report = render_month(store, 2026, 6)
    assert "Bank summary — 2026-06" in report
    assert "None — nice." in report


def test_uncategorised_rule_stub_is_valid_yaml(tmp_path: Path) -> None:
    import yaml

    store = _store(tmp_path)
    store.upsert_transaction("acc-1", _txn("2026-07-10", "-30.00", "DBIT", "e1"))

    report = render_month(store, 2026, 7)
    fence_start = report.index("```yaml")
    fence_end = report.index("```", fence_start + len("```yaml"))
    stub_lines = report[fence_start + len("```yaml") : fence_end].strip("\n").splitlines()
    stub_yaml = "\n".join(line[2:] for line in stub_lines)  # strip the fence's own 2-space indent

    parsed = yaml.safe_load(stub_yaml)
    assert parsed == [{"category": "TODO", "match": {"counterparty_regex": "(?i)Shop"}}]
