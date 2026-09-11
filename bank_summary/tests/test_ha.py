from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import httpx
import respx

from bank_summary.enablebanking.models import AccountRef, Amount, Party, Transaction
from bank_summary.ha import build_states, update_account_uid_option
from bank_summary.store import Store


def _txn(booking_date: str, amount: str, indicator: str, entry_reference: str) -> Transaction:
    return Transaction(
        entry_reference=entry_reference,
        booking_date=booking_date,
        value_date=booking_date,
        transaction_amount=Amount(amount=amount, currency="DKK"),
        credit_debit_indicator=indicator,
        status="BOOK",
        debtor=Party(name="Employer") if indicator == "CRDT" else Party(name="Christian"),
        creditor=Party(name="Christian") if indicator == "CRDT" else Party(name="Shop"),
    )


def test_states_reflect_current_month_totals(tmp_path: Path) -> None:
    store = Store(tmp_path / "bank.db")
    store.upsert_transaction("acc-1", _txn("2026-05-01", "1000.00", "CRDT", "e1"))
    store.upsert_transaction("acc-1", _txn("2026-05-02", "-300.00", "DBIT", "e2"))

    states = build_states(store, now=datetime(2026, 5, 15, tzinfo=UTC))
    by_id = {s.entity_id: s for s in states}

    assert by_id["sensor.bank_income_this_month"].state == "1000.00"
    assert by_id["sensor.bank_expenditure_this_month"].state == "300.00"
    assert by_id["sensor.bank_net_this_month"].state == "700.00"
    assert by_id["sensor.bank_uncategorised_count"].state == "2"


def test_consent_expiring_flips_within_window(tmp_path: Path) -> None:
    store = Store(tmp_path / "bank.db")
    store.upsert_session("s1", "Some Bank", "2026-05-20T00:00:00+00:00", "active")

    soon = build_states(store, consent_expiring_soon_days=14, now=datetime(2026, 5, 10, tzinfo=UTC))
    far = build_states(store, consent_expiring_soon_days=14, now=datetime(2026, 4, 1, tzinfo=UTC))

    assert {s.entity_id: s.state for s in soon}["binary_sensor.bank_consent_expiring"] == "on"
    assert {s.entity_id: s.state for s in far}["binary_sensor.bank_consent_expiring"] == "off"


def test_no_session_reports_unknown_expiry(tmp_path: Path) -> None:
    store = Store(tmp_path / "bank.db")

    states = build_states(store)

    by_id = {s.entity_id: s for s in states}
    assert by_id["sensor.bank_consent_expires"].state == "unknown"
    assert by_id["binary_sensor.bank_consent_expiring"].state == "off"


def test_balances_only_published_when_supplied(tmp_path: Path) -> None:
    store = Store(tmp_path / "bank.db")
    store.upsert_account(
        AccountRef(uid="acc-1", iban="DK123", name="Checking", currency="DKK"), "Some Bank"
    )

    without_balances = build_states(store)
    assert not any(s.entity_id.startswith("sensor.bank_balance_") for s in without_balances)

    with_balances = build_states(store, balances={"acc-1": "1234.56"})
    balance_states = [s for s in with_balances if s.entity_id.startswith("sensor.bank_balance_")]
    assert len(balance_states) == 1
    assert balance_states[0].entity_id == "sensor.bank_balance_acc_1"
    assert balance_states[0].state == "1234.56"


@respx.mock
def test_update_account_uid_option_posts_to_supervisor() -> None:
    route = respx.post("http://supervisor/addons/self/options").mock(
        return_value=httpx.Response(200, json={"result": "ok"})
    )

    update_account_uid_option("token-1", "acc-2")

    import json

    assert route.called
    request = route.calls.last.request
    assert request.headers["Authorization"] == "Bearer token-1"
    assert json.loads(request.content) == {"options": {"account_uid": "acc-2"}}
