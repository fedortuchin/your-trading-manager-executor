"""Guards that prevent broker secrets from entering YTM API payloads."""

from __future__ import annotations

from collections.abc import Iterable

FORBIDDEN_SECRET_KEYS = frozenset(
    {
        "apikey",
        "apisecret",
        "authorization",
        "bearer",
        "brokersecret",
        "clientsecret",
        "password",
        "passphrase",
        "privatekey",
        "secret",
        "secretkey",
        "token",
    }
)


class SecretFieldError(ValueError):
    """A payload contains a field that could carry a broker secret."""


def reject_secret_fields(value: object, *, field_path: Iterable[str] = ()) -> None:
    """Reject secret-like keys recursively.

    This guard prevents accidental egress of broker credentials to YTM Cloud. It checks keys, not
    values, because values can look like arbitrary opaque strings.
    """

    if isinstance(value, dict):
        for key, item in value.items():
            token = secret_key_token(str(key))
            next_path = (*field_path, str(key))
            if token in FORBIDDEN_SECRET_KEYS:
                path = ".".join(next_path)
                raise SecretFieldError(f"payload contains forbidden secret field: {path}")
            reject_secret_fields(item, field_path=next_path)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            reject_secret_fields(item, field_path=(*field_path, str(index)))


def secret_key_token(value: str) -> str:
    return "".join(ch for ch in value.lower() if ch.isalnum())
