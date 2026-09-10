# ha-bank-summary

A Home Assistant add-on that syncs transactions from a personal bank account via
[Enable Banking](https://enablebanking.com) (PSD2, read-only), categorises them with an editable
rules file, publishes live sensors to the HA dashboard, and archives a per-month Markdown report.

This repo doubles as a Home Assistant **add-on repository** — paste its GitHub URL into
**Settings → Add-ons → Add-on Store → ⋮ → Repositories** to install the add-on it contains.

The add-on itself lives in [`bank_summary/`](bank_summary/); see
[`bank_summary/DOCS.md`](bank_summary/DOCS.md) for full add-on installation, bank linking, and
sensor/report documentation. For local/sandbox development, the same code is CLI-first:

```sh
cd bank_summary
uv sync
cp .env.example .env   # fill in BANK_SUMMARY_APPLICATION_ID / _PRIVATE_KEY_PATH
uv run bank-summary aspsps --country DK
uv run bank-summary connect "<aspsp name>"
uv run bank-summary exchange <code>
uv run bank-summary sync
uv run bank-summary report --month 2026-08
```

No secrets are committed: `.pem` keys, the SQLite database, and generated reports are all
gitignored and live only on the machine running the add-on.

## Status

- **M1 (core library)**, **M2 (categorisation + report)**, and **M3 (add-on packaging)** are
  implemented and tested (31 unit tests, ruff + mypy strict clean): add-on manifest, Dockerfile,
  Ingress UI (`web.py`), OAuth callback receiver, HA sensor publishing (`ha.py`), and the daily
  scheduler (`scheduler.py`) are all in place.
- **Not yet verified against the real thing** — this has only run against fixtures and Enable
  Banking's sandbox mock. **M4 (real bank, real consent)** needs a working Enable Banking
  application (app ID + private key) and SSH/Samba access to the target Home Assistant Green to
  actually install the add-on and complete a live MitID authorisation.
