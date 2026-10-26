"""Selects the active model provider from settings."""

from contracts.model import ModelProvider
from core.config import Settings
from core.secret_store import SecretStore
from models.anthropic_provider import AnthropicProvider
from models.openai_compat import OpenAICompatProvider


def build_provider(settings: Settings, secrets: SecretStore) -> ModelProvider:
    if settings.provider == "local":
        return OpenAICompatProvider(settings.local)
    return AnthropicProvider(settings.anthropic, api_key=secrets.anthropic_key)
