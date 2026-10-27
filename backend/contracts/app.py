"""Product identity. The product name is a placeholder: change it here only."""

from typing import Final

APP_NAME: Final = "Assistant"
"""Human-facing product name shown in the UI and logs."""

DEFAULT_WAKE_WORD: Final = "jarvis"
"""What you say to start talking to it (a setting; this is the default)."""

APP_ID: Final = "local-assistant"
"""Machine identifier: app-data folder name and keychain service name."""

ENV_PREFIX: Final = "ASSISTANT_"
"""Prefix for every environment variable the app reads."""

APP_VERSION: Final = "0.1.16"
