"""Local encrypted broker credential storage."""

from __future__ import annotations

import base64
import json
import os
import stat
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from ytm_executor.guards import reject_secret_fields
from ytm_executor.state import DEFAULT_HOME

DEFAULT_KEY_FILE = DEFAULT_HOME / "master.key"
DEFAULT_SECRETS_FILE = DEFAULT_HOME / "secrets.json"


@dataclass(frozen=True, slots=True)
class CredentialSummary:
    provider: str
    name: str


class LocalSecretStore:
    """AES-GCM local file store.

    The master key is generated and kept on the executor host. This is a local containment layer,
    not a replacement for OS keychain, Vault, or user-owned cloud secret managers.
    """

    def __init__(
        self,
        *,
        key_file: Path = DEFAULT_KEY_FILE,
        secrets_file: Path = DEFAULT_SECRETS_FILE,
    ) -> None:
        self._key_file = key_file
        self._secrets_file = secrets_file

    def put(self, *, provider: str, name: str, secret: dict[str, str]) -> None:
        normalized_provider = _required_text(provider, "provider")
        normalized_name = _required_text(name, "name")
        if not secret:
            raise ValueError("secret must not be empty")
        payload = _load_records(self._secrets_file)
        records = [
            item
            for item in payload
            if not _same_record(item, normalized_provider, normalized_name)
        ]
        encrypted_secret = self._encrypt(secret)
        records.append(
            {
                "ciphertext": encrypted_secret["ciphertext"],
                "name": normalized_name,
                "nonce": encrypted_secret["nonce"],
                "provider": normalized_provider,
            }
        )
        _write_json_private(self._secrets_file, records)

    def list(self) -> tuple[CredentialSummary, ...]:
        return tuple(
            CredentialSummary(
                provider=_required_text(item.get("provider"), "provider"),
                name=_required_text(item.get("name"), "name"),
            )
            for item in _load_records(self._secrets_file)
        )

    def get(self, *, provider: str, name: str) -> dict[str, str]:
        normalized_provider = _required_text(provider, "provider")
        normalized_name = _required_text(name, "name")
        for item in _load_records(self._secrets_file):
            if _same_record(item, normalized_provider, normalized_name):
                return self._decrypt(item)
        credential_id = f"{normalized_provider}/{normalized_name}"
        raise ValueError(f"broker credential is not configured: {credential_id}")

    def heartbeat_capability(
        self,
        *,
        validation_summaries: Iterable[Mapping[str, Any]] = (),
    ) -> dict[str, Any]:
        capability: dict[str, Any] = {
            "localCredentials": [
                {"name": item.name, "provider": item.provider}
                for item in sorted(self.list(), key=lambda value: (value.provider, value.name))
            ]
        }
        validations = [dict(item) for item in validation_summaries]
        if validations:
            capability["brokerCredentialValidations"] = sorted(
                validations,
                key=lambda value: (str(value.get("provider", "")), str(value.get("name", ""))),
            )
        reject_secret_fields(capability)
        return capability

    def _encrypt(self, secret: dict[str, str]) -> dict[str, str]:
        key = self._load_or_create_key()
        nonce = os.urandom(12)
        raw = json.dumps(secret, separators=(",", ":"), sort_keys=True).encode("utf-8")
        ciphertext = AESGCM(key).encrypt(nonce, raw, b"ytm-executor-local-secret-v1")
        return {
            "ciphertext": base64.urlsafe_b64encode(ciphertext).decode("ascii"),
            "nonce": base64.urlsafe_b64encode(nonce).decode("ascii"),
        }

    def _decrypt(self, item: dict[str, Any]) -> dict[str, str]:
        key = self._load_or_create_key()
        ciphertext = base64.urlsafe_b64decode(_required_text(item.get("ciphertext"), "ciphertext"))
        nonce = base64.urlsafe_b64decode(_required_text(item.get("nonce"), "nonce"))
        raw = AESGCM(key).decrypt(nonce, ciphertext, b"ytm-executor-local-secret-v1")
        parsed = json.loads(raw.decode("utf-8"))
        if not isinstance(parsed, dict):
            raise ValueError("stored broker credential must be an object")
        secret = {str(key): str(value) for key, value in parsed.items()}
        if not secret:
            raise ValueError("stored broker credential is empty")
        return secret

    def _load_or_create_key(self) -> bytes:
        if self._key_file.exists():
            return base64.urlsafe_b64decode(self._key_file.read_text(encoding="utf-8"))
        self._key_file.parent.mkdir(parents=True, exist_ok=True)
        key = AESGCM.generate_key(bit_length=256)
        self._key_file.write_text(base64.urlsafe_b64encode(key).decode("ascii"), encoding="utf-8")
        os.chmod(self._key_file, stat.S_IRUSR | stat.S_IWUSR)
        return key


def _load_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, list):
        raise ValueError("secrets file must contain a list")
    return [dict(item) for item in value if isinstance(item, dict)]


def _write_json_private(path: Path, value: object) -> None:
    reject_secret_fields(_public_metadata(value))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)


def _public_metadata(value: object) -> object:
    if isinstance(value, dict):
        return {key: _public_metadata(item) for key, item in value.items() if key != "ciphertext"}
    if isinstance(value, list):
        return [_public_metadata(item) for item in value]
    return value


def _same_record(item: dict[str, Any], provider: str, name: str) -> bool:
    return item.get("provider") == provider and item.get("name") == name


def _required_text(value: object, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} is required")
    return text
