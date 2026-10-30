"""Setup for window control, screenshots and highlights: what works, and turning permissions on."""

import asyncio
import os
import shutil
import subprocess
import sys
from typing import Literal

from pydantic import Field

from contracts.common import RiskLevel
from contracts.tools import ToolAssessment, ToolResult
from workers.common.base import Args
from workers.hands import capture, uinput
from workers.hands.overlay import unavailable_reason
from workers.hands.tools import HandsEnv, NoArgs, WorkerTool

MAC_BUNDLE_ID = "local.assistant.jarvis"
"""Jarvis.app's identity to macOS (install.sh writes it into Info.plist)."""
PANES = {
    "accessibility": "Privacy_Accessibility",
    "screen_recording": "Privacy_ScreenCapture",
    "microphone": "Privacy_Microphone",
    "speech_recognition": "Privacy_SpeechRecognition",
    "automation": "Privacy_Automation",
}
Permission = Literal["accessibility", "screen_recording", "microphone", "speech_recognition", "automation"]


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
        if sys.platform == "darwin":
            return await asyncio.to_thread(_mac_status, env)
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


def _mac_status(env: HandsEnv) -> ToolResult:
    """macOS: one permission covers windows, controls, keys and clicks: Accessibility for Jarvis."""
    try:
        trusted = bool(getattr(env.hands, "trusted", lambda: False)())
    except Exception as exc:
        return ToolResult(ok=False, output=f"Window and app control: not available ({exc})", evidence={})
    if not trusted:
        output = (
            "Window and app control: not allowed. In System Settings → Privacy & Security → "
            "Accessibility: if Jarvis is listed, even switched on (an update makes it a new app to "
            "macOS), select it, remove it with the minus button, add it again with + "
            "(Applications in your home folder → Jarvis), then reopen Jarvis. If it isn't listed, "
            "add it with +. Screenshots "
            "also need Screen Recording for Jarvis."
        )
        return ToolResult(ok=False, output=output, evidence={"accessibility": False})
    windows = env.hands.list_windows()
    output = f"Window and app control: working ({len(windows)} windows visible); keys, clicks, menus too."
    return ToolResult(ok=True, output=output, evidence={"accessibility": True, "windows": len(windows)})


class PermissionArgs(Args):
    permission: Permission = Field(default="accessibility", description="Which permission to set up.")


def _run(argv: list[str]) -> bool:
    try:
        return subprocess.run(argv, capture_output=True, timeout=10, check=False).returncode == 0  # noqa: S603
    except (OSError, subprocess.SubprocessError):
        return False


class SetupPermission(WorkerTool[PermissionArgs]):
    name = "setup_permission"
    description = (
        "When the user asks you to turn on a permission yourself (Accessibility, Screen Recording, "
        "microphone, speech recognition, Automation): do every step the system allows. On a Mac that "
        "clears a stale entry, shows macOS's own prompt and opens the exact settings page; the final "
        "switch (with their password or Touch ID) is the one step macOS keeps for the user."
    )
    args_model = PermissionArgs
    risk = RiskLevel.WRITE
    capabilities = ()

    def assess(self, args: PermissionArgs, env: HandsEnv) -> ToolAssessment:
        return ToolAssessment(
            risk=self.risk, summary=f"Set up the {args.permission.replace('_', ' ')} permission"
        )

    async def run(self, args: PermissionArgs, env: HandsEnv) -> ToolResult:
        if sys.platform == "darwin":
            return await asyncio.to_thread(_mac_setup, env, args.permission)
        if sys.platform.startswith("linux") and args.permission == "accessibility":
            done = await asyncio.to_thread(
                _run, ["gsettings", "set", "org.gnome.desktop.interface", "toolkit-accessibility", "true"]
            )
            if done:
                return ToolResult(
                    ok=True, output="Turned on desktop accessibility. Apps opened from now on can be read."
                )
            return ToolResult(ok=True, output="Accessibility needs nothing to be turned on on this desktop.")
        return ToolResult(ok=True, output="Nothing needs turning on for that on this computer.")


def _mac_setup(env: HandsEnv, permission: str) -> ToolResult:
    """Everything macOS lets an app do about its own permission, short of switching it on."""
    done: list[str] = []
    if permission == "accessibility":
        trusted = bool(getattr(env.hands, "trusted", lambda: False)())
        if trusted:
            return ToolResult(
                ok=True, output="Accessibility is already on for Jarvis: windows, clicks and keys work."
            )
        if _run(["tccutil", "reset", "Accessibility", MAC_BUNDLE_ID]):
            done.append(
                "cleared the old Accessibility entry (it stays on but stops applying after an update)"
            )
        ask = getattr(env.hands, "ask_trust", None)
        if ask is not None:
            ask()
            done.append("asked macOS to show its prompt")
    url = f"x-apple.systempreferences:com.apple.preference.security?{PANES[permission]}"
    if _run(["open", url]):
        done.append("opened that page in System Settings")
    what = permission.replace("_", " ").title()
    steps = "; ".join(done) or "couldn't open System Settings"
    return ToolResult(
        ok=bool(done),
        output=(
            f"{what}: I {steps}. The last step is yours: switch Jarvis on there (macOS asks for your "
            "password or Touch ID, and doesn't let any app do that for itself). If Jarvis isn't in the "
            "list, add it with + (Applications in your home folder → Jarvis)."
        ),
        evidence={"steps": done},
    )
