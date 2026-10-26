"""Setup diagnostics for window control, screenshots and highlights (read-only)."""

import asyncio
import os
import shutil
import subprocess

from contracts.common import RiskLevel
from contracts.tools import ToolAssessment, ToolResult
from workers.hands import capture, uinput
from workers.hands.overlay import unavailable_reason
from workers.hands.tools import HandsEnv, NoArgs, WorkerTool


def _gsetting_accessibility() -> bool | None:
    if shutil.which("gsettings") is None:
        return None
    try:
        out = subprocess.run(
            ["gsettings", "get", "org.gnome.desktop.interface", "toolkit-accessibility"],  # noqa: S607
            capture_output=True, text=True, timeout=3, check=True,
        ).stdout.strip()  # fmt: skip
    except (OSError, subprocess.SubprocessError):
        return None
    return out == "true"


class AccessibilityStatus(WorkerTool[NoArgs]):
    name = "accessibility_status"
    description = "Check whether window and control access is set up on this computer, and how to fix it."
    args_model = NoArgs
    risk = RiskLevel.READ
    capabilities = ()

    def assess(self, args: NoArgs, env: HandsEnv) -> ToolAssessment:
        return ToolAssessment(risk=self.risk, summary="Check accessibility setup")

    async def run(self, args: NoArgs, env: HandsEnv) -> ToolResult:
        evidence: dict[str, object] = {"session": os.environ.get("XDG_SESSION_TYPE", "unknown")}
        lines = [f"Desktop session: {evidence['session']}"]
        try:
            windows = await asyncio.to_thread(env.hands.list_windows)
            evidence["accessibility_bus"] = True
            evidence["windows"] = len(windows)
            lines.append(f"Accessibility bus: working ({len(windows)} windows visible)")
        except Exception as exc:
            evidence["accessibility_bus"] = False
            lines.append(f"Accessibility bus: not available ({exc})")
        toolkit = await asyncio.to_thread(_gsetting_accessibility)
        evidence["toolkit_accessibility"] = toolkit
        if toolkit is False:
            lines.append(
                "GNOME toolkit accessibility is off: GTK apps still work, but some browsers may hide "
                "their controls. To turn it on, run: "
                "gsettings set org.gnome.desktop.interface toolkit-accessibility true"
            )
        session = capture.session_kind()
        evidence["capture"] = session
        if session == "wayland":
            lines.append(
                "Screenshots: through the desktop's screenshot portal. 'Pick a region' always works; "
                "silent full-screen captures need a one-time GNOME permission (the app explains how)."
            )
        elif session == "none":
            lines.append("Screenshots: no display found")
        else:
            lines.append(f"Screenshots: available ({session})")
        highlight = unavailable_reason()
        evidence["on_screen_highlight"] = highlight is None
        lines.append(
            "On-screen highlights: available" if highlight is None else f"On-screen highlights: {highlight}"
        )
        problem = uinput.available()
        evidence["key_input"] = problem is None
        lines.append("Key presses and scrolling: available" if problem is None else f"Key presses: {problem}")
        return ToolResult(ok=bool(evidence["accessibility_bus"]), output="\n".join(lines), evidence=evidence)
