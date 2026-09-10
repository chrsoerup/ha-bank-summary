#!/usr/bin/with-contenv bashio
set -e

export BANK_SUMMARY_APPLICATION_ID
export BANK_SUMMARY_PRIVATE_KEY_PATH
export BANK_SUMMARY_ASPSP_NAME
export BANK_SUMMARY_ASPSP_COUNTRY
export BANK_SUMMARY_ENVIRONMENT
export BANK_SUMMARY_REDIRECT_URL
export BANK_SUMMARY_ACCOUNT_UID
export BANK_SUMMARY_CURRENCY
export BANK_SUMMARY_SYNC_INTERVAL_HOURS
export BANK_SUMMARY_CONSENT_EXPIRING_SOON_DAYS
export BANK_SUMMARY_DATA_DIR=/data
export BANK_SUMMARY_RULES_PATH=/addon_config/rules.yaml

BANK_SUMMARY_APPLICATION_ID=$(bashio::config 'application_id')
BANK_SUMMARY_ASPSP_NAME=$(bashio::config 'aspsp_name')
BANK_SUMMARY_ASPSP_COUNTRY=$(bashio::config 'aspsp_country')
BANK_SUMMARY_ENVIRONMENT=$(bashio::config 'environment')
BANK_SUMMARY_REDIRECT_URL=$(bashio::config 'redirect_url')
BANK_SUMMARY_ACCOUNT_UID=$(bashio::config 'account_uid')
BANK_SUMMARY_CURRENCY=$(bashio::config 'currency')
BANK_SUMMARY_SYNC_INTERVAL_HOURS=$(bashio::config 'sync_interval_hours')
BANK_SUMMARY_CONSENT_EXPIRING_SOON_DAYS=$(bashio::config 'consent_expiring_soon_days')

private_key_path=$(bashio::config 'private_key_path')
if [ -n "${private_key_path}" ]; then
    BANK_SUMMARY_PRIVATE_KEY_PATH="/addon_config/${private_key_path}"
fi

mkdir -p /addon_config
if [ ! -f /addon_config/rules.yaml ]; then
    bashio::log.info "Seeding /addon_config/rules.yaml from the default rule set"
    cp /app/src/bank_summary/rules.default.yaml /addon_config/rules.yaml
fi

bashio::log.info "Starting Bank Summary on port 8099"
exec python3 -m uvicorn bank_summary.web:app --host 0.0.0.0 --port 8099
