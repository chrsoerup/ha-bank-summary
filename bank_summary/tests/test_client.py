from __future__ import annotations

import json
from pathlib import Path

import httpx
import respx

from bank_summary.enablebanking.client import EnableBankingClient

BASE_URL = "https://api.enablebanking.com"


def _txn(entry_reference: str, amount: str = "-42.00") -> dict:
    return {
        "entry_reference": entry_reference,
        "booking_date": "2026-01-05",
        "value_date": "2026-01-05",
        "transaction_amount": {"amount": amount, "currency": "DKK"},
        "credit_debit_indicator": "DBIT",
        "status": "BOOK",
        "debtor": {"name": "Christian"},
        "creditor": {"name": "Netto"},
    }


@respx.mock
def test_iter_transactions_follows_continuation_key(rsa_private_key_path: Path) -> None:
    route = respx.get(f"{BASE_URL}/accounts/acc-1/transactions")
    route.side_effect = [
        httpx.Response(
            200,
            json={"transactions": [_txn("t1"), _txn("t2")], "continuation_key": "page-2"},
        ),
        httpx.Response(200, json={"transactions": [_txn("t3")], "continuation_key": None}),
    ]

    client = EnableBankingClient(
        application_id="app-123", private_key_path=str(rsa_private_key_path), base_url=BASE_URL
    )
    transactions = list(client.iter_transactions("acc-1"))

    assert [t.entry_reference for t in transactions] == ["t1", "t2", "t3"]
    assert route.call_count == 2
    # second call must carry the continuation_key from the first page's response
    second_request = route.calls[1].request
    assert "continuation_key=page-2" in str(second_request.url)


@respx.mock
def test_iter_transactions_stops_without_continuation_key(rsa_private_key_path: Path) -> None:
    route = respx.get(f"{BASE_URL}/accounts/acc-1/transactions").mock(
        return_value=httpx.Response(200, json={"transactions": [_txn("t1")]})
    )

    client = EnableBankingClient(
        application_id="app-123", private_key_path=str(rsa_private_key_path), base_url=BASE_URL
    )
    transactions = list(client.iter_transactions("acc-1"))

    assert len(transactions) == 1
    assert route.call_count == 1


@respx.mock
def test_requests_carry_bearer_jwt(rsa_private_key_path: Path) -> None:
    route = respx.get(f"{BASE_URL}/aspsps").mock(
        return_value=httpx.Response(200, json={"aspsps": []})
    )

    client = EnableBankingClient(
        application_id="app-123", private_key_path=str(rsa_private_key_path), base_url=BASE_URL
    )
    client.list_aspsps("DK")

    auth_header = route.calls[0].request.headers["authorization"]
    assert auth_header.startswith("Bearer ")


@respx.mock
def test_start_authorization_always_sends_valid_until(rsa_private_key_path: Path) -> None:
    # /auth returns 422 when access.valid_until is absent, so the client must default it.
    route = respx.post(f"{BASE_URL}/auth").mock(
        return_value=httpx.Response(200, json={"url": "https://bank/login"})
    )
    client = EnableBankingClient(
        application_id="app-123", private_key_path=str(rsa_private_key_path), base_url=BASE_URL
    )

    client.start_authorization("Some Bank", "DK", "https://ha/webhook")

    body = json.loads(route.calls[0].request.content)
    assert body["access"]["valid_until"]
    assert body["aspsp"] == {"name": "Some Bank", "country": "DK"}
