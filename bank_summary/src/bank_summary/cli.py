"""Typer CLI — same code path as the add-on, runnable standalone for local/sandbox development."""

from __future__ import annotations

import typer

from .config import Settings
from .enablebanking.client import EnableBankingClient
from .report import write_report
from .store import Store
from .sync import run_sync

app = typer.Typer()


def _settings() -> Settings:
    return Settings()  # populated from env / .env


def _client(settings: Settings) -> EnableBankingClient:
    if not settings.application_id or not settings.private_key_path:
        raise typer.BadParameter(
            "BANK_SUMMARY_APPLICATION_ID and BANK_SUMMARY_PRIVATE_KEY_PATH must be set "
            "(see .env.example) for any command that talks to Enable Banking."
        )
    return EnableBankingClient(
        application_id=settings.application_id,
        private_key_path=str(settings.private_key_path),
        base_url=settings.base_url,
    )


def _store(settings: Settings) -> Store:
    return Store(settings.resolved_db_path())


@app.command()
def aspsps(country: str = typer.Option("DK", help="ISO country code")) -> None:
    """List ASPSPs (banks) available for the given country."""
    settings = _settings()
    with _client(settings) as client:
        for aspsp in client.list_aspsps(country):
            marker = " [sandbox]" if aspsp.sandbox else ""
            typer.echo(f"{aspsp.name}{marker}")


@app.command()
def connect(
    aspsp_name: str = typer.Argument(...),
    aspsp_country: str = typer.Option("DK"),
) -> None:
    """Start the bank authorisation flow: prints a URL to open in a browser."""
    settings = _settings()
    with _client(settings) as client:
        resp = client.start_authorization(
            aspsp_name=aspsp_name,
            aspsp_country=aspsp_country,
            redirect_url=settings.redirect_url,
        )
    typer.echo(f"Open this URL to authorise: {resp.url}")


@app.command()
def exchange(code: str = typer.Argument(...)) -> None:
    """Exchange an authorisation code (from the redirect) for a session, and store accounts."""
    settings = _settings()
    store = _store(settings)
    with _client(settings) as client:
        session = client.create_session(code)
        for account in session.accounts:
            store.upsert_account(account, session.aspsp.name if session.aspsp else None)
        store.upsert_session(
            session.session_id,
            session.aspsp.name if session.aspsp else None,
            session.valid_until,
            session.status,
        )
    typer.echo(f"Linked {len(session.accounts)} account(s), session {session.session_id}")


@app.command()
def accounts() -> None:
    """List locally stored accounts."""
    settings = _settings()
    store = _store(settings)
    for row in store.list_accounts():
        typer.echo(
            f"{row['uid']}  {row['name'] or ''}  {row['iban'] or ''}  {row['currency'] or ''}"
        )


@app.command()
def sync() -> None:
    """Fetch new transactions for all linked accounts and re-run categorisation."""
    settings = _settings()
    store = _store(settings)
    with _client(settings) as client:
        result = run_sync(settings, client, store)
    typer.echo(
        f"Synced {result.accounts_synced} account(s): "
        f"{result.transactions_new} new / {result.transactions_seen} seen, "
        f"{result.recategorized} re-categorised"
    )


@app.command()
def report(month: str = typer.Option(..., help="YYYY-MM")) -> None:
    """Render (or re-render) the Markdown report for a given month."""
    settings = _settings()
    store = _store(settings)
    year_s, month_s = month.split("-")
    path = write_report(store, settings.resolved_reports_dir(), int(year_s), int(month_s))
    typer.echo(f"Wrote {path}")


if __name__ == "__main__":
    app()
