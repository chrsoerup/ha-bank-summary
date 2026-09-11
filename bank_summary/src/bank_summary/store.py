"""SQLite persistence: accounts, transactions, sessions, sync_log.

No ORM — the schema is small and stable enough that raw SQL is clearer than an abstraction layer.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from .enablebanking.models import AccountRef, Transaction

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    uid TEXT PRIMARY KEY,
    iban TEXT,
    name TEXT,
    currency TEXT,
    aspsp_name TEXT,
    identification_hash TEXT
);

CREATE TABLE IF NOT EXISTS transactions (
    dedupe_key TEXT PRIMARY KEY,
    account_uid TEXT NOT NULL,
    booking_date TEXT,
    value_date TEXT,
    amount TEXT NOT NULL,
    currency TEXT NOT NULL,
    credit_debit_indicator TEXT NOT NULL,
    counterparty_name TEXT,
    remittance_text TEXT,
    bank_transaction_code TEXT,
    merchant_category_code TEXT,
    status TEXT NOT NULL,
    category TEXT,
    category_source TEXT,
    raw_json TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_transactions_booking_date ON transactions(booking_date);
CREATE INDEX IF NOT EXISTS idx_transactions_account ON transactions(account_uid);
CREATE INDEX IF NOT EXISTS idx_transactions_pending_match
    ON transactions(account_uid, amount, currency, remittance_text)
    WHERE status = 'PDNG';

CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    aspsp_name TEXT,
    valid_until TEXT,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sync_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    transactions_seen INTEGER DEFAULT 0,
    transactions_new INTEGER DEFAULT 0,
    error TEXT
);
"""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def dedupe_key(account_uid: str, txn: Transaction) -> str:
    if txn.entry_reference:
        return f"ref:{account_uid}:{txn.entry_reference}"
    if txn.transaction_id:
        return f"txn:{account_uid}:{txn.transaction_id}"
    basis = "|".join(
        [
            account_uid,
            txn.booking_date or "",
            txn.signed_amount,
            txn.transaction_amount.currency,
            txn.remittance_text or "",
        ]
    )
    digest = hashlib.sha256(basis.encode("utf-8")).hexdigest()
    return f"hash:{digest}"


class Store:
    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        try:
            yield self._conn
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def upsert_account(self, account: AccountRef, aspsp_name: str | None) -> None:
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO accounts (uid, iban, name, currency, aspsp_name, identification_hash)
                VALUES (:uid, :iban, :name, :currency, :aspsp_name, :identification_hash)
                ON CONFLICT(uid) DO UPDATE SET
                    iban=excluded.iban,
                    name=excluded.name,
                    currency=excluded.currency,
                    aspsp_name=excluded.aspsp_name,
                    identification_hash=excluded.identification_hash
                """,
                {
                    "uid": account.uid,
                    "iban": account.iban,
                    "name": account.name,
                    "currency": account.currency,
                    "aspsp_name": aspsp_name,
                    "identification_hash": account.identification_hash,
                },
            )

    def upsert_transaction(self, account_uid: str, txn: Transaction) -> bool:
        """Insert or refresh a transaction. Returns True if it is new (not previously stored).

        A newly booked transaction that matches an existing pending row (same account, amount,
        currency, remittance text) replaces it — pending and booked copies of the same movement
        rarely share an `entry_reference`/`transaction_id`, so the dedupe key alone won't catch
        that they're the same underlying transaction.
        """
        key = dedupe_key(account_uid, txn)
        now = _now()
        with self.transaction() as conn:
            existing = conn.execute(
                "SELECT first_seen, category, category_source FROM transactions"
                " WHERE dedupe_key = ?",
                (key,),
            ).fetchone()

            if txn.status == "BOOK" and existing is None:
                pending = conn.execute(
                    """
                    SELECT dedupe_key FROM transactions
                    WHERE account_uid = ? AND amount = ? AND currency = ?
                      AND COALESCE(remittance_text, '') = ? AND status = 'PDNG'
                      AND dedupe_key != ?
                    """,
                    (account_uid, txn.signed_amount, txn.transaction_amount.currency,
                     txn.remittance_text or "", key),
                ).fetchone()
                if pending is not None:
                    conn.execute(
                        "DELETE FROM transactions WHERE dedupe_key = ?", (pending["dedupe_key"],)
                    )

            first_seen = existing["first_seen"] if existing else now
            category = existing["category"] if existing else None
            category_source = existing["category_source"] if existing else None

            conn.execute(
                """
                INSERT INTO transactions (
                    dedupe_key, account_uid, booking_date, value_date, amount, currency,
                    credit_debit_indicator, counterparty_name, remittance_text,
                    bank_transaction_code, merchant_category_code, status,
                    category, category_source, raw_json, first_seen, last_seen
                ) VALUES (
                    :dedupe_key, :account_uid, :booking_date, :value_date, :amount, :currency,
                    :credit_debit_indicator, :counterparty_name, :remittance_text,
                    :bank_transaction_code, :merchant_category_code, :status,
                    :category, :category_source, :raw_json, :first_seen, :last_seen
                )
                ON CONFLICT(dedupe_key) DO UPDATE SET
                    booking_date=excluded.booking_date,
                    value_date=excluded.value_date,
                    amount=excluded.amount,
                    currency=excluded.currency,
                    credit_debit_indicator=excluded.credit_debit_indicator,
                    counterparty_name=excluded.counterparty_name,
                    remittance_text=excluded.remittance_text,
                    bank_transaction_code=excluded.bank_transaction_code,
                    merchant_category_code=excluded.merchant_category_code,
                    status=excluded.status,
                    raw_json=excluded.raw_json,
                    last_seen=excluded.last_seen
                """,
                {
                    "dedupe_key": key,
                    "account_uid": account_uid,
                    "booking_date": txn.booking_date,
                    "value_date": txn.value_date,
                    "amount": txn.signed_amount,
                    "currency": txn.transaction_amount.currency,
                    "credit_debit_indicator": txn.credit_debit_indicator,
                    "counterparty_name": txn.counterparty_name,
                    "remittance_text": txn.remittance_text,
                    "bank_transaction_code": txn.bank_transaction_code,
                    "merchant_category_code": txn.merchant_category_code,
                    "status": txn.status,
                    "category": category,
                    "category_source": category_source,
                    "raw_json": json.dumps(txn.model_dump(mode="json")),
                    "first_seen": first_seen,
                    "last_seen": now,
                },
            )
        return existing is None

    def set_category(self, dedupe_key_: str, category: str, source: str) -> None:
        with self.transaction() as conn:
            conn.execute(
                "UPDATE transactions SET category = ?, category_source = ? WHERE dedupe_key = ?",
                (category, source, dedupe_key_),
            )

    def transactions_for_month(
        self, year: int, month: int, account_uid: str | None = None
    ) -> list[sqlite3.Row]:
        prefix = f"{year:04d}-{month:02d}"
        if account_uid:
            return list(
                self._conn.execute(
                    "SELECT * FROM transactions WHERE booking_date LIKE ? AND account_uid = ? "
                    "ORDER BY booking_date",
                    (f"{prefix}%", account_uid),
                ).fetchall()
            )
        return list(
            self._conn.execute(
                "SELECT * FROM transactions WHERE booking_date LIKE ? ORDER BY booking_date",
                (f"{prefix}%",),
            ).fetchall()
        )

    def all_transactions(self) -> list[sqlite3.Row]:
        return list(
            self._conn.execute("SELECT * FROM transactions ORDER BY booking_date").fetchall()
        )

    def uncategorised_count(self, account_uid: str | None = None) -> int:
        if account_uid:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM transactions WHERE category IS NULL AND account_uid = ?",
                (account_uid,),
            ).fetchone()
        else:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM transactions WHERE category IS NULL"
            ).fetchone()
        return int(row["n"])

    def start_sync_log(self) -> int:
        with self.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO sync_log (started_at) VALUES (?)", (_now(),)
            )
            assert cur.lastrowid is not None
            return cur.lastrowid

    def finish_sync_log(
        self, log_id: int, transactions_seen: int, transactions_new: int, error: str | None = None
    ) -> None:
        with self.transaction() as conn:
            conn.execute(
                """
                UPDATE sync_log
                SET finished_at = ?, transactions_seen = ?, transactions_new = ?, error = ?
                WHERE id = ?
                """,
                (_now(), transactions_seen, transactions_new, error, log_id),
            )

    def last_sync(self) -> sqlite3.Row | None:
        return cast(
            "sqlite3.Row | None",
            self._conn.execute("SELECT * FROM sync_log ORDER BY id DESC LIMIT 1").fetchone(),
        )

    def upsert_session(
        self, session_id: str, aspsp_name: str | None, valid_until: str | None, status: str
    ) -> None:
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO sessions (session_id, aspsp_name, valid_until, created_at, status)
                VALUES (:session_id, :aspsp_name, :valid_until, :created_at, :status)
                ON CONFLICT(session_id) DO UPDATE SET
                    aspsp_name=excluded.aspsp_name,
                    valid_until=excluded.valid_until,
                    status=excluded.status
                """,
                {
                    "session_id": session_id,
                    "aspsp_name": aspsp_name,
                    "valid_until": valid_until,
                    "created_at": _now(),
                    "status": status,
                },
            )

    def list_accounts(self, account_uid: str | None = None) -> list[sqlite3.Row]:
        if account_uid:
            return list(
                self._conn.execute(
                    "SELECT * FROM accounts WHERE uid = ?", (account_uid,)
                ).fetchall()
            )
        return list(self._conn.execute("SELECT * FROM accounts").fetchall())

    def find_account_by_identification_hash(self, identification_hash: str) -> sqlite3.Row | None:
        return cast(
            "sqlite3.Row | None",
            self._conn.execute(
                "SELECT * FROM accounts WHERE identification_hash = ?", (identification_hash,)
            ).fetchone(),
        )

    def find_account_by_iban(self, iban: str) -> sqlite3.Row | None:
        return cast(
            "sqlite3.Row | None",
            self._conn.execute("SELECT * FROM accounts WHERE iban = ?", (iban,)).fetchone(),
        )

    def remap_account_uid(self, old_uid: str, new_uid: str) -> None:
        """Re-key a linked account from `old_uid` to `new_uid` after a consent renewal.

        Enable Banking reissues `uid` on every new consent even for the same physical account
        (`identification_hash`/`iban` stay stable) — without this, the account and all its
        transaction history become orphaned under a uid nothing references anymore.
        """
        if old_uid == new_uid:
            return
        with self.transaction() as conn:
            rows = conn.execute(
                "SELECT * FROM transactions WHERE account_uid = ?", (old_uid,)
            ).fetchall()
            for row in rows:
                txn = load_raw_transaction(row)
                new_key = dedupe_key(new_uid, txn)
                try:
                    conn.execute(
                        "UPDATE transactions SET account_uid = ?, dedupe_key = ? "
                        "WHERE dedupe_key = ?",
                        (new_uid, new_key, row["dedupe_key"]),
                    )
                except sqlite3.IntegrityError:
                    # A row already exists under the new uid with the same dedupe key (e.g. it
                    # was already synced once under the new consent) — drop the stale duplicate.
                    conn.execute(
                        "DELETE FROM transactions WHERE dedupe_key = ?", (row["dedupe_key"],)
                    )

            try:
                conn.execute("UPDATE accounts SET uid = ? WHERE uid = ?", (new_uid, old_uid))
            except sqlite3.IntegrityError:
                # A row for the new uid was already inserted (e.g. by upsert_account) — drop the
                # now-redundant old row instead.
                conn.execute("DELETE FROM accounts WHERE uid = ?", (old_uid,))

    def latest_session(self) -> sqlite3.Row | None:
        return cast(
            "sqlite3.Row | None",
            self._conn.execute(
                "SELECT * FROM sessions ORDER BY created_at DESC LIMIT 1"
            ).fetchone(),
        )


def load_raw_transaction(row: sqlite3.Row) -> Transaction:
    return Transaction.model_validate(json.loads(row["raw_json"]))
