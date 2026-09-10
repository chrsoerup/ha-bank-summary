# Bank Summary

Syncs transactions from a personal bank account via [Enable Banking](https://enablebanking.com)
(PSD2, read-only), categorises them with an editable rules file, publishes live sensors to the
Home Assistant dashboard, and archives a per-month Markdown report.

## Before you install

1. **Register an Enable Banking application** at
   [enablebanking.com](https://enablebanking.com): sign up, create an application, and download
   the generated `<app-id>.pem` private key. Free "restricted production" access works for
   personal use on accounts you link yourself — no sales contact needed.
2. **Pick a redirect URL.** Enable Banking requires the redirect to be **HTTPS on a real
   domain** — `http://homeassistant.local` and Ingress URLs (which embed a per-session token)
   both fail. The supported pattern is a Home Assistant **webhook** reached over your Tailscale
   tailnet:

   ```
   https://<your-tailnet-host>.ts.net/api/webhook/<a-random-id-you-choose>
   ```

   Whitelist this exact URL in your Enable Banking application, and use it as this add-on's
   `redirect_url` option.

## Installation

1. Add this repository to **Settings → Add-ons → Add-on Store → ⋮ → Repositories**.
2. Install **Bank Summary** and open its **Configuration** tab.
3. Set:
   - `application_id` — your Enable Banking application ID.
   - `private_key_path` — the filename the `.pem` will be stored under in
     `/addon_configs/bank_summary/`, e.g. `enablebanking_private_key.pem`. You don't need to copy
     the file over yourself: after starting the add-on, open its **Web UI** and paste the key's
     contents into the *Enable Banking private key* form (it's written owner-read-only). If you'd
     rather upload the file directly, the **Samba share** or **File editor** add-on works too.
   - `aspsp_country` — ISO country code of your bank (e.g. `DK`).
   - `redirect_url` — the webhook URL from step 2 above.
   - `environment` — `sandbox` while testing, `production` once you're ready to link the real
     bank.
   - `account_uid` — optional. If your consent links more than one account (e.g. a joint account
     alongside your own), set this to the one `uid` from `bank-summary accounts` you actually want
     synced, categorised, and published as sensors; leave blank to use all linked accounts.
4. Start the add-on and check its log for `Starting Bank Summary on port 8099`.
5. Find your bank's exact ASPSP name: open **Web UI** (Ingress) and check the add-on log after a
   sync attempt, or run `aspsps --country DK` via the CLI on a dev machine against the same
   credentials. Set `aspsp_name` to the exact string returned.

## Linking your bank (first-time consent)

The authorisation flow needs a browser and MitID, so it can't run inside the add-on:

1. Add the automation below (adjust the webhook id to match your `redirect_url`).
2. On a device on your tailnet, open the URL printed by the add-on's `/connect` step (see
   below) — this is a manual, one-off action; there's no button for it yet in the Ingress UI.
3. Complete the bank's MitID login. The bank redirects to your webhook with `?code=...&state=...`.
4. The automation below forwards those to the add-on's `/callback` endpoint, which exchanges the
   code for a session and stores your linked account(s).
5. Repeat every ~90–180 days when consent expires — watch
   `binary_sensor.bank_consent_expiring`.

```yaml
# automations.yaml
- alias: Bank Summary — OAuth callback
  trigger:
    - platform: webhook
      webhook_id: "<the-random-id-from-your-redirect_url>"
      allowed_methods: [GET]
      local_only: false
  action:
    - service: rest_command.bank_summary_callback
      data:
        code: "{{ trigger.query.code }}"
        state: "{{ trigger.query.state }}"

# configuration.yaml
rest_command:
  bank_summary_callback:
    url: "http://<add-on-hostname>:8099/callback"
    method: POST
    content_type: "application/json"
    payload: '{"code": "{{ code }}", "state": "{{ state }}"}'
```

The Tailscale add-on must be running with `Serve` enabled so `https://<tailnet-host>.ts.net`
reaches HA Core on port 443. `local_only: false` is required because Tailscale traffic doesn't
look "local" to HA, and `GET` must be explicitly allowed since webhooks default to POST-only.

## Sensors

| Entity | Notes |
|---|---|
| `sensor.bank_income_this_month` | `device_class: monetary` |
| `sensor.bank_expenditure_this_month` | " |
| `sensor.bank_net_this_month` | " |
| `sensor.bank_categories_this_month` | state = top category; attributes carry the full breakdown |
| `sensor.bank_balance_<account>` | latest booked balance, when available |
| `sensor.bank_last_sync` | `device_class: timestamp` |
| `sensor.bank_consent_expires` | `device_class: timestamp` |
| `binary_sensor.bank_consent_expiring` | `on` within `consent_expiring_soon_days` of expiry |
| `sensor.bank_uncategorised_count` | nudge to extend `rules.yaml` |

Example Lovelace card:

```yaml
type: entities
title: Bank Summary
entities:
  - sensor.bank_income_this_month
  - sensor.bank_expenditure_this_month
  - sensor.bank_net_this_month
  - sensor.bank_uncategorised_count
  - sensor.bank_consent_expires
```

## Categorisation rules

`/addon_configs/bank_summary/rules.yaml` is seeded from a starter rule set on first run and is
yours to edit — no restart needed, rules are re-read on the next sync. Rules are evaluated
top-to-bottom, first match wins:

```yaml
- category: Groceries
  match: { counterparty_regex: "(?i)netto|rema 1000|f[øo]tex|bilka|lidl|coop|meny" }
- category: Salary
  match: { credit_debit_indicator: CRDT, counterparty_regex: "(?i)your-employer" }
```

The monthly report lists uncategorised transactions with a ready-to-paste rule stub for each.

## Reports

Markdown reports land in `/data/reports/YYYY-MM.md` (inside the add-on's persistent storage) and
are listed on the add-on's **Web UI** (Ingress) page.

## Data & backups

`/addon_configs/bank_summary/` (your `.pem` and `rules.yaml`) and the add-on's `/data` (the
SQLite database and reports) are both included in Home Assistant backups by default — worth
knowing if backups sync off-device, since the private key travels with them.

## Troubleshooting

- **No sensors after install**: sensors only publish once you've linked an account (see
  "Linking your bank" above) and a sync has run.
- **`binary_sensor.bank_consent_expiring` stuck `on`**: re-run the linking flow to refresh
  consent.
- **Webhook never fires**: confirm Tailscale Serve is active and the automation's
  `local_only: false` / `allowed_methods: [GET]` are set — these are off by default.
