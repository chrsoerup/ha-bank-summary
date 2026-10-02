from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from test_web import _client

from bank_summary.categorize import (
    CategorizableTransaction,
    append_rule,
    categorize,
    load_rules,
    normalise_merchant,
)
from bank_summary.enablebanking.models import Amount, Party, Transaction
from bank_summary.store import Store


def _ct(text: str, amount: str = "-100.00", mcc: str | None = None) -> CategorizableTransaction:
    return CategorizableTransaction(
        counterparty_name=None,
        remittance_text=text,
        bank_transaction_code=None,
        merchant_category_code=mcc,
        credit_debit_indicator="CRDT" if not amount.startswith("-") else "DBIT",
        amount=Decimal(amount),
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Dankort-nota NETTO 1234 KBH V", "NETTO KBH V"),
        ("VISA/DANKORT 12.09 SUMUP *CAFE VIGGO", "CAFE VIGGO"),
        ("PAYPAL *SPOTIFY 4029357733", "SPOTIFY"),
        ("Visa køb XXXX1234 H&M 0123 Aarhus", "H&M AARHUS"),
        ("MobilePay Jens Hansen", "JENS HANSEN"),
        ("Overførsel til Louise", "LOUISE"),
        ("Dankort-nota GOLFKLUBBEN 88", "GOLFKLUBBEN"),
        ("7-Eleven Banegården", "7-ELEVEN BANEGÅRDEN"),
    ],
)
def test_normalise_merchant(raw: str, expected: str) -> None:
    assert normalise_merchant(raw) == expected


@pytest.mark.parametrize(
    ("text", "amount", "mcc", "expected"),
    [
        ("Dankort-nota NETTO 1234 KBH V", "-89.50", None, "Groceries"),
        ("Rema 1000 Viby", "-212.00", None, "Groceries"),
        ("PAYPAL *SPOTIFY 4029357733", "-119.00", None, "Subscriptions"),
        ("BS Ørsted Salg & Service", "-850.00", None, "Utilities"),
        ("Dankort-nota CIRCLE K 4411", "-500.00", None, "Fuel & charging"),
        ("Lønoverførsel", "32000.00", None, "Salary"),
        ("Saxo Bank opsparing", "-2000.00", None, "Pension & savings"),
        ("Spar Nord overførsel", "-2000.00", None, "Uncategorised"),
        ("SOME LOCAL SHOP", "-75.00", "5411", "Groceries"),  # MCC fallback
        ("Kaffebaren på hjørnet", "-45.00", None, "Uncategorised"),
    ],
)
def test_builtin_rules(
    tmp_path: Path, text: str, amount: str, mcc: str | None, expected: str
) -> None:
    rules = load_rules(tmp_path / "missing.yaml")  # no user file: built-ins only
    assert categorize(_ct(text, amount, mcc), rules)[0] == expected


def test_user_rules_take_precedence_over_builtin(tmp_path: Path) -> None:
    path = tmp_path / "rules.yaml"
    append_rule(path, "Lunch", "NETTO KBH")

    rules = load_rules(path)

    assert categorize(_ct("Dankort-nota NETTO 1234 KBH V"), rules)[0] == "Lunch"
    assert categorize(_ct("Dankort-nota NETTO 9 AARHUS"), rules)[0] == "Groceries"


@pytest.mark.parametrize(
    "initial", ["", "[]", "# my rules\n- category: X\n  match: { text_regex: 'ZZZ' }\n"]
)
def test_append_rule_keeps_file_valid(tmp_path: Path, initial: str) -> None:
    path = tmp_path / "rules.yaml"
    path.write_text(initial)

    append_rule(path, 'Café "fun"', "CAFE VIGGO")

    content = path.read_text()
    if initial.startswith("#"):
        assert content.startswith(initial)  # comments and existing rules untouched
    rules = load_rules(path, builtin=False)
    assert rules[-1].category == 'Café "fun"'
    assert categorize(_ct("SUMUP *CAFE VIGGO 12.09"), rules)[0] == 'Café "fun"'
    assert categorize(_ct("CAFE VIGGOS NABO"), rules)[0] != 'Café "fun"'  # whole words only


def _txn(ref: str, text: str, amount: str) -> Transaction:
    return Transaction(
        entry_reference=ref,
        booking_date="2026-09-10",
        value_date="2026-09-10",
        transaction_amount=Amount(amount=amount, currency="DKK"),
        credit_debit_indicator="DBIT",
        status="BOOK",
        creditor=Party(name=text),
        debtor=Party(name="Me"),
    )


def test_uncategorised_count_includes_the_uncategorised_label(tmp_path: Path) -> None:
    store = Store(tmp_path / "bank.db")
    store.upsert_transaction("acc-1", _txn("a", "X", "-1.00"))
    store.upsert_transaction("acc-1", _txn("b", "Y", "-1.00"))
    store.set_category("ref:acc-1:a", "Uncategorised", "none")

    assert store.uncategorised_count() == 2  # one labelled, one never categorised


def test_categorise_page_groups_and_save_applies_rules(tmp_path: Path) -> None:
    rules_path = tmp_path / "rules.yaml"
    rules_path.write_text("[]")
    client = _client(tmp_path, rules_path=rules_path)
    store = Store(tmp_path / "bank.db")
    store.upsert_transaction("acc-1", _txn("a", "Dankort-nota KAFFEBAREN 1111 KBH", "-40.00"))
    store.upsert_transaction("acc-1", _txn("b", "Dankort-nota KAFFEBAREN 2222 KBH", "-35.00"))
    store.upsert_transaction("acc-1", _txn("c", "MobilePay Jens Hansen", "-200.00"))
    store.close()

    page = client.get("/categorise").text
    assert "3 uncategorised transaction(s) from 2 merchant(s)" in page
    # Largest total first: JENS HANSEN (200) before KAFFEBAREN KBH (75).
    assert page.index("JENS HANSEN") < page.index("KAFFEBAREN KBH")

    resp = client.post(
        "/categorise",
        content="match_0=JENS+HANSEN&category_0=&match_1=KAFFEBAREN&category_1=Caf%C3%A9",
        headers={"content-type": "application/x-www-form-urlencoded"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "categorise?saved=1&recategorised=2"

    store = Store(tmp_path / "bank.db")
    assert store.uncategorised_count() == 1
    assert store.categories_in_use() == ["Café"]
    assert "KAFFEBAREN" in rules_path.read_text()
