Handoff — ha-bank-summary

Repo: ~/github/ha-bank-summary, pushed to github.com/chrsoerup/ha-bank-summary (private). HEAD: 2dc8d2d.

Done (M1 + M2 of the plan at ~/.claude/plans/floofy-frolicking-spindle.md):
- Enable Banking JWT auth, API client with continuation_key pagination, SQLite store with pending→booked dedupe, rule-based categorisation, Jinja2 monthly Markdown report.
- Typer CLI: aspsps, connect, exchange, accounts, sync, report --month YYYY-MM.
- 16 unit tests, ruff + mypy clean.
- Smoke-tested against fabricated demo data — found and fixed a real bug (broken YAML indentation in the uncategorised-transaction rule stub).

Blocked on, in order — both need your action, not mine:

1. M0 — Enable Banking signup. Sign up, register an application, get the app ID + .pem. The redirect URL must be HTTPS on a real domain (the plan uses a Tailscale-Serve + HA-webhook callback) — don't reuse the old prototype's http://localhost:8000/callback. Hand me the app ID and .pem path when you have them and I'll wire up .env and do a real sandbox run.
2. SSH to the Home Assistant Green, needed for M3 (add-on packaging/install) onward. The "Terminal & SSH" add-on shows Running but ports 22/2222/22222 are all refused from this WSL machine over Tailscale (homeassistant.tailaf1ceb.ts.net, 100.116.138.61). Check the add-on's Network tab for the actual port it's mapped to, and its Configuration tab for authorized_keys — my WSL pubkey ends ...z/Db22kK christian.sorup@laerdal.com.

Not started: M3 (add-on packaging: config.yaml, Dockerfile, run.sh, ingress web.py, ha.py, scheduler.py), M4 (real bank/consent), M5 (polish).

Housekeeping note: the old ~/github/privat-economy-budget-overview-cs prototype (deleted from your GitHub) is still sitting on disk locally, abandoned — fine to leave or delete, your call.

Both blockers are saved to memory (ha-bank-summary-project.md), so if we pick this up in a new session I'll have this context without re-deriving it
