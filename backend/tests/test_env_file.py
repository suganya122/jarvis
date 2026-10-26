"""The .env file: found, read without leaking, private, and winning over the keychain;
and API errors that say what to fix."""

import os
import stat
from pathlib import Path

import anthropic
import httpx
import pytest

from core import env_file
from core.secret_store import MemoryBackend, SecretStore
from models.anthropic_provider import _error_text


def test_template_is_private_and_never_overwrites(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    env_file.create_template(path)
    if os.name == "posix":  # Windows has no permission bits; the profile folder is private
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert env_file.load(path) == {}  # the empty key line isn't a key
    path.write_text("ANTHROPIC_API_KEY=sk-ant-real\n")
    with pytest.raises(FileExistsError):
        env_file.create_template(path)
    assert "sk-ant-real" in path.read_text()


def test_reads_the_key_with_quotes_and_spaces_and_wins_over_keychain(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text('# my key\nANTHROPIC_API_KEY = "  sk-ant-abc  "\nOTHER=\n')
    path.chmod(0o600)
    values = env_file.load(path)
    assert values == {"ANTHROPIC_API_KEY": "sk-ant-abc"}
    backend = MemoryBackend()
    backend.set("anthropic_api_key", "old-keychain-key")
    store = SecretStore(backend, env={**os.environ, **values})
    assert store.anthropic_key() == "sk-ant-abc"


@pytest.mark.skipif(os.name != "posix", reason="POSIX permissions")
def test_a_world_readable_file_is_made_private(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text("ANTHROPIC_API_KEY=sk-ant-abc\n")
    path.chmod(0o644)
    env_file.load(path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_explicit_path_and_data_dir_lookup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    other = tmp_path / "elsewhere.env"
    other.write_text("ANTHROPIC_API_KEY=x\n")
    monkeypatch.setenv("ASSISTANT_ENV_FILE", str(other))
    assert env_file.find_env_file(tmp_path) == other
    monkeypatch.setenv("ASSISTANT_ENV_FILE", str(tmp_path / "missing.env"))
    assert env_file.find_env_file(tmp_path) is None
    monkeypatch.delenv("ASSISTANT_ENV_FILE")
    monkeypatch.setattr(env_file, "CHECKOUT_ROOT", tmp_path / "no-checkout")
    (tmp_path / ".env").write_text("A=1\n")
    assert env_file.find_env_file(tmp_path) == tmp_path / ".env"


def _status_error(status: int, body: object) -> anthropic.APIStatusError:
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(status, request=request)
    return anthropic.APIStatusError("x", response=response, body=body)


def test_api_errors_say_what_to_fix() -> None:
    low = {
        "type": "error",
        "error": {
            "type": "invalid_request_error",
            "message": "Your credit balance is too low to access the Anthropic API.",
        },
    }
    assert "no credit left" in _error_text(_status_error(400, low))
    bad = {"error": {"message": "model: claude-x\nnot found"}}
    assert _error_text(_status_error(400, bad)) == "Anthropic API error 400: model: claude-x not found"
    assert _error_text(_status_error(400, None)) == "Anthropic API error 400."
