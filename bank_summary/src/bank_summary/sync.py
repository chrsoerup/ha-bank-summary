"""Fetch -> store -> categorise. Publishing to Home Assistant is a separate, optional step
(see `ha.py`) invoked by the caller after a successful sync, not part of this module."""

from __future__ import annotations

from dataclasses import dataclass

from .categorize import load_rules, recategorize_all
from .config import Settings
from .enablebanking.client import EnableBankingClient
from .store import Store


@dataclass
class SyncResult:
    accounts_synced: int
    transactions_seen: int
    transactions_new: int
    recategorized: int


def run_sync(settings: Settings, client: EnableBankingClient, store: Store) -> SyncResult:
    session_row = store.latest_session()
    if session_row is None:
        raise RuntimeError("No linked session. Run the authorisation flow first.")

    log_id = store.start_sync_log()
    accounts_synced = 0
    transactions_seen = 0
    transactions_new = 0

    try:
        account_uids = [row["uid"] for row in store.list_accounts(settings.account_uid)]

        for account_uid in account_uids:
            transactions_seen_for_account = 0
            for txn in client.iter_transactions(account_uid):
                is_new = store.upsert_transaction(account_uid, txn)
                transactions_seen_for_account += 1
                if is_new:
                    transactions_new += 1
            transactions_seen += transactions_seen_for_account
            accounts_synced += 1

        rules = load_rules(settings.resolved_rules_path())
        recategorized = recategorize_all(store, rules)

        store.finish_sync_log(log_id, transactions_seen, transactions_new)
        return SyncResult(
            accounts_synced=accounts_synced,
            transactions_seen=transactions_seen,
            transactions_new=transactions_new,
            recategorized=recategorized,
        )
    except Exception as exc:
        store.finish_sync_log(log_id, transactions_seen, transactions_new, error=str(exc))
        raise
