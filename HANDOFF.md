# Handoff — ha-bank-summary (2026-09-11)

Repo: ~/github/ha-bank-summary, public at github.com/chrsoerup/ha-bank-summary. HEAD a31e863 =
add-on 0.1.6. Plan: ~/.claude/plans/floofy-frolicking-spindle.md.

## Status: M1–M4 complete, verified end-to-end against the real bank

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

## Next: M5 polish, in priority order

1. **Stable account selection.** Enable Banking issues *new account uids on every consent*, so
   `account_uid` breaks at each 90-day renewal. `accounts.identification_hash` (hash of
   IBAN+currency) is populated and stable — let the option accept it (or auto-remap the
   configured uid to the new one on `/callback` by matching `identification_hash`).
2. **"Sync now" button** on the Ingress page (user asked how to trigger a sync; currently only
   restart or the 24 h interval). Respect the PSD2 4-calls/day limit.
3. **Consent-expiry notification**: HA automation on `binary_sensor.bank_consent_expiring`
   (offer to write it; user was interested in Overview/notification behaviour).
4. DOCS.md: mention `/config` mount, the re-consent → uid change caveat, rules.yaml location
   on the host; wrap the three long lines edited by sed.
5. Optional: `ruff format` the 5 pre-existing unformatted files (only `ruff check` is enforced).

## Working-with-the-user notes

- HA UI work: give **one action at a time**, name the exact tab/button, and say what success
  looks like. Don't assume knowledge of HA's file layout (confused `bank_summary/config.yaml`
  with HA's `configuration.yaml`).
- Getting files onto the Green: Samba is blocked by the corporate Windows laptop and SSH was
  never reachable — use the add-on's PEM form, or the File editor add-on for HA YAML.
- The add-on Log tab does not always refresh live; reload the page before concluding "nothing
  happened".
