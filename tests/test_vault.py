"""Tests for storage/vault.py using passphrase mode (no OS keyring)."""
from __future__ import annotations

import pytest

# All tests use vault_env fixture (conftest.py) which sets:
#   NIGGLESS_USE_PASSPHRASE=1
#   NIGGLESS_PASSPHRASE=test-passphrase
#   NIGGLESS_DATA_DIR=<tmp_path>

pytestmark = pytest.mark.usefixtures("vault_env")


def _init():
    """Initialise the vault for this test (idempotent helper)."""
    from storage.vault import init_vault
    init_vault()


# ---------------------------------------------------------------------------
# init_vault
# ---------------------------------------------------------------------------

class TestInitVault:
    def test_creates_vault_file(self, vault_env):
        _init()
        vault_file = vault_env / "vault.enc"
        assert vault_file.exists()

    def test_double_init_raises(self):
        _init()
        from storage.vault import init_vault
        with pytest.raises(FileExistsError):
            init_vault()


# ---------------------------------------------------------------------------
# set_secret / get_secret
# ---------------------------------------------------------------------------

class TestSetAndGetSecret:
    def test_set_and_retrieve(self):
        _init()
        from storage.vault import get_secret, set_secret
        set_secret("MY_KEY", "my-value")
        assert get_secret("MY_KEY") == "my-value"

    def test_get_nonexistent_returns_none(self):
        _init()
        from storage.vault import get_secret
        assert get_secret("DOES_NOT_EXIST") is None

    def test_overwrite_existing(self):
        _init()
        from storage.vault import get_secret, set_secret
        set_secret("KEY", "v1")
        set_secret("KEY", "v2")
        assert get_secret("KEY") == "v2"

    def test_multiple_secrets_independent(self):
        _init()
        from storage.vault import get_secret, set_secret
        set_secret("A", "alpha")
        set_secret("B", "beta")
        assert get_secret("A") == "alpha"
        assert get_secret("B") == "beta"

    def test_secret_with_special_characters(self):
        _init()
        from storage.vault import get_secret, set_secret
        value = "p@ss!w0rd*&^%$"
        set_secret("SPECIAL", value)
        assert get_secret("SPECIAL") == value


# ---------------------------------------------------------------------------
# delete_secret
# ---------------------------------------------------------------------------

class TestDeleteSecret:
    def test_delete_removes_key(self):
        _init()
        from storage.vault import delete_secret, get_secret, set_secret
        set_secret("TO_DELETE", "value")
        delete_secret("TO_DELETE")
        assert get_secret("TO_DELETE") is None

    def test_delete_nonexistent_does_not_raise(self):
        _init()
        from storage.vault import delete_secret
        delete_secret("NEVER_SET")  # should not raise

    def test_delete_only_removes_targeted_key(self):
        _init()
        from storage.vault import delete_secret, get_secret, set_secret
        set_secret("KEEP", "stay")
        set_secret("REMOVE", "gone")
        delete_secret("REMOVE")
        assert get_secret("KEEP") == "stay"
        assert get_secret("REMOVE") is None


# ---------------------------------------------------------------------------
# list_secret_keys
# ---------------------------------------------------------------------------

class TestListSecretKeys:
    def test_empty_after_init(self):
        _init()
        from storage.vault import list_secret_keys
        assert list_secret_keys() == []

    def test_lists_set_keys(self):
        _init()
        from storage.vault import list_secret_keys, set_secret
        set_secret("OPENAI_API_KEY", "sk-test")
        set_secret("GEMINI_API_KEY", "gem-test")
        keys = list_secret_keys()
        assert "OPENAI_API_KEY" in keys
        assert "GEMINI_API_KEY" in keys

    def test_deleted_key_not_listed(self):
        _init()
        from storage.vault import delete_secret, list_secret_keys, set_secret
        set_secret("TEMP", "value")
        delete_secret("TEMP")
        assert "TEMP" not in list_secret_keys()


# ---------------------------------------------------------------------------
# TOTP seeds
# ---------------------------------------------------------------------------

class TestTotpSeeds:
    def test_add_and_retrieve_totp_seed(self):
        _init()
        from storage.vault import add_totp_seed, get_totp_seed
        add_totp_seed("example.com", "JBSWY3DPEHPK3PXP")
        seed = get_totp_seed("example.com")
        assert seed == "JBSWY3DPEHPK3PXP"

    def test_get_nonexistent_totp_seed_returns_none(self):
        _init()
        from storage.vault import get_totp_seed
        assert get_totp_seed("no-such-host.com") is None
