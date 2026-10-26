"""Instant-command router: handles obvious requests with zero LLM calls."""

from core.config import RouterSettings
from core.router.rules import Route, match
from core.toolbox import ToolRegistry

__all__ = ["Route", "plan"]


async def plan(text: str, settings: RouterSettings, registry: ToolRegistry) -> Route | None:
    """A route that is safe to run instantly, or None to let the model handle it."""
    route = match(text, settings)
    if route is None or route.tool is None:
        return route
    tool = registry.get(route.tool)
    if tool is None:  # e.g. OS tools unavailable: the model can still explain
        return None
    if route.verify:
        assessment = await tool.assess(route.args or {})
        if assessment.error:
            return None
    return route
