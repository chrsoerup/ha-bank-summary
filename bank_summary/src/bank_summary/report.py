"""Monthly aggregation and Markdown report rendering."""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from jinja2 import Template

from .categorize import UNCATEGORISED, CategorizableTransaction, suggest_rule_stub
from .store import Store

TEMPLATE = Template(
    """\
# Bank summary — {{ period }}

## Totals

| | This month | Δ vs. previous month |
|---|---|---|
| Income | {{ "%.2f"|format(income) }} {{ currency }} | {{ delta(income, prev_income) }} |
| Expenditure | {{ "%.2f"|format(expenditure) }} {{ currency }} | {{ delta(expenditure, prev_expenditure) }} |
| Net | {{ "%.2f"|format(net) }} {{ currency }} | {{ delta(net, prev_net) }} |

## Categories

| Category | Amount | Share of spend | Δ vs. prev. month | Δ vs. 3-mo avg |
|---|---|---|---|---|
{% for c in categories -%}
| {{ c.category }} | {{ "%.2f"|format(c.amount) }} {{ currency }} | {{ c.share }} | {{ c.delta_prev }} | {{ c.delta_avg }} |
{% endfor %}

## Top 10 largest expenditures

| Date | Counterparty | Amount |
|---|---|---|
{% for t in top_expenditures -%}
| {{ t.booking_date }} | {{ t.counterparty_name or "—" }} | {{ "%.2f"|format(t.amount) }} {{ currency }} |
{% endfor %}

## Uncategorised ({{ uncategorised|length }})

{% if uncategorised -%}
Add one of these to `rules.yaml` to categorise similar transactions in future:

{% for u in uncategorised -%}
- {{ u.booking_date }} — {{ u.counterparty_name or u.remittance_text or "?" }} — \
{{ "%.2f"|format(u.amount) }} {{ currency }}
  ```yaml
  {{ u.stub }}
  ```
{% endfor %}
{%- else -%}
None — nice.
{%- endif %}

---
Account(s): {{ accounts }}. Period: {{ period }}. Synced: {{ synced_at or "never" }}. \
Consent expires: {{ consent_expires or "unknown" }}.
"""
)


@dataclass
class CategoryLine:
    category: str
    amount: Decimal
    share: str
    delta_prev: str
    delta_avg: str


@dataclass
class MonthlyAggregate:
    year: int
    month: int
    income: Decimal = Decimal(0)
    expenditure: Decimal = Decimal(0)
    category_totals: dict[str, Decimal] = field(default_factory=lambda: defaultdict(Decimal))
    rows: list[sqlite3.Row] = field(default_factory=list)

    @property
    def net(self) -> Decimal:
        return self.income - self.expenditure


def _prev_month(year: int, month: int) -> tuple[int, int]:
    return (year - 1, 12) if month == 1 else (year, month - 1)


def aggregate_month(store: Store, year: int, month: int) -> MonthlyAggregate:
    rows = store.transactions_for_month(year, month)
    agg = MonthlyAggregate(year=year, month=month, rows=rows)
    for row in rows:
        amount = Decimal(row["amount"])
        category = row["category"] or UNCATEGORISED
        agg.category_totals[category] += amount
        if amount > 0:
            agg.income += amount
        else:
            agg.expenditure += -amount
    return agg


def _fmt_delta(current: Decimal, previous: Decimal) -> str:
    if previous == 0:
        return "—"
    change = current - previous
    pct = (change / abs(previous)) * 100
    sign = "+" if change >= 0 else ""
    return f"{sign}{change:.2f} ({sign}{pct:.0f}%)"


def render_month(store: Store, year: int, month: int, currency: str = "DKK") -> str:
    current = aggregate_month(store, year, month)
    prev_year, prev_month = _prev_month(year, month)
    previous = aggregate_month(store, prev_year, prev_month)

    last_3 = []
    y, m = year, month
    for _ in range(3):
        y, m = _prev_month(y, m)
        last_3.append(aggregate_month(store, y, m))

    categories = []
    total_expenditure = current.expenditure or Decimal(1)
    for category, amount in sorted(current.category_totals.items(), key=lambda kv: kv[1]):
        prev_amount = previous.category_totals.get(category, Decimal(0))
        avg_amount = (
            sum((agg.category_totals.get(category, Decimal(0)) for agg in last_3), Decimal(0))
            / 3
        )
        share = f"{(-amount / total_expenditure * 100):.0f}%" if amount < 0 else "—"
        categories.append(
            CategoryLine(
                category=category,
                amount=amount,
                share=share,
                delta_prev=_fmt_delta(amount, prev_amount),
                delta_avg=_fmt_delta(amount, avg_amount),
            )
        )

    expenditures = [
        {
            "booking_date": row["booking_date"],
            "counterparty_name": row["counterparty_name"],
            "amount": Decimal(row["amount"]),
        }
        for row in current.rows
        if Decimal(row["amount"]) < 0
    ]
    top_expenditures = sorted(expenditures, key=lambda r: r["amount"])[:10]

    uncategorised = []
    for row in current.rows:
        if row["category"] in (None, UNCATEGORISED):
            txn = CategorizableTransaction.from_row(row)
            uncategorised.append(
                {
                    "booking_date": row["booking_date"],
                    "counterparty_name": row["counterparty_name"],
                    "remittance_text": row["remittance_text"],
                    "amount": Decimal(row["amount"]),
                    "stub": suggest_rule_stub(txn),
                }
            )

    accounts = ", ".join(sorted({row["account_uid"] for row in current.rows})) or "—"
    last_sync = store.last_sync()
    session = store.latest_session()

    return str(TEMPLATE.render(
        period=f"{year:04d}-{month:02d}",
        currency=currency,
        income=current.income,
        expenditure=current.expenditure,
        net=current.net,
        prev_income=previous.income,
        prev_expenditure=previous.expenditure,
        prev_net=previous.net,
        delta=_fmt_delta,
        categories=categories,
        top_expenditures=top_expenditures,
        uncategorised=uncategorised,
        accounts=accounts,
        synced_at=last_sync["finished_at"] if last_sync else None,
        consent_expires=session["valid_until"] if session else None,
    ))


def write_report(
    store: Store, reports_dir: Path, year: int, month: int, currency: str = "DKK"
) -> Path:
    reports_dir.mkdir(parents=True, exist_ok=True)
    content = render_month(store, year, month, currency=currency)
    path = reports_dir / f"{year:04d}-{month:02d}.md"
    path.write_text(content)
    return path
