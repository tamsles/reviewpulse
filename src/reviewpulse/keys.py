"""Pseudonymous user keys: user_key = HMAC-SHA256(RP_USER_KEY, user_id) (spec 04, REL-01).

The secret is read from the environment only; it is never written to code,
configs, manifests or logs.
"""

from __future__ import annotations

import hashlib
import hmac
import os

USER_KEY_ENV = "RP_USER_KEY"
KEY_ID_MESSAGE = b"reviewpulse/key-id/v1"
MIN_SECRET_LEN = 16


class UserKeyError(RuntimeError):
    """Raised when the user-key secret is missing or too weak."""


def load_secret(env: str = USER_KEY_ENV) -> bytes:
    value = os.environ.get(env)
    if not value:
        raise UserKeyError(f"environment variable {env} is not set")
    if len(value) < MIN_SECRET_LEN:
        raise UserKeyError(f"{env} must be at least {MIN_SECRET_LEN} characters")
    return value.encode("utf-8")


class UserKeyer:
    def __init__(self, secret: bytes):
        self._secret = secret

    @classmethod
    def from_env(cls, env: str = USER_KEY_ENV) -> UserKeyer:
        return cls(load_secret(env))

    def __call__(self, user_id: str) -> str:
        return hmac.new(self._secret, user_id.encode("utf-8"), hashlib.sha256).hexdigest()

    @property
    def key_id(self) -> str:
        """Fingerprint used to detect a wrong key at load time (first 16 hex chars)."""
        return hmac.new(self._secret, KEY_ID_MESSAGE, hashlib.sha256).hexdigest()[:16]

    def __repr__(self) -> str:  # never show the secret
        return f"UserKeyer(key_id={self.key_id})"
