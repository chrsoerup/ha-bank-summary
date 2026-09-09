"""Pydantic models for the Enable Banking API.

Field names follow Enable Banking's documented (Berlin-Group-derived) schema. `extra="allow"` on
every model tolerates fields the bank includes that aren't modeled here — better to keep them in
`raw_json` than to fail parsing. Verify against real sandbox/production responses in M1/M4 and
tighten as needed (see plan's "Open items" section).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class EBModel(BaseModel):
    model_config = ConfigDict(extra="allow")


class Aspsp(EBModel):
    name: str
    country: str
    logo: str | None = None
    sandbox: bool = False
    maximum_consent_validity: int | None = None  # seconds
    psu_types: list[str] = []


class StartAuthorizationResponse(EBModel):
    url: str


class Amount(EBModel):
    amount: str
    currency: str


class Party(EBModel):
    name: str | None = None


class AccountRef(EBModel):
    uid: str
    identification_hash: str | None = None
    iban: str | None = None
    name: str | None = None
    currency: str | None = None


class Session(EBModel):
    session_id: str
    status: str
    accounts: list[AccountRef] = []
    aspsp: Aspsp | None = None
    valid_until: str | None = None


class Balance(EBModel):
    balance_amount: Amount
    balance_type: str
    reference_date: str | None = None


class Transaction(EBModel):
    entry_reference: str | None = None
    transaction_id: str | None = None
    booking_date: str | None = None
    value_date: str | None = None
    transaction_amount: Amount
    credit_debit_indicator: str  # "CRDT" or "DBIT"
    status: str  # "BOOK" or "PDNG"
    bank_transaction_code: str | None = None
    merchant_category_code: str | None = None
    creditor: Party | None = None
    debtor: Party | None = None
    remittance_information_unstructured: str | None = None
    remittance_information_unstructured_array: list[str] | None = None

    @property
    def counterparty_name(self) -> str | None:
        """The other party to the transaction: the creditor when money leaves this account
        (DBIT — we are the debtor), the debtor when money arrives (CRDT — we are the creditor)."""
        party = self.creditor if self.credit_debit_indicator == "DBIT" else self.debtor
        return party.name if party else None

    @property
    def remittance_text(self) -> str | None:
        if self.remittance_information_unstructured_array:
            return " ".join(self.remittance_information_unstructured_array)
        return self.remittance_information_unstructured

    @property
    def signed_amount(self) -> str:
        amount = self.transaction_amount.amount
        if self.credit_debit_indicator == "DBIT" and not amount.startswith("-"):
            return f"-{amount}"
        return amount


class TransactionsPage(EBModel):
    transactions: list[Transaction] = []
    continuation_key: str | None = None
