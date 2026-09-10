"""FastAPI app: Ingress UI, OAuth callback receiver, health check.

Served on the port declared in `config.yaml`'s `ingress_port`. The OAuth `code`/`state` from the
bank's redirect never reaches this add-on directly (Enable Banking requires an HTTPS redirect on
a real domain, which Ingress URLs are not) — a Home Assistant automation catches them at
`/api/webhook/<id>` and forwards them here via `rest_command` (see DOCS.md).
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, PlainTextResponse
from pydantic import BaseModel

from .config import Settings
from .enablebanking.client import EnableBankingClient
from .scheduler import start_scheduler
from .store import Store

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = Settings()
    app.state.settings = settings
    app.state.scheduler = start_scheduler(settings)
    try:
        yield
    finally:
        app.state.scheduler.shutdown(wait=False)


app = FastAPI(title="Bank Summary", lifespan=lifespan)


def _settings() -> Settings:
    return app.state.settings  # type: ignore[no-any-return]


class CallbackPayload(BaseModel):
    code: str
    state: str | None = None


@app.get("/health", response_class=PlainTextResponse)
def health() -> str:
    return "ok"


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    settings = _settings()
    store = Store(settings.resolved_db_path())
    try:
        last_sync = store.last_sync()
        session = store.latest_session()
        uncategorised = store.uncategorised_count()
        reports_dir = settings.resolved_reports_dir()
        reports = (
            sorted((p.name for p in reports_dir.glob("*.md")), reverse=True)
            if reports_dir.exists()
            else []
        )
    finally:
        store.close()

    report_items = "".join(f'<li><a href="reports/{name}">{name}</a></li>' for name in reports)
    return f"""
    <html>
    <head><title>Bank Summary</title></head>
    <body>
      <h1>Bank Summary</h1>
      <p>Last sync: {last_sync["finished_at"] if last_sync else "never"}</p>
      <p>Consent expires: {session["valid_until"] if session else "not linked yet"}</p>
      <p>Uncategorised transactions: {uncategorised}</p>
      <p><a href="connect">Connect / re-authorise bank account</a></p>
      <h2>Reports</h2>
      <ul>{report_items or "<li>None yet</li>"}</ul>
    </body>
    </html>
    """


@app.get("/connect", response_class=HTMLResponse)
def connect() -> str:
    """Starts the bank authorisation flow and returns the MitID URL to open on a tailnet device.

    Must be opened from a browser reachable by the bank's redirect (see DOCS.md) — Ingress
    sessions cannot complete the flow themselves, since the whitelisted redirect is a plain HA
    webhook URL, not this page.
    """
    settings = _settings()
    missing = [
        opt
        for opt, val in [
            ("application_id", settings.application_id),
            ("private_key_path", settings.private_key_path),
            ("aspsp_name", settings.aspsp_name),
            ("redirect_url", settings.redirect_url),
        ]
        if not val
    ]
    if missing:
        raise HTTPException(
            status_code=400, detail=f"Add-on options not configured yet: {', '.join(missing)}"
        )

    with EnableBankingClient(
        application_id=settings.application_id,  # type: ignore[arg-type]
        private_key_path=str(settings.private_key_path),
        base_url=settings.base_url,
    ) as client:
        resp = client.start_authorization(
            aspsp_name=settings.aspsp_name,  # type: ignore[arg-type]
            aspsp_country=settings.aspsp_country,
            redirect_url=settings.redirect_url,
        )

    return (
        f'<html><body><p>Open this URL on a device on your tailnet and complete the MitID '
        f'login:</p><p><a href="{resp.url}">{resp.url}</a></p></body></html>'
    )


@app.get("/reports/{name}", response_class=PlainTextResponse)
def get_report(name: str) -> str:
    settings = _settings()
    safe_name = Path(name).name  # strip any directory components before joining
    path = settings.resolved_reports_dir() / safe_name
    if path.suffix != ".md" or not path.is_file():
        raise HTTPException(status_code=404, detail="Report not found")
    return path.read_text()


@app.post("/callback")
def callback(payload: CallbackPayload) -> dict[str, object]:
    settings = _settings()
    if not settings.application_id or not settings.private_key_path:
        raise HTTPException(
            status_code=500, detail="Add-on is not configured with Enable Banking credentials"
        )

    store = Store(settings.resolved_db_path())
    try:
        with EnableBankingClient(
            application_id=settings.application_id,
            private_key_path=str(settings.private_key_path),
            base_url=settings.base_url,
        ) as client:
            try:
                session = client.create_session(payload.code)
            except httpx.HTTPStatusError as exc:
                raise HTTPException(
                    status_code=400, detail=f"Enable Banking rejected the code: {exc}"
                ) from exc

            for account in session.accounts:
                store.upsert_account(account, session.aspsp.name if session.aspsp else None)
            store.upsert_session(
                session.session_id,
                session.aspsp.name if session.aspsp else None,
                session.consent_valid_until,
                session.status or "authorized",
            )
    finally:
        store.close()

    logger.info("Linked %d account(s) via callback", len(session.accounts))
    return {"linked_accounts": len(session.accounts), "session_id": session.session_id}
