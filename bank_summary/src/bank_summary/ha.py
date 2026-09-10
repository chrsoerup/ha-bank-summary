"""Publish sensor states to Home Assistant via the Supervisor REST API.

States are set with `homeassistant_api: true` + `SUPERVISOR_TOKEN` (see `config.py`), not via
MQTT or a custom integration — the add-on has no other way to reach Core. API-set states do not
survive an HA Core restart, so the caller re-publishes on every sync *and* on add-on start.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from .report import aggregate_month, prev_month
from .store import Store


@dataclass
class HAState:
    entity_id: str
    state: str
    attributes: dict[str, Any] = field(default_factory=dict)


def _monetary_attrs(currency: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    attrs = {"device_class": "monetary", "unit_of_measurement": currency, "state_class": "total"}
    if extra:
        attrs.update(extra)
    return attrs


def build_states(
    store: Store,
    currency: str = "DKK",
    consent_expiring_soon_days: int = 14,
    now: datetime | None = None,
    balances: dict[str, str] | None = None,
    account_uid: str | None = None,
) -> list[HAState]:
    now = now or datetime.now(UTC)
    today = now.date()
    current = aggregate_month(store, today.year, today.month, account_uid=account_uid)
    prev_year, prev_month_num = prev_month(today.year, today.month)
    previous = aggregate_month(store, prev_year, prev_month_num, account_uid=account_uid)

    top_category = max(
        current.category_totals, key=lambda c: abs(current.category_totals[c]), default="—"
    )
    category_map = {c: str(a) for c, a in current.category_totals.items()}
    prev_category_map = {c: str(a) for c, a in previous.category_totals.items()}

    last_sync = store.last_sync()
    session = store.latest_session()
    consent_expires = session["valid_until"] if session else None

    consent_expiring = False
    if consent_expires:
        try:
            expires_at = datetime.fromisoformat(consent_expires)
            consent_expiring = expires_at - now <= timedelta(days=consent_expiring_soon_days)
        except ValueError:
            consent_expiring = False

    states = [
        HAState(
            "sensor.bank_income_this_month",
            f"{current.income:.2f}",
            _monetary_attrs(currency),
        ),
        HAState(
            "sensor.bank_expenditure_this_month",
            f"{current.expenditure:.2f}",
            _monetary_attrs(currency),
        ),
        HAState(
            "sensor.bank_net_this_month",
            f"{current.net:.2f}",
            _monetary_attrs(currency),
        ),
        HAState(
            "sensor.bank_categories_this_month",
            top_category,
            {"categories": category_map, "categories_previous_month": prev_category_map},
        ),
        HAState(
            "sensor.bank_last_sync",
            last_sync["finished_at"] if last_sync and last_sync["finished_at"] else "unknown",
            {"device_class": "timestamp"},
        ),
        HAState(
            "sensor.bank_consent_expires",
            consent_expires or "unknown",
            {"device_class": "timestamp"},
        ),
        HAState(
            "binary_sensor.bank_consent_expiring",
            "on" if consent_expiring else "off",
            {},
        ),
        HAState(
            "sensor.bank_uncategorised_count",
            str(store.uncategorised_count(account_uid=account_uid)),
            {},
        ),
    ]

    if balances:
        for account in store.list_accounts(account_uid):
            balance = balances.get(account["uid"])
            if balance is None:
                continue
            slug = account["uid"].lower().replace("-", "_")
            states.append(
                HAState(
                    f"sensor.bank_balance_{slug}",
                    balance,
                    _monetary_attrs(account["currency"] or currency, {"account": account["name"]}),
                )
            )

    return states


def publish_states(states: list[HAState], ha_api_base: str, supervisor_token: str) -> int:
    """POSTs each state to the Supervisor proxy. Returns the number that failed to publish —
    one bad entity shouldn't stop the rest from updating."""
    failures = 0
    headers = {"Authorization": f"Bearer {supervisor_token}"}
    with httpx.Client(base_url=ha_api_base, headers=headers, timeout=10.0) as http:
        for s in states:
            try:
                resp = http.post(
                    f"/states/{s.entity_id}",
                    json={"state": s.state, "attributes": s.attributes},
                )
                resp.raise_for_status()
            except httpx.HTTPError:
                failures += 1
    return failures
