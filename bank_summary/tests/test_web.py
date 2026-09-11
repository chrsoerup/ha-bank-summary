from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import respx
from fastapi.testclient import TestClient

from bank_summary import web
from bank_summary.config import Settings
from bank_summary.store import Store

BASE_URL = "https://api.enablebanking.com"


class _FakeScheduler:
    def shutdown(self, wait: bool = False) -> None:
        pass


def _client(tmp_path: Path, **overrides: Any) -> TestClient:
    settings = Settings(_env_file=None, data_dir=tmp_path, **overrides)
    web.app.state.settings = settings
    web.app.state.scheduler = _FakeScheduler()
    return TestClient(web.app)


def test_health(tmp_path: Path) -> None:
    resp = _client(tmp_path).get("/health")
    assert resp.status_code == 200
    assert resp.text == "ok"


def test_index_shows_status_before_anything_is_linked(tmp_path: Path) -> None:
    resp = _client(tmp_path).get("/")
    assert resp.status_code == 200
    assert "never" in resp.text
    assert "not linked yet" in resp.text


def test_index_flags_missing_private_key(tmp_path: Path) -> None:
    resp = _client(tmp_path, private_key_path=tmp_path / "missing.pem").get("/")
    assert resp.status_code == 200
    assert "Missing" in resp.text
    assert 'action="private-key"' in resp.text


def test_save_private_key_writes_file_and_redirects(
    tmp_path: Path, rsa_private_key_path: Path
) -> None:
    target = tmp_path / "keys" / "key.pem"
    client = _client(tmp_path, private_key_path=target)

    resp = client.post(
        "/private-key",
        data={"pem": rsa_private_key_path.read_text()},
        follow_redirects=False,
    )

    assert resp.status_code == 303
    assert resp.headers["location"] == "."
    assert target.read_text().strip() == rsa_private_key_path.read_text().strip()
    assert target.stat().st_mode & 0o777 == 0o600
    assert "Installed at" in client.get("/").text


def test_save_private_key_rejects_garbage(tmp_path: Path) -> None:
    target = tmp_path / "key.pem"
    client = _client(tmp_path, private_key_path=target)

    resp = client.post("/private-key", data={"pem": "not a key"})

    assert resp.status_code == 400
    assert not target.exists()


def test_save_private_key_requires_path_option(tmp_path: Path) -> None:
    resp = _client(tmp_path).post("/private-key", data={"pem": "x"})
    assert resp.status_code == 400


def test_report_path_traversal_is_blocked(tmp_path: Path) -> None:
    secret = tmp_path.parent / "secret.md"
    secret.write_text("top secret")

    resp = _client(tmp_path).get("/reports/..%2F..%2Fsecret.md")
    assert resp.status_code == 404


def test_report_rejects_non_markdown(tmp_path: Path) -> None:
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    (reports_dir / "notes.txt").write_text("hi")

    resp = _client(tmp_path).get("/reports/notes.txt")
    assert resp.status_code == 404


def test_report_serves_existing_markdown(tmp_path: Path) -> None:
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    (reports_dir / "2026-05.md").write_text("# Bank summary")

    resp = _client(tmp_path).get("/reports/2026-05.md")
    assert resp.status_code == 200
    assert "# Bank summary" in resp.text


def test_callback_requires_configured_credentials(tmp_path: Path) -> None:
    resp = _client(tmp_path).post("/callback", json={"code": "auth-code"})
    assert resp.status_code == 500


@respx.mock
def test_callback_links_accounts(tmp_path: Path, rsa_private_key_path: Path) -> None:
    respx.post(f"{BASE_URL}/sessions").mock(
        return_value=httpx.Response(
            200,
            json={
                "session_id": "sess-1",
                "status": "active",
                "valid_until": "2026-08-01T00:00:00+00:00",
                "accounts": [
                    {"uid": "acc-1", "iban": "DK123", "name": "Checking", "currency": "DKK"}
                ],
            },
        )
    )
    client = _client(
        tmp_path,
        application_id="app-1",
        private_key_path=rsa_private_key_path,
        base_url=BASE_URL,
    )

    resp = client.post("/callback", json={"code": "auth-code", "state": "s"})

    assert resp.status_code == 200
    assert resp.json() == {"linked_accounts": 1, "session_id": "sess-1"}

    store = Store(Settings(_env_file=None, data_dir=tmp_path).resolved_db_path())
    assert len(store.list_accounts()) == 1


@respx.mock
def test_callback_rejects_bad_code(tmp_path: Path, rsa_private_key_path: Path) -> None:
    respx.post(f"{BASE_URL}/sessions").mock(return_value=httpx.Response(400, json={}))
    client = _client(
        tmp_path,
        application_id="app-1",
        private_key_path=rsa_private_key_path,
        base_url=BASE_URL,
    )

    resp = client.post("/callback", json={"code": "bad-code"})

    assert resp.status_code == 400


@respx.mock
def test_connect_surfaces_enable_banking_validation_error(
    tmp_path: Path, rsa_private_key_path: Path
) -> None:
    respx.post(f"{BASE_URL}/auth").mock(
        return_value=httpx.Response(422, json={"message": "redirect_url not whitelisted"})
    )
    client = _client(
        tmp_path,
        application_id="app-1",
        private_key_path=rsa_private_key_path,
        aspsp_name="Some Bank",
        redirect_url="https://ha/webhook",
        base_url=BASE_URL,
    )

    resp = client.get("/connect")

    assert resp.status_code == 400
    assert "redirect_url not whitelisted" in resp.text


def test_connect_reports_missing_private_key_file(tmp_path: Path) -> None:
    client = _client(
        tmp_path,
        application_id="app-1",
        private_key_path=tmp_path / "missing.pem",
        aspsp_name="Some Bank",
        redirect_url="https://ha/webhook",
    )

    resp = client.get("/connect")

    assert resp.status_code == 400
    assert "Private key file not found" in resp.text


def test_index_warns_when_account_uid_matches_no_linked_account(tmp_path: Path) -> None:
    from bank_summary.enablebanking.models import AccountRef

    store = Store(Settings(_env_file=None, data_dir=tmp_path).resolved_db_path())
    store.upsert_account(AccountRef(uid="acc-1", iban="DK123", name="Checking"), "B")
    store.close()

    resp = _client(tmp_path, account_uid="acc-2").get("/")

    assert "acc-1" in resp.text
    assert "matches none of the linked accounts" in resp.text
    assert "← synced" not in _client(tmp_path, account_uid="acc-2").get("/").text
    assert "← synced" in _client(tmp_path, account_uid="acc-1").get("/").text
