"""Fernet-encrypted secrets vault backed by the OS keyring."""
from __future__ import annotations

import base64
import getpass
import hashlib
import json
import logging
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

log = logging.getLogger(__name__)

_SERVICE = "niggless-jobber"
_USERNAME = "vault-key"


def _data_dir() -> Path:
    path = Path(os.environ.get("NIGGLESS_DATA_DIR", "") or Path.home() / ".niggless-jobber")
    path.mkdir(parents=True, exist_ok=True)
    return path


def _vault_path() -> Path:
    return _data_dir() / "vault.enc"


# ---------------------------------------------------------------------------
# Key management
# ---------------------------------------------------------------------------

def _use_passphrase() -> bool:
    return os.environ.get("NIGGLESS_USE_PASSPHRASE", "0") == "1"


def _derive_key_from_passphrase(passphrase: str) -> bytes:
    salt = b"niggless-jobber-salt-v1"
    dk = hashlib.pbkdf2_hmac("sha256", passphrase.encode(), salt, 100_000)
    return base64.urlsafe_b64encode(dk)


def _get_fernet_key() -> bytes:
    if _use_passphrase():
        passphrase = os.environ.get("NIGGLESS_PASSPHRASE") or getpass.getpass("Vault passphrase: ")
        return _derive_key_from_passphrase(passphrase)

    try:
        import keyring
        key = keyring.get_password(_SERVICE, _USERNAME)
        if key:
            return key.encode()
    except Exception as exc:
        log.warning("OS keyring unavailable (%s); falling back to passphrase mode", exc)

    passphrase = os.environ.get("NIGGLESS_PASSPHRASE") or getpass.getpass("Vault passphrase: ")
    return _derive_key_from_passphrase(passphrase)


def _save_key_to_keyring(key: bytes) -> None:
    try:
        import keyring
        keyring.set_password(_SERVICE, _USERNAME, key.decode())
    except Exception as exc:
        log.warning("Could not save key to OS keyring: %s", exc)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def init_vault() -> None:
    """Generate a new Fernet key, store it in the OS keyring, create an empty vault."""
    if _vault_path().exists():
        raise FileExistsError(
            f"Vault already exists at {_vault_path()}. "
            "Delete it first if you want to re-initialise."
        )
    if _use_passphrase():
        # Derive a deterministic key from the passphrase so that subsequent
        # _read_secrets() / _write_secrets() calls use the same key.
        key = _get_fernet_key()
    else:
        key = Fernet.generate_key()
        _save_key_to_keyring(key)
    _write_secrets({}, key)
    print(f"Vault initialised at {_vault_path()}")


def _read_secrets(key: bytes | None = None) -> dict:
    path = _vault_path()
    if not path.exists():
        return {}
    if key is None:
        key = _get_fernet_key()
    f = Fernet(key)
    try:
        plaintext = f.decrypt(path.read_bytes())
    except InvalidToken as exc:
        raise ValueError("Wrong vault passphrase or corrupted vault.") from exc
    return json.loads(plaintext.decode())


def _write_secrets(secrets: dict, key: bytes | None = None) -> None:
    if key is None:
        key = _get_fernet_key()
    f = Fernet(key)
    plaintext = json.dumps(secrets, indent=2).encode()
    _vault_path().write_bytes(f.encrypt(plaintext))


def get_secret(name: str) -> str | None:
    """Return the value for *name* from the vault, or None if absent."""
    return _read_secrets().get(name)


def set_secret(name: str, value: str) -> None:
    """Write or update *name* in the vault."""
    secrets = _read_secrets()
    secrets[name] = value
    _write_secrets(secrets)


def delete_secret(name: str) -> None:
    secrets = _read_secrets()
    secrets.pop(name, None)
    _write_secrets(secrets)


def list_secret_keys() -> list[str]:
    return list(_read_secrets().keys())


def add_totp_seed(hostname: str, seed: str) -> None:
    secrets = _read_secrets()
    totp_seeds = secrets.get("totp_seeds", {})
    totp_seeds[hostname] = seed
    secrets["totp_seeds"] = totp_seeds
    _write_secrets(secrets)


def get_totp_seed(hostname: str) -> str | None:
    secrets = _read_secrets()
    return secrets.get("totp_seeds", {}).get(hostname)


def get_all_secrets() -> dict:
    """Return the full decrypted secrets dict (use sparingly)."""
    return _read_secrets()
