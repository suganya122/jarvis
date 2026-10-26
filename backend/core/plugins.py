"""Finding plugins: first-party ones in backend/plugins/, the user's own in <data>/plugins/.

A plugin is a folder with plugin.json (contracts.plugin.PluginManifest) and a Python
package named after it. Plugins run as separate processes and start only when the user
enables them. They are not a sandbox: a plugin's code runs with the user's permissions,
which is why the UI says so and lists what the plugin asks for before enabling it.
"""

import logging
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from contracts import CONTRACT_VERSION
from contracts.plugin import PluginManifest

log = logging.getLogger(__name__)
FIRST_PARTY = Path(__file__).resolve().parents[1] / "plugins"
MANIFEST = "plugin.json"


@dataclass(frozen=True)
class Plugin:
    manifest: PluginManifest
    folder: Path
    first_party: bool

    @property
    def name(self) -> str:
        return self.manifest.name

    @property
    def pythonpath(self) -> Path:
        """Where `python -m <entrypoint>` finds the plugin's package."""
        return self.folder.parent


def compatible(version: str) -> bool:
    return version.split(".")[0] == CONTRACT_VERSION.split(".")[0]


def load(folder: Path, first_party: bool) -> Plugin | None:
    path = folder / MANIFEST
    try:
        manifest = PluginManifest.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError, ValueError) as exc:
        log.warning("ignoring plugin in %s: %s", folder, exc)
        return None
    if manifest.name != folder.name:
        log.warning("ignoring plugin in %s: its name must match the folder (%s)", folder, manifest.name)
        return None
    if not compatible(manifest.contract_version):
        log.warning("ignoring plugin %s: written for contracts %s", manifest.name, manifest.contract_version)
        return None
    if manifest.entrypoint.split(".")[0] != manifest.name:
        log.warning("ignoring plugin %s: its entrypoint must be inside its own package", manifest.name)
        return None
    return Plugin(manifest, folder, first_party)


def discover(user_dir: Path | None, first_party_dir: Path = FIRST_PARTY) -> dict[str, Plugin]:
    """First-party plugins win a name clash, so a user folder can't impersonate one."""
    found: dict[str, Plugin] = {}
    for base, first in ((first_party_dir, True), (user_dir, False)):
        if base is None or not base.is_dir():
            continue
        for folder in sorted(p for p in base.iterdir() if (p / MANIFEST).is_file()):
            plugin = load(folder, first)
            if plugin is not None and plugin.name not in found:
                found[plugin.name] = plugin
    return found
