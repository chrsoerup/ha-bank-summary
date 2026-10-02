# Handoff — ha-bank-summary (2026-10-02)

Repo: ~/github/ha-bank-summary, public at github.com/chrsoerup/ha-bank-summary. HEAD `4cb6478` =
add-on 0.2.0, pushed. 0.1.8 and 0.1.9 are confirmed running on the Green; **0.2.0 is pushed but not
yet confirmed deployed**. Plan: ~/.claude/plans/floofy-frolicking-spindle.md.

## Status (2026-09-11): M1–M5 complete and verified end-to-end on the Green

The Bank Summary add-on runs on the Home Assistant Green, is linked to Arbejdernes Landsbank via
a real MitID consent completed *through the add-on* (Ingress `/connect` → Tailscale webhook → HA
automation → `/callback`), and has produced a full, correct `2026-09.md` report from live data.
Sensors publish to HA on every sync.

### Live facts

| Item | Value |
|---|---|
| Enable Banking app (production, restricted) | `47ec09c3-a580-4688-99d3-7e28ff69c9a3`, `.pem` at `bank_summary/secrets/enablebanking_private_key.pem` (WSL only, gitignored) |
| ASPSP | `Arbejdernes Landsbank`, country `DK` |
| Redirect URL (whitelisted) | `https://homeassistant.tailaf1ceb.ts.net/api/webhook/f8836cc779c247430a844e4ea477242d` |
| HA Green | `https://homeassistant.tailaf1ceb.ts.net/` via Tailscale Serve |
| Add-on hostname on the Green | `2a94ed89-bank-summary` (slug `2a94ed89_bank_summary`) |
| Persistent add-on folder (host) | `/addon_configs/2a94ed89_bank_summary/` → `/config` inside the container |
| Consent valid until | 2026-12-10 |
| `account_uid` currently synced | `b28cef6e-29f5-4699-b0d3-6fab3279acf6` (Louise's active spending account) |
| Other linked uids (not synced) | `256d6cd8-…` (Christian), `d89c146b-…` (Louise, low-activity) |

HA-side config on the Green (done, via File editor add-on): `configuration.yaml` has the
`rest_command.bank_summary_callback` block pointing at the hostname above; `automations.yaml`
has `bank_summary_oauth_callback` (webhook GET, `local_only: false`). Both as in
`bank_summary/DOCS.md`.

### Bugs found and fixed against the real HA install today (all pushed)

- `map: addon_config` mounts at **`/config`**, not `/addon_config` — key and rules.yaml were
  being written to an ephemeral dir and wiped on every restart/update (0.1.4).
- `run.sh` never exported `BANK_SUMMARY_ACCOUNT_UID` — the option was silently ignored (0.1.1).
- Add-on `/connect` sent `access: {}` → 422 from `/auth`; default `valid_until` (90 d) now lives
  in the client so every caller gets it (0.1.2).
- Bank login URL must open outside the Ingress iframe (`target=_blank`) (0.1.3).
- App INFO logs never reached the add-on log (uvicorn-only logging) (0.1.5).
- Added: paste-the-PEM form on the Ingress page (`POST /private-key`), linked-accounts list with
  `account_uid` mismatch warning, Enable Banking error text surfaced from `/connect`.
- DOCS: hostname rule (every `_` → `-`), automation needs an `id:` for traces,
  `trigger.query.get('state', '')`.

## M5, implemented and deployed today (0.1.7, confirmed on the Green)

1. **Stable account selection.** `/callback` now matches an incoming account against a
   previously linked one by `identification_hash` (falling back to IBAN) and, on a uid change,
   calls `Store.remap_account_uid` (rewrites `accounts.uid` and every `transactions` row's
   `account_uid`/`dedupe_key`), updates `settings.account_uid` in memory, and persists it via a
   new `ha.update_account_uid_option` call to the Supervisor API
   (`POST http://supervisor/addons/self/options`). Re-consent no longer breaks sync or needs a
   manual option edit.
2. **"Sync now" button** on the Ingress index page — `POST /sync-now` runs
   `scheduler.run_daily_job` inline and redirects back; the index also now shows the last sync
   error inline if one occurred.
3. **Consent-expiry notification**: sample HA automation added to DOCS.md, triggering on
   `binary_sensor.bank_consent_expiring` → `on` (docs-only; the add-on can't own HA automations
   itself).
4. **DOCS.md touch-ups**: noted Samba is blocked on installs like the Green (use the Ingress
   paste form), documented the auto-remap-on-reconsent behaviour, added a troubleshooting entry
   for when the auto-remap can't find a match (set `account_uid` manually from the linked-accounts
   list).

New tests in `test_store.py` (remap, identification_hash/IBAN lookups), `test_web.py` (callback
remap end-to-end, sync-now, last-sync-error display), `test_ha.py` (Supervisor option POST).
`ruff check`, `mypy --strict`, and `pytest` all clean (48 tests).

Deployed and verified 2026-09-11: update to 0.1.7 went cleanly (private key and `account_uid`
option both survived the update this time, no re-paste needed like 0.1.4), startup sync ran
(272 transactions, report written, 9 sensors published), and the Ingress page shows the Sync now
button with the correct account still marked synced.

## 2026-10-02: digest, visual report, categorisation (0.1.8 → 0.2.0)

Christian expected a monthly digest on Oct 1 and got none. He had picked "Dashboard only" during
planning, so nothing had been built. Three releases followed the same day:

- **0.1.8 `02c0d1a`: monthly digest (confirmed: the Sept digest arrived).** `digest.py` builds a
  persistent notification (income/expenditure/net vs. the previous month, top 5 spending
  categories, uncategorised count), sent from `scheduler.send_monthly_digest` on the first
  *successful* sync after a month closes. The new `digests` table makes it once-per-month: a failed
  send retries on the next sync, and restarts never resend. Months with no transactions are
  skipped. **Bug fixed:** the daily job only rendered the current month's report, so a closed
  month never picked up late-posting transactions; now the previous month is re-rendered on every
  sync.
- **0.1.9 `1b566cb`: visual HTML report (confirmed working).** `html_report.py` serves
  `/report/YYYY-MM` live from the DB. It has stat tiles (net is the hero), category bars with a
  previous-month marker, a 12-month income/expenditure column chart, largest expenditures, and
  uncategorised transactions. Charts are server-side inline SVG with no JS or CDN, so they work in
  the Ingress iframe. Light and dark mode follow `prefers-color-scheme`. Colours are the dataviz
  skill's validated slots 1–2, checked with its validator in both modes. Every chart has a
  table view. `report.month_context` is now the shared data source for the Markdown and HTML
  renderers, and the .md archive is kept and linked.
- **0.2.0 `4cb6478`: built-in rules + Categorise page (deploy unconfirmed).**
  - `rules.builtin.yaml` ships in the package and is evaluated **after** the user's
    `/config/rules.yaml`. That way it reaches existing installs on every update (the user file is
    only seeded once) and never overrides the user. It holds about 20 categories of Danish
    merchants, bills and income texts, plus an MCC fallback. The seed `rules.default.yaml` is now
    a near-empty commented file.
  - The new `text_regex` matcher checks the counterparty, the remittance text, and
    `normalise_merchant()` output. The normaliser repeatedly strips payment prefixes
    (Dankort-nota, VISA/DANKORT, MobilePay, SumUp, PayPal, …), transfer wording ("Overførsel
    til"), card masks, dates, times, reference numbers and short trailing numbers.
  - `/categorise` (`categorise_page.py`) groups uncategorised transactions by
    `merchant_key` (the first 2 normalised words), largest absolute total first, top 40. Saving
    calls `categorize.append_rule`, which appends a whole-word `text_regex` rule to rules.yaml,
    keeps comments, handles a `[]` file, and rolls back if the result doesn't parse. It then calls
    `scheduler.refresh_after_rules_change` (recategorise, rewrite current and previous .md, publish
    sensors). This resolves the old "can't edit rules.yaml from the Green" item.
  - **Bug fixed:** `sensor.bank_uncategorised_count` only counted `category IS NULL`, but
    `categorize()` stores the literal `"Uncategorised"`, so it has read ~0 since the first sync.
    Expect a visible jump after updating.

Tests: 79 passing; `ruff check` and `mypy --strict src` clean (`tests/` has pre-existing mypy
errors from the `Settings(_env_file=...)` pattern, not enforced). Screenshots of both new pages
were checked in light, dark and phone widths. That check caught an overflowing Net tile and
unreadable phone chart text, now fixed. The no-sudo headless-Chromium recipe is in Claude's memory
(`wsl-headless-chromium-screenshots`).

### Still to do

- **Confirm 0.2.0 on the Green**: update via Add-on Store → check the Categorise page loads and
  the uncategorised list is much shorter than before.
- **Tune merchant grouping on real data.** The built-in patterns and `normalise_merchant` were
  tested on fabricated Danish bank texts only; Christian hasn't shared real ones. If a group looks
  wrong, ask for the example text shown under it on the Categorise page.
- **Per-transaction override** doesn't exist; every categorisation is a merchant-wide rule.
  Christian has been told; likely future ask.
- Category chart is cramped at phone width (offered a taller narrow-screen layout; not taken up).
- Verify the `account_uid` auto-remap for real at the consent renewal on **2026-12-10**.
- Optional: `ruff format` the pre-existing unformatted files.

## Working-with-the-user notes

- Think about what he'll *notice missing*, not just the literal spec: he picked "Dashboard only"
  notifications, then expected a monthly digest anyway.

- HA UI work: give **one action at a time**, name the exact tab/button, and say what success
  looks like. Don't assume knowledge of HA's file layout (confused `bank_summary/config.yaml`
  with HA's `configuration.yaml`).
- Getting files onto the Green: Samba is blocked by the corporate Windows laptop and SSH was
  never reachable — use the add-on's PEM form, or the File editor add-on for HA YAML.
- The add-on Log tab does not always refresh live; reload the page before concluding "nothing
  happened".
