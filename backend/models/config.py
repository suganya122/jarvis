"""Provider configuration. Model ids live here as config defaults, never in code paths."""

from typing import Literal

from pydantic import BaseModel, Field, HttpUrl

from contracts.model import ModelTier

Effort = Literal["low", "medium", "high", "xhigh", "max"]


class TierConfig(BaseModel):
    model: str
    effort: Effort
    max_tokens: int = Field(ge=256, le=128_000)
    refusal_fallback: bool = False
    """Use the API's server-side fallback when a safety classifier declines (not all models)."""


class Price(BaseModel):
    """USD per million tokens."""

    input: float
    output: float
    cache_read: float
    cache_write: float
    long_context_threshold: int | None = None
    """Prompts above this many input tokens bill at the long_* rates."""
    long_input: float | None = None
    long_output: float | None = None


def default_tiers() -> dict[ModelTier, TierConfig]:
    return {
        ModelTier.CHEAP: TierConfig(model="claude-haiku-5-5", effort="low", max_tokens=8_000),
        ModelTier.BALANCED: TierConfig(
            model="claude-sonnet-5-5", effort="medium", max_tokens=16_000, refusal_fallback=True
        ),
        ModelTier.MAX: TierConfig(
            model="claude-opus-5-5", effort="high", max_tokens=32_000, refusal_fallback=True
        ),
    }


def default_prices() -> dict[str, Price]:
    # Anthropic list prices (Oct 2026). Cache writes are 1.25x input (5-minute TTL).
    return {
        "claude-haiku-5-5": Price(
            input=0.10,
            output=0.50,
            cache_read=0.01,
            cache_write=0.125,
            long_context_threshold=100_000,
            long_input=0.50,
            long_output=2.50,
        ),
        "claude-sonnet-5-5": Price(input=2.00, output=10.00, cache_read=0.20, cache_write=2.50),
        "claude-opus-5-5": Price(input=4.00, output=20.00, cache_read=0.20, cache_write=5.00),
    }


class AnthropicConfig(BaseModel):
    tiers: dict[ModelTier, TierConfig] = Field(default_factory=default_tiers)
    prices: dict[str, Price] = Field(default_factory=default_prices)
    timeout_s: float = Field(default=120.0, gt=0)
    max_retries: int = Field(default=2, ge=0, le=5)


class LocalConfig(BaseModel):
    """Any OpenAI-compatible local server: Ollama, LM Studio, llama.cpp, vLLM."""

    base_url: HttpUrl = HttpUrl("http://127.0.0.1:11434/v1")
    model: str = "llama3.2"
    max_tokens: int = Field(default=4_096, ge=256)
    timeout_s: float = Field(default=120.0, gt=0)
