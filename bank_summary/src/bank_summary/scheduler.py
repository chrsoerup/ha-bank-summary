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
from .enablebanking.client import EnableBankingClient
from .ha import build_states, publish_states
from .report import write_report
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

        today = date.today()
        write_report(
            store,
            settings.resolved_reports_dir(),
            today.year,
            today.month,
            currency=settings.currency,
            account_uid=settings.account_uid,
        )
        logger.info("Wrote report for %04d-%02d", today.year, today.month)

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
    except Exception:
        logger.exception("Scheduled sync failed")
    finally:
        store.close()


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
