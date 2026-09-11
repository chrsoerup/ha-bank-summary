from __future__ import annotations

from pathlib import Path

from bank_summary.enablebanking.models import AccountRef, Amount, Party, Transaction
from bank_summary.store import Store


def _pending() -> Transaction:
    return Transaction(
        transaction_id="ptxn-1",
        booking_date=None,
        value_date="2026-01-04",
        transaction_amount=Amount(amount="42.00", currency="DKK"),
        credit_debit_indicator="DBIT",
        status="PDNG",
        debtor=Party(name="Christian"),
        creditor=Party(name="Netto"),
        remittance_information_unstructured="NETTO KOEBENHAVN",
    )


def _booked() -> Transaction:
    return Transaction(
        entry_reference="e-123",
        booking_date="2026-01-05",
        value_date="2026-01-04",
        transaction_amount=Amount(amount="42.00", currency="DKK"),
        credit_debit_indicator="DBIT",
        status="BOOK",
        debtor=Party(name="Christian"),
        creditor=Party(name="Netto"),
        remittance_information_unstructured="NETTO KOEBENHAVN",
    )


def test_pending_transaction_replaced_by_booked_twin(tmp_path: Path) -> None:
    store = Store(tmp_path / "bank.db")

    is_new = store.upsert_transaction("acc-1", _pending())
    assert is_new is True
    assert len(store.all_transactions()) == 1
    assert store.all_transactions()[0]["status"] == "PDNG"

    is_new = store.upsert_transaction("acc-1", _booked())
    assert is_new is True

    rows = store.all_transactions()
    assert len(rows) == 1
    assert rows[0]["status"] == "BOOK"
    assert rows[0]["booking_date"] == "2026-01-05"


def test_resync_is_idempotent(tmp_path: Path) -> None:
    store = Store(tmp_path / "bank.db")
    txn = _booked()

    for _ in range(3):
        store.upsert_transaction("acc-1", txn)

    assert len(store.all_transactions()) == 1


def test_recategorization_preserves_first_seen_but_updates_fields(tmp_path: Path) -> None:
    store = Store(tmp_path / "bank.db")
    store.upsert_transaction("acc-1", _pending())
    first_seen_before = store.all_transactions()[0]["first_seen"]

    store.set_category(
        store.all_transactions()[0]["dedupe_key"], "Groceries", "rule"
    )
    store.upsert_transaction("acc-1", _pending())  # re-sync the same pending txn again

    row = store.all_transactions()[0]
    assert row["first_seen"] == first_seen_before
    assert row["category"] == "Groceries"  # preserved across re-sync


def test_find_account_by_identification_hash_and_iban(tmp_path: Path) -> None:
    store = Store(tmp_path / "bank.db")
    store.upsert_account(
        AccountRef(uid="acc-1", iban="DK123", identification_hash="idh-1"), "Some Bank"
    )

    assert store.find_account_by_identification_hash("idh-1")["uid"] == "acc-1"
    assert store.find_account_by_iban("DK123")["uid"] == "acc-1"
    assert store.find_account_by_identification_hash("unknown") is None
    assert store.find_account_by_iban("unknown") is None


def test_remap_account_uid_moves_account_and_transactions(tmp_path: Path) -> None:
    store = Store(tmp_path / "bank.db")
    store.upsert_account(
        AccountRef(uid="acc-1", iban="DK123", identification_hash="idh-1"), "Some Bank"
    )
    store.upsert_transaction("acc-1", _booked())
    other = _booked()
    other.entry_reference = "e-456"
    store.upsert_transaction("acc-1", other)
    old_keys = {row["dedupe_key"] for row in store.all_transactions()}

    store.remap_account_uid("acc-1", "acc-2")

    accounts = store.list_accounts()
    assert [row["uid"] for row in accounts] == ["acc-2"]
    assert accounts[0]["identification_hash"] == "idh-1"

    transactions = store.all_transactions()
    assert len(transactions) == 2
    assert all(row["account_uid"] == "acc-2" for row in transactions)
    assert {row["dedupe_key"] for row in transactions}.isdisjoint(old_keys)


def test_remap_account_uid_drops_stale_duplicate_on_collision(tmp_path: Path) -> None:
    store = Store(tmp_path / "bank.db")
    store.upsert_transaction("acc-1", _booked())
    store.upsert_transaction("acc-2", _booked())  # already synced once under the new uid

    store.remap_account_uid("acc-1", "acc-2")

    transactions = store.all_transactions()
    assert len(transactions) == 1
    assert transactions[0]["account_uid"] == "acc-2"


def test_remap_account_uid_is_a_noop_for_same_uid(tmp_path: Path) -> None:
    store = Store(tmp_path / "bank.db")
    store.upsert_transaction("acc-1", _booked())

    store.remap_account_uid("acc-1", "acc-1")

    assert len(store.all_transactions()) == 1
