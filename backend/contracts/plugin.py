"""Plugin manifest contract: `plugin.json` in the plugin's folder."""

from typing import Literal

from pydantic import Field

from contracts.common import Capability, ContractModel


class PluginField(ContractModel):
    """A setting the user fills in (options) or a secret kept in the OS keychain (secrets)."""

    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,40}$")
    label: str
    help: str = ""
    advanced: bool = False
    """Hidden behind 'Advanced' in the UI (e.g. a test server URL)."""


class PluginManifest(ContractModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{1,40}$")
    display_name: str
    version: str
    description: str = ""
    contract_version: str
    """The contracts version the plugin was written for; the major version must match."""
    provides: tuple[Literal["channel"], ...]
    """Only channels for now; tools and model providers are reserved for later."""
    capabilities: tuple[Capability, ...] = ()
    """What the plugin itself needs (shown to the user before enabling)."""
    entrypoint: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_.]*$")
    """Module run as `python -m <entrypoint>`."""
    options: tuple[PluginField, ...] = ()
    secrets: tuple[PluginField, ...] = ()
