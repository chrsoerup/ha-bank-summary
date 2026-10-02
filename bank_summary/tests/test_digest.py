from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import respx

from bank_summary.config import Settings
from bank_summary.digest import build_digest
from bank_summary.enablebanking.models import AccountRef, Amount, Party, Transaction
from bank_summary.report import prev_month
from bank_summary.scheduler import run_daily_job
from bank_summary.store import Store

BASE_URL = "https://api.enablebanking.com"
HA_API = "http://supervisor/core/api"


def _txn(booking_date: str, amount: str, entry_reference: str, name: str = "Shop") -> Transaction:
    indicator = "CRDT" if not amount.startswith("-") else "DBIT"
    return Transaction(
        entry_reference=entry_reference,
        booking_date=booking_date,
        value_date=booking_date,
        transaction_amount=Amount(amount=amount, currency="DKK"),
        credit_debit_indicator=indicator,
        status="BOOK",
        debtor=Party(name=name) if indicator == "CRDT" else Party(name="Me"),
        creditor=Party(name="Me") if indicator == "CRDT" else Party(name=name),
    )


def test_digest_totals_and_top_categories(tmp_path: Path) -> None:
    store = Store(tmp_path / "bank.db")
    store.upsert_transaction("acc-1", _txn("2026-08-05", "-200.00", "a1"))
    store.upsert_transaction("acc-1", _txn("2026-09-01", "30000.00", "s1", "Employer"))
    store.upsert_transaction("acc-1", _txn("2026-09-03", "-400.00", "g1", "Netto"))
    store.upsert_transaction("acc-1", _txn("2026-09-04", "-9000.00", "r1", "Landlord"))
    store.set_category("ref:acc-1:g1", "Groceries", "rule")
    store.set_category("ref:acc-1:r1", "Rent", "rule")

    digest = build_digest(store, 2026, 9)

    assert digest is not None
    title, message = digest
    assert title == "Bank summary — 2026-09"
    assert "**Income:** 30,000.00 DKK" in message
    assert "**Expenditure:** 9,400.00 DKK (+4600% vs. prev.)" in message
    assert "**Net:** 20,600.00 DKK" in message
    # Largest spend first; income categories are not "spending".
    assert message.index("- Rent: 9,000.00 DKK") < message.index("- Groceries: 400.00 DKK")
    assert "1 uncategorised transaction(s)" in message


def test_no_digest_for_empty_month(tmp_path: Path) -> None:
    store = Store(tmp_path / "bank.db")
    assert build_digest(store, 2026, 9) is None


def _linked_settings(tmp_path: Path, key_path: Path, **overrides: Any) -> Settings:
    rules_path = tmp_path / "rules.yaml"
    rules_path.write_text("[]")
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path,
        rules_path=rules_path,
        application_id="app-1",
        private_key_path=key_path,
        base_url=BASE_URL,
        SUPERVISOR_TOKEN="tok",
        **overrides,
    )
    store = Store(settings.resolved_db_path())
    store.upsert_session("sess-1", "Some Bank", "2099-01-01T00:00:00+00:00", "active")
    store.upsert_account(
        AccountRef(uid="acc-1", iban="DK1", name="Checking", currency="DKK"), "Some Bank"
    )
    store.close()
    return settings


def _mock_bank(transactions: list[dict[str, Any]]) -> None:
    respx.get(f"{BASE_URL}/accounts/acc-1/transactions").mock(
        return_value=httpx.Response(200, json={"transactions": transactions})
    )
    respx.get(f"{BASE_URL}/accounts/acc-1/balances").mock(
        return_value=httpx.Response(200, json={"balances": []})
    )
    respx.post(url__startswith=f"{HA_API}/states/").mock(return_value=httpx.Response(200))


@respx.mock
def test_daily_job_sends_last_months_digest_once(
    tmp_path: Path, rsa_private_key_path: Path
) -> None:
    year, month = prev_month(date.today().year, date.today().month)
    last_month_day = f"{year:04d}-{month:02d}-15"
    _mock_bank(
        [_txn(last_month_day, "-123.00", "x1").model_dump(mode="json", exclude_none=True)]
    )
    notify = respx.post(f"{HA_API}/services/persistent_notification/create").mock(
        return_value=httpx.Response(200, json=[])
    )
    settings = _linked_settings(tmp_path, rsa_private_key_path)

    run_daily_job(settings)
    run_daily_job(settings)  # second sync the same month must not re-send

    assert notify.call_count == 1
    body = json.loads(notify.calls[0].request.content)
    assert body["notification_id"] == f"bank_summary_digest_{year:04d}_{month:02d}"
    assert "123.00 DKK" in body["message"]
    # The closed month's report is (re)written alongside the current one.
    assert (settings.resolved_reports_dir() / f"{year:04d}-{month:02d}.md").exists()


@respx.mock
def test_failed_notification_is_retried_next_sync(
    tmp_path: Path, rsa_private_key_path: Path
) -> None:
    year, month = prev_month(date.today().year, date.today().month)
    txn = _txn(f"{year:04d}-{month:02d}-15", "-1.00", "x1")
    _mock_bank([txn.model_dump(mode="json", exclude_none=True)])
    notify = respx.post(f"{HA_API}/services/persistent_notification/create").mock(
        side_effect=[httpx.Response(500), httpx.Response(200, json=[])]
    )
    settings = _linked_settings(tmp_path, rsa_private_key_path)

    run_daily_job(settings)
    store = Store(settings.resolved_db_path())
    assert not store.digest_sent(f"{year:04d}-{month:02d}")
    store.close()

    run_daily_job(settings)
    store = Store(settings.resolved_db_path())
    assert store.digest_sent(f"{year:04d}-{month:02d}")
    assert notify.call_count == 2
