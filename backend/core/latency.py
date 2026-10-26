"""Latency budgets. Tests fail when a change pushes a stage over its budget."""

ROUTER_MATCH_BUDGET_MS = 5.0
"""Rule matching (p95), measured without tool verification."""

INSTANT_FIRST_ACTION_BUDGET_MS = 1000.0
"""From message received to the instant command's action finishing (the brief's target)."""

VOICE_RELEASE_TO_FINAL_BUDGET_MS = 300.0
"""From releasing the talk key to the final transcript (the brief's target)."""
