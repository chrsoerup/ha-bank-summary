from __future__ import annotations

from pathlib import Path

import jwt
from freezegun import freeze_time

from bank_summary.enablebanking.auth import AUDIENCE, ISSUER, build_jwt, load_private_key


def test_jwt_claims_and_header(rsa_private_key_path: Path) -> None:
    private_key = load_private_key(rsa_private_key_path)

    with freeze_time("2026-01-15 12:00:00"):
        token = build_jwt("app-123", private_key)

    header = jwt.get_unverified_header(token)
    assert header["kid"] == "app-123"
    assert header["alg"] == "RS256"

    claims = jwt.decode(token, options={"verify_signature": False})
    assert claims["iss"] == ISSUER
    assert claims["aud"] == AUDIENCE
    assert claims["exp"] - claims["iat"] == 3600


def test_jwt_is_verifiable_with_matching_public_key(rsa_private_key_path: Path) -> None:
    private_key = load_private_key(rsa_private_key_path)
    token = build_jwt("app-123", private_key)

    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    key_obj = load_pem_private_key(private_key.encode(), password=None)
    public_key = key_obj.public_key()

    claims = jwt.decode(token, public_key, algorithms=["RS256"], audience="api.enablebanking.com")
    assert claims["iss"] == "enablebanking.com"
