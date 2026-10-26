import logging

import pytest

from core.redact import MASK, RedactingFilter, Redactor
from core.secret_store import KeychainUnavailable, MemoryBackend, SecretStore


def test_env_file_key_wins_over_the_keychain() -> None:
    store = SecretStore(MemoryBackend(), env={"ANTHROPIC_API_KEY": "env-key"})
    assert store.anthropic_key() == "env-key"
    assert store.anthropic_key_source() == "environment"
    store.set_anthropic_key("chain-key")
    assert store.anthropic_key() == "env-key"  # the .env you edit is the one that counts
    keychain_only = SecretStore(MemoryBackend(), env={})
    keychain_only.set_anthropic_key("chain-key")
    assert keychain_only.anthropic_key() == "chain-key"
    assert keychain_only.anthropic_key_source() == "keychain"


def test_keychain_failure_is_reported_not_raised() -> None:
    class Broken(MemoryBackend):
        def get(self, name: str) -> str | None:
            raise KeychainUnavailable("no backend")

    store = SecretStore(Broken(), env={})
    assert store.anthropic_key() is None
    assert store.keychain_error == "no backend"


@pytest.mark.parametrize(
    "text",
    [
        "key=sk-ant-api03-abcdefghijklmnop",
        "Authorization: Bearer abcdefghijklmnopqrstuvwxyz",
        "http://127.0.0.1:8765/#token=abcdefghijklmnopqrstuvwxyz",
    ],
)
def test_redactor_patterns(text: str) -> None:
    out = Redactor()(text)
    assert MASK in out
    assert "abcdefghijklmnop" not in out


def test_redactor_literal_and_log_filter() -> None:
    redactor = Redactor()
    redactor.add_secret("my-local-token-value")
    record = logging.LogRecord("x", logging.INFO, "f", 1, "token is %s", ("my-local-token-value",), None)
    RedactingFilter(redactor).filter(record)
    assert record.getMessage() == f"token is {MASK}"
