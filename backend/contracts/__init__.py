"""Typed contracts shared by core, models, workers, and plugins.

This package is the single source of truth for every message and interface
that crosses a module or process boundary. It must not import from any other
first-party package.
"""

from contracts.app import APP_ID, APP_NAME, APP_VERSION, ENV_PREFIX
from contracts.common import CONTRACT_VERSION, Capability, ContractModel, RiskLevel, new_trace_id

__all__ = [
    "APP_ID",
    "APP_NAME",
    "APP_VERSION",
    "CONTRACT_VERSION",
    "ENV_PREFIX",
    "Capability",
    "ContractModel",
    "RiskLevel",
    "new_trace_id",
]
