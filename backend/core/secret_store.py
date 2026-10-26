"""API key storage in the OS keychain. Never written to config files or logs."""

import os
from typing import Literal, Protocol

import keyring
import keyring.errors

from contracts import APP_ID

ANTHROPIC_KEY_NAME = "anthropic_api_key"
KeySource = Literal["keychain", "environment", "none"]


class KeychainUnavailable(RuntimeError):
    pass


class SecretBackend(Protocol):
    def get(self, name: str) -> str | None: ...

    def set(self, name: str, value: str) -> None: ...

    def delete(self, name: str) -> None: ...


class KeyringBackend:
    def get(self, name: str) -> str | None:
        try:
            return keyring.get_password(APP_ID, name)
        except keyring.errors.KeyringError as exc:
            raise KeychainUnavailable(str(exc)) from exc

    def set(self, name: str, value: str) -> None:
        try:
            keyring.set_password(APP_ID, name, value)
        except keyring.errors.KeyringError as exc:
            raise KeychainUnavailable(str(exc)) from exc

    def delete(self, name: str) -> None:
        try:
            keyring.delete_password(APP_ID, name)
        except keyring.errors.PasswordDeleteError:
            pass
        except keyring.errors.KeyringError as exc:
            raise KeychainUnavailable(str(exc)) from exc


class MemoryBackend:
    """For tests."""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, name: str) -> str | None:
        return self.values.get(name)

    def set(self, name: str, value: str) -> None:
        self.values[name] = value

    def delete(self, name: str) -> None:
        self.values.pop(name, None)


class SecretStore:
    """ANTHROPIC_API_KEY (from the .env file or the environment) first, then the OS keychain."""

    def __init__(self, backend: SecretBackend, env: dict[str, str] | None = None) -> None:
        self._backend = backend
        self._env = env if env is not None else dict(os.environ)
        self._cached: str | None = None
        self._loaded = False
        self.keychain_error: str | None = None

    def anthropic_key(self) -> str | None:
        if not self._loaded:
            try:
                self._cached = self._backend.get(ANTHROPIC_KEY_NAME)
                self.keychain_error = None
            except KeychainUnavailable as exc:
                self._cached = None
                self.keychain_error = str(exc)
            self._loaded = True
        return self._env.get("ANTHROPIC_API_KEY") or self._cached or None

    def anthropic_key_source(self) -> KeySource:
        self.anthropic_key()
        if self._env.get("ANTHROPIC_API_KEY"):
            return "environment"
        return "keychain" if self._cached else "none"

    def set_anthropic_key(self, value: str) -> None:
        self._backend.set(ANTHROPIC_KEY_NAME, value)
        self._cached, self._loaded = value, True

    def delete_anthropic_key(self) -> None:
        self._backend.delete(ANTHROPIC_KEY_NAME)
        self._cached, self._loaded = None, True

    # Plugin secrets (e.g. a chat bot's token), one keychain entry each.

    @staticmethod
    def plugin_key(plugin: str, secret: str) -> str:
        return f"plugin.{plugin}.{secret}"

    def get_plugin_secret(self, plugin: str, secret: str) -> str | None:
        return self._backend.get(self.plugin_key(plugin, secret))

    def set_plugin_secret(self, plugin: str, secret: str, value: str) -> None:
        self._backend.set(self.plugin_key(plugin, secret), value)

    def delete_plugin_secret(self, plugin: str, secret: str) -> None:
        self._backend.delete(self.plugin_key(plugin, secret))
