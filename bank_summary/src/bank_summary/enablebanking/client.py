from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from types import TracebackType
from typing import Any, cast

import httpx

from .auth import build_jwt, load_private_key
from .models import (
    Aspsp,
    Balance,
    Session,
    StartAuthorizationResponse,
    Transaction,
    TransactionsPage,
)


class EnableBankingClient:
    def __init__(
        self,
        application_id: str,
        private_key_path: str,
        base_url: str = "https://api.enablebanking.com",
        timeout: float = 30.0,
    ) -> None:
        self._application_id = application_id
        self._private_key = load_private_key(private_key_path)
        self._http = httpx.Client(base_url=base_url, timeout=timeout)

    def __enter__(self) -> EnableBankingClient:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    def _headers(self) -> dict[str, str]:
        token = build_jwt(self._application_id, self._private_key)
        return {"Authorization": f"Bearer {token}"}

    def _get(self, path: str, params: dict[str, str] | None = None) -> httpx.Response:
        resp = self._http.get(path, headers=self._headers(), params=params)
        resp.raise_for_status()
        return resp

    def _post(self, path: str, json: dict[str, Any]) -> httpx.Response:
        resp = self._http.post(path, headers=self._headers(), json=json)
        resp.raise_for_status()
        return resp

    def get_application(self) -> dict[str, Any]:
        return cast("dict[str, Any]", self._get("/application").json())

    def list_aspsps(self, country: str) -> list[Aspsp]:
        data = self._get("/aspsps", params={"country": country}).json()
        return [Aspsp.model_validate(item) for item in data["aspsps"]]

    def start_authorization(
        self,
        aspsp_name: str,
        aspsp_country: str,
        redirect_url: str,
        psu_type: str = "personal",
        valid_until: str | None = None,
        state: str | None = None,
    ) -> StartAuthorizationResponse:
        # /auth returns 422 without access.valid_until; default to the 90-day PSD2 maximum.
        if valid_until is None:
            valid_until = (datetime.now(UTC) + timedelta(days=90)).isoformat()
        body = {
            "access": {"valid_until": valid_until},
            "aspsp": {"name": aspsp_name, "country": aspsp_country},
            "state": state or uuid.uuid4().hex,
            "redirect_url": redirect_url,
            "psu_type": psu_type,
        }
        data = self._post("/auth", json=body).json()
        return StartAuthorizationResponse.model_validate(data)

    def create_session(self, code: str) -> Session:
        data = self._post("/sessions", json={"code": code}).json()
        return Session.model_validate(data)

    def get_balances(self, account_uid: str) -> list[Balance]:
        data = self._get(f"/accounts/{account_uid}/balances").json()
        return [Balance.model_validate(item) for item in data["balances"]]

    def get_transactions_page(
        self,
        account_uid: str,
        date_from: date | None = None,
        date_to: date | None = None,
        continuation_key: str | None = None,
    ) -> TransactionsPage:
        params: dict[str, str] = {}
        if continuation_key:
            params["continuation_key"] = continuation_key
        else:
            if date_from:
                params["date_from"] = date_from.isoformat()
            if date_to:
                params["date_to"] = date_to.isoformat()
        data = self._get(f"/accounts/{account_uid}/transactions", params=params).json()
        return TransactionsPage.model_validate(data)

    def iter_transactions(
        self,
        account_uid: str,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> Iterator[Transaction]:
        """Follows `continuation_key` until the ASPSP stops returning one."""
        continuation_key: str | None = None
        while True:
            page = self.get_transactions_page(
                account_uid,
                date_from=date_from,
                date_to=date_to,
                continuation_key=continuation_key,
            )
            yield from page.transactions
            if not page.continuation_key:
                break
            continuation_key = page.continuation_key
