"""RS256 JWT construction for the Enable Banking API.

Enable Banking authenticates API calls with a JWT signed by the application's private key
(downloaded once from the control panel when the application is registered), sent as a bearer
token. The token is short-lived and built fresh per request (or per client instance) rather than
cached across restarts.
"""

from __future__ import annotations

import time
from pathlib import Path

import jwt

ISSUER = "enablebanking.com"
AUDIENCE = "api.enablebanking.com"
TOKEN_LIFETIME_SECONDS = 3600


def build_jwt(application_id: str, private_key: str) -> str:
    now = int(time.time())
    claims = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "iat": now,
        "exp": now + TOKEN_LIFETIME_SECONDS,
    }
    headers = {"kid": application_id}
    return jwt.encode(claims, private_key, algorithm="RS256", headers=headers)


def load_private_key(private_key_path: str | Path) -> str:
    return Path(private_key_path).read_text()
