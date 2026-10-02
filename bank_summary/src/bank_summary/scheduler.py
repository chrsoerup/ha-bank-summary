"""APScheduler daily job: sync -> categorise -> publish HA states -> render current month report.

Runs once at startup (in case the add-on was off during the last scheduled slot) and then every
`sync_interval_hours`. PSD2 allows at most 4 unattended AIS calls/day per account, so this must
stay well below that.
"""

from __future__ import annotations

import logging
from datetime import date, datetime

from apscheduler.schedulers.background import BackgroundScheduler

from .config import Settings
from .digest import build_digest, send_persistent_notification
from .enablebanking.client import EnableBankingClient
from .ha import build_states, publish_states
from .report import prev_month, write_report
from .store import Store
from .sync import run_sync

logger = logging.getLogger(__name__)


def run_daily_job(settings: Settings) -> None:
    store = Store(settings.resolved_db_path())
    try:
        if store.latest_session() is None:
            logger.info("No linked bank session yet; skipping scheduled sync.")
            return
        if not settings.application_id or not settings.private_key_path:
            logger.warning("Enable Banking credentials are not configured; skipping sync.")
            return

        balances: dict[str, str] = {}
        with EnableBankingClient(
            application_id=settings.application_id,
            private_key_path=str(settings.private_key_path),
            base_url=settings.base_url,
        ) as client:
            result = run_sync(settings, client, store)
            logger.info(
                "Sync done: %d account(s), %d transaction(s) seen, %d new, %d recategorised",
                result.accounts_synced,
                result.transactions_seen,
                result.transactions_new,
                result.recategorized,
            )
            for account in store.list_accounts(settings.account_uid):
                try:
                    for balance in client.get_balances(account["uid"]):
                        balances[account["uid"]] = balance.balance_amount.amount
                        break
                except Exception:
                    logger.exception("Failed to fetch balance for account %s", account["uid"])

        # The previous month is re-rendered too: transactions keep posting with last month's
        # booking date for a few days into the new one, and nothing else would pick them up.
        today = date.today()
        last_year, last_month = prev_month(today.year, today.month)
        for year, month in ((last_year, last_month), (today.year, today.month)):
            write_report(
                store,
                settings.resolved_reports_dir(),
                year,
                month,
                currency=settings.currency,
                account_uid=settings.account_uid,
            )
            logger.info("Wrote report for %04d-%02d", year, month)

        if not settings.supervisor_token:
            logger.warning("SUPERVISOR_TOKEN not set; skipping HA state publish")
        else:
            states = build_states(
                store,
                currency=settings.currency,
                consent_expiring_soon_days=settings.consent_expiring_soon_days,
                balances=balances,
                account_uid=settings.account_uid,
            )
            failures = publish_states(states, settings.ha_api_base, settings.supervisor_token)
            if failures:
                logger.warning("%d/%d HA states failed to publish", failures, len(states))
            else:
                logger.info("Published %d HA state(s)", len(states))
            send_monthly_digest(settings, store, last_year, last_month)
    except Exception:
        logger.exception("Scheduled sync failed")
    finally:
        store.close()


def send_monthly_digest(settings: Settings, store: Store, year: int, month: int) -> None:
    """Sends the digest for a closed month once. Only called after a successful sync, so the
    digest never goes out based on stale data."""
    assert settings.supervisor_token
    period = f"{year:04d}-{month:02d}"
    if store.digest_sent(period):
        return
    digest = build_digest(
        store, year, month, currency=settings.currency, account_uid=settings.account_uid
    )
    if digest is None:
        logger.info("No transactions for %s; no digest to send", period)
        return
    title, message = digest
    try:
        send_persistent_notification(
            settings.ha_api_base,
            settings.supervisor_token,
            f"bank_summary_digest_{year:04d}_{month:02d}",
            title,
            message,
        )
    except Exception:
        logger.exception("Failed to send digest for %s; will retry on next sync", period)
        return
    store.mark_digest_sent(period)
    logger.info("Sent monthly digest for %s", period)


def start_scheduler(settings: Settings) -> BackgroundScheduler:
    scheduler = BackgroundScheduler()
    scheduler.add_job(
        run_daily_job,
        "interval",
        hours=settings.sync_interval_hours,
        args=[settings],
        next_run_time=datetime.now(),
        id="daily_sync",
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    return scheduler
