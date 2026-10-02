from __future__ import annotations

from pathlib import Path

from test_web import _client

from bank_summary.enablebanking.models import Amount, Party, Transaction
from bank_summary.html_report import _nice_ticks, render_month_html
from bank_summary.store import Store


def _txn(booking_date: str, amount: str, ref: str, name: str) -> Transaction:
    indicator = "DBIT" if amount.startswith("-") else "CRDT"
    return Transaction(
        entry_reference=ref,
        booking_date=booking_date,
        value_date=booking_date,
        transaction_amount=Amount(amount=amount, currency="DKK"),
        credit_debit_indicator=indicator,
        status="BOOK",
        debtor=Party(name=name if indicator == "CRDT" else "Me"),
        creditor=Party(name="Me" if indicator == "CRDT" else name),
    )


def _seed(store: Store) -> None:
    store.upsert_transaction("acc-1", _txn("2026-08-01", "30000.00", "s8", "Employer"))
    store.upsert_transaction("acc-1", _txn("2026-08-03", "-500.00", "g8", "Netto"))
    store.upsert_transaction("acc-1", _txn("2026-09-01", "31000.00", "s9", "Employer"))
    store.upsert_transaction("acc-1", _txn("2026-09-03", "-750.00", "g9", "Netto"))
    store.upsert_transaction("acc-1", _txn("2026-09-04", "-9000.00", "r9", "Landlord <&>"))
    for key, cat in [("g8", "Groceries"), ("g9", "Groceries"), ("r9", "Rent")]:
        store.set_category(f"ref:acc-1:{key}", cat, "rule")


def test_html_report_has_tiles_charts_and_escapes_text(tmp_path: Path) -> None:
    store = Store(tmp_path / "bank.db")
    _seed(store)

    html = render_month_html(store, 2026, 9, has_month=lambda y, m: (y, m) == (2026, 8))

    assert "<h1>September 2026</h1>" in html
    assert "31,000" in html and "9,750" in html  # income / expenditure tiles
    assert "▲ 3% vs. August" in html  # income up = good
    assert html.count("<svg") == 2  # category bars + monthly trend
    assert "Landlord &lt;&amp;&gt;" in html and "Landlord <&>" not in html
    assert 'href="2026-08"' in html and 'href="2026-10"' not in html
    # Rent (largest) is charted before Groceries.
    assert html.index("Rent: 9,000.00 DKK") < html.index("Groceries: 750.00 DKK")


def test_nice_ticks_cover_the_maximum() -> None:
    assert _nice_ticks(33_377) == [0, 10_000, 20_000, 30_000, 40_000]
    assert _nice_ticks(0) == [0.0, 1.0]


def test_report_route_renders_and_404s(tmp_path: Path) -> None:
    client = _client(tmp_path)
    store = Store(tmp_path / "bank.db")
    _seed(store)
    store.close()

    assert client.get("/report/2026-09").status_code == 200
    assert client.get("/report/2026-07").status_code == 404  # no data
    assert client.get("/report/2026-13").status_code == 404
    assert client.get("/report/nonsense").status_code == 404
