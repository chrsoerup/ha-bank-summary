# Handoff — ha-bank-summary (2026-09-11)

Repo: ~/github/ha-bank-summary, public at github.com/chrsoerup/ha-bank-summary. HEAD 4e1d1ce =
add-on 0.1.7 (M5 changes below), pushed and deployed to the Green. Plan:
~/.claude/plans/floofy-frolicking-spindle.md.

## Status: M1–M5 complete and verified end-to-end on the Green

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

### Still to do

- Verify the account_uid auto-remap for real at the next consent renewal (2026-12-10) — it's only
  been exercised by tests so far, not against a live Enable Banking response.
- **Revisit editing `rules.yaml` from the Green.** File editor only browses HA's `/config`, not
  the add-on's persistent folder at `/addon_configs/2a94ed89_bank_summary/`, and Samba/SSH are
  both blocked for this user (see below) — so there's currently no way to edit `rules.yaml` (fix
  uncategorised transactions) without a workaround. Proposed and user declined a bespoke
  paste-into-Ingress editor (same pattern as the private-key form) as "a bit too hardcoded" —
  revisit with a less special-cased approach, e.g. a generic small file browser/editor for the
  add-on's config dir, or check whether Studio Code Server / another add-on can be pointed at
  `/addon_configs` directly.
- Optional: `ruff format` the 5 pre-existing unformatted files (only `ruff check` is enforced).

## Working-with-the-user notes

- HA UI work: give **one action at a time**, name the exact tab/button, and say what success
  looks like. Don't assume knowledge of HA's file layout (confused `bank_summary/config.yaml`
  with HA's `configuration.yaml`).
- Getting files onto the Green: Samba is blocked by the corporate Windows laptop and SSH was
  never reachable — use the add-on's PEM form, or the File editor add-on for HA YAML.
- The add-on Log tab does not always refresh live; reload the page before concluding "nothing
  happened".
