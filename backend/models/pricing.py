"""Cost estimation from token usage."""

from contracts.model import Usage
from models.config import Price

_PER_TOKEN = 1 / 1_000_000


def estimate_cost(usage: Usage, price: Price | None) -> float:
    """Estimated USD for one request. Unknown models cost 0 and are flagged by the caller."""
    if price is None:
        return 0.0
    prompt_tokens = usage.input_tokens + usage.cache_read_tokens + usage.cache_write_tokens
    input_rate, output_rate = price.input, price.output
    if (
        price.long_context_threshold is not None
        and prompt_tokens > price.long_context_threshold
        and price.long_input is not None
        and price.long_output is not None
    ):
        input_rate, output_rate = price.long_input, price.long_output
    total = (
        usage.input_tokens * input_rate
        + usage.output_tokens * output_rate
        + usage.cache_read_tokens * price.cache_read
        + usage.cache_write_tokens * price.cache_write
    )
    return round(total * _PER_TOKEN, 8)
