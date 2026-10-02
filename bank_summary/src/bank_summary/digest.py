"""Monthly digest: a persistent notification in HA summarising the month that just closed.

Sent once per month, on the first successful sync after the month ends. Which months have been
sent is tracked in the `digests` table, so a missed run (add-on off on the 1st) catches up on the
next sync instead of being skipped, and restarts never send duplicates.
"""

from __future__ import annotations

from decimal import Decimal

import httpx

from .categorize import UNCATEGORISED
from .report import aggregate_month, prev_month
from .store import Store

TOP_CATEGORIES = 5


def _amount(value: Decimal, currency: str) -> str:
    return f"{value:,.2f} {currency}"


def _change(current: Decimal, previous: Decimal) -> str:
    if previous == 0:
        return ""
    change = current - previous
    pct = (change / abs(previous)) * 100
    sign = "+" if change >= 0 else ""
    return f" ({sign}{pct:.0f}% vs. prev.)"


def build_digest(
    store: Store, year: int, month: int, currency: str = "DKK", account_uid: str | None = None
) -> tuple[str, str] | None:
    """Returns (title, markdown message), or None if the month has no transactions at all —
    e.g. the month before the account was first linked."""
    current = aggregate_month(store, year, month, account_uid=account_uid)
    if not current.rows:
        return None
    prev_year, prev_month_num = prev_month(year, month)
    previous = aggregate_month(store, prev_year, prev_month_num, account_uid=account_uid)

    period = f"{year:04d}-{month:02d}"
    lines = [
        f"**Income:** {_amount(current.income, currency)}"
        f"{_change(current.income, previous.income)}",
        f"**Expenditure:** {_amount(current.expenditure, currency)}"
        f"{_change(current.expenditure, previous.expenditure)}",
        f"**Net:** {_amount(current.net, currency)}{_change(current.net, previous.net)}",
    ]

    spending = sorted(
        ((c, -a) for c, a in current.category_totals.items() if a < 0),
        key=lambda kv: kv[1],
        reverse=True,
    )[:TOP_CATEGORIES]
    if spending:
        lines += ["", "**Top spending categories:**"]
        lines += [f"- {c}: {_amount(a, currency)}" for c, a in spending]

    uncategorised = sum(1 for row in current.rows if row["category"] in (None, UNCATEGORISED))
    if uncategorised:
        lines += ["", f"{uncategorised} uncategorised transaction(s) — see the report."]

    lines += ["", f"Charts and full breakdown: **Bank Summary** in the sidebar → {period}."]
    return f"Bank summary — {period}", "\n".join(lines)


def send_persistent_notification(
    ha_api_base: str, supervisor_token: str, notification_id: str, title: str, message: str
) -> None:
    headers = {"Authorization": f"Bearer {supervisor_token}"}
    with httpx.Client(base_url=ha_api_base, headers=headers, timeout=10.0) as http:
        resp = http.post(
            "/services/persistent_notification/create",
            json={"notification_id": notification_id, "title": title, "message": message},
        )
        resp.raise_for_status()
