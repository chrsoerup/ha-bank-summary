from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import httpx
import respx

from bank_summary.config import Settings
from bank_summary.enablebanking.models import AccountRef
from bank_summary.scheduler import run_daily_job
from bank_summary.store import Store

BASE_URL = "https://api.enablebanking.com"


def _settings(tmp_path: Path, **overrides: Any) -> Settings:
    rules_path = tmp_path / "rules.yaml"
    rules_path.write_text("[]")
    return Settings(_env_file=None, data_dir=tmp_path, rules_path=rules_path, **overrides)


def test_skips_when_no_session_linked(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    Store(settings.resolved_db_path()).close()  # creates the schema; no session yet

    run_daily_job(settings)  # must not raise, and must not attempt any HTTP calls

    store = Store(settings.resolved_db_path())
    assert store.last_sync() is None


@respx.mock
def test_syncs_and_writes_report_when_linked(tmp_path: Path, rsa_private_key_path: Path) -> None:
    respx.get(f"{BASE_URL}/accounts/acc-1/transactions").mock(
        return_value=httpx.Response(200, json={"transactions": []})
    )
    respx.get(f"{BASE_URL}/accounts/acc-1/balances").mock(
        return_value=httpx.Response(
            200,
            json={
                "balances": [
                    {
                        "balance_amount": {"amount": "500.00", "currency": "DKK"},
                        "balance_type": "interimAvailable",
                    }
                ]
            },
        )
    )

    settings = _settings(
        tmp_path, application_id="app-1", private_key_path=rsa_private_key_path, base_url=BASE_URL
    )
    store = Store(settings.resolved_db_path())
    store.upsert_session("sess-1", "Some Bank", "2026-08-01T00:00:00+00:00", "active")
    store.upsert_account(
        AccountRef(uid="acc-1", iban="DK1", name="Checking", currency="DKK"), "Some Bank"
    )
    store.close()

    run_daily_job(settings)

    store = Store(settings.resolved_db_path())
    assert store.last_sync() is not None
    assert store.last_sync()["error"] is None

    today = date.today()
    report_path = settings.resolved_reports_dir() / f"{today.year:04d}-{today.month:02d}.md"
    assert report_path.exists()


@respx.mock
def test_errors_are_contained_and_logged_in_sync_log(
    tmp_path: Path, rsa_private_key_path: Path
) -> None:
    respx.get(f"{BASE_URL}/accounts/acc-1/transactions").mock(
        return_value=httpx.Response(500, json={"error": "boom"})
    )

    settings = _settings(
        tmp_path, application_id="app-1", private_key_path=rsa_private_key_path, base_url=BASE_URL
    )
    store = Store(settings.resolved_db_path())
    store.upsert_session("sess-1", "Some Bank", "2026-08-01T00:00:00+00:00", "active")
    store.upsert_account(
        AccountRef(uid="acc-1", iban="DK1", name="Checking", currency="DKK"), "Some Bank"
    )
    store.close()

    run_daily_job(settings)  # must not raise despite the upstream 500

    store = Store(settings.resolved_db_path())
    last = store.last_sync()
    assert last is not None
    assert last["error"] is not None
