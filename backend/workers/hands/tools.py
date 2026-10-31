"""Hands tools exposed to the model. All screen-derived output is untrusted."""

import asyncio
import sys
from typing import Any, ClassVar, Literal

from pydantic import Field

from contracts.common import MONEY, Capability, RiskLevel, Trust
from contracts.hands import ActionResult, ElementInfo
from contracts.tools import ToolAssessment, ToolResult
from workers.common import clipboard
from workers.common.base import Args, truncate
from workers.common.base import WorkerTool as _WorkerTool
from workers.hands import uinput
from workers.hands.env import MAX_IDS as MAX_IDS
from workers.hands.env import HandsEnv as HandsEnv
from workers.hands.env import HandsUnavailable as HandsUnavailable
from workers.hands.env import IdMap as IdMap
from workers.hands.keys import display, parse_combo
from workers.hands.locate import (
    ElementRef,
    WindowRef,
    element_meta,
    window_meta,
)
from workers.hands.risk import key_risk, label_risk, runs_commands

WORKER_NAME = "hands"


class WorkerTool[A: Args](_WorkerTool[A, HandsEnv]):
    source: ClassVar[str] = WORKER_NAME


def _element_line(env: HandsEnv, info: ElementInfo, window_label: str, window_meta_: dict[str, Any]) -> str:
    meta = element_meta(info.role, info.name, window_meta_)
    short = env.ids.add("e", info.element_id, f"{info.role} '{info.name}' in {window_label}", meta)
    parts = [f"{short}  {info.role} '{info.name}'"]
    if info.text:
        parts.append(f"text='{info.text[:120]}'")
    if info.role == "password text":
        parts.append("(password: hidden)")
    flags: list[str] = [s for s in ("checked", "focused", "selected", "expanded") if s in info.states]
    if "disabled" in info.states or (
        sys.platform != "darwin" and "sensitive" not in info.states and "enabled" not in info.states
    ):  # AT-SPI lists what's enabled; macOS says only what's disabled
        flags.append("disabled")
    if flags:
        parts.append(f"[{', '.join(flags)}]")
    return " ".join(parts)


def _report(verb: str, result: ActionResult, limit: int, target: dict[str, Any] | None = None) -> ToolResult:
    evidence = {**result.evidence, "target": target or {}}
    if not result.ok:
        return ToolResult(ok=False, output=f"{verb} failed: {result.error}", evidence=evidence)
    changes = result.evidence.get("changes")
    if changes:
        seen = "; ".join(changes)
        text = f"{verb}. Observed: {seen}."
    elif changes is not None:
        text = f"{verb}, but no visible change was detected."
    else:
        text = f"{verb}."
    return ToolResult(ok=True, output=truncate(text, limit), trust=Trust.UNTRUSTED, evidence=evidence)


class NoArgs(Args):
    pass


class ListWindows(WorkerTool[NoArgs]):
    name = "list_windows"
    description = "List open app windows with short ids (w1, w2, …) for the other window tools."
    args_model = NoArgs
    risk = RiskLevel.READ
    capabilities = (Capability.SCREEN_READ,)

    def assess(self, args: NoArgs, env: HandsEnv) -> ToolAssessment:
        return ToolAssessment(risk=self.risk, summary="See which windows are open")

    async def run(self, args: NoArgs, env: HandsEnv) -> ToolResult:
        windows = await asyncio.to_thread(env.hands.list_windows)
        lines = []
        for w in windows:
            label = f"{w.app} — {w.title}" if w.title else w.app
            short = env.ids.add("w", w.window_id, label, window_meta(w.app, w.title))
            lines.append(f"{short}  {label}{'  (active)' if w.focused else ''}")
        text = "\n".join(lines) if lines else "No windows are visible through accessibility."
        return ToolResult(ok=True, output=truncate(text, env.config.max_output_chars), trust=Trust.UNTRUSTED)


class FindArgs(Args):
    window_id: WindowRef = Field(description="A window id from list_windows, e.g. 'w1'.")
    query: str = Field(default="", max_length=100, description="Optional text to filter controls by.")
    limit: int = Field(default=40, ge=1, le=200)


class FindElements(WorkerTool[FindArgs]):
    name = "find_elements"
    description = "List the buttons, fields, menus and other controls in a window, with ids (e1, e2, …)."
    args_model = FindArgs
    risk = RiskLevel.READ
    capabilities = (Capability.SCREEN_READ,)

    def assess(self, args: FindArgs, env: HandsEnv) -> ToolAssessment:
        _, label = env.window(args.window_id)[:2]
        return ToolAssessment(risk=self.risk, summary=f"Read the controls in {label}")

    async def run(self, args: FindArgs, env: HandsEnv) -> ToolResult:
        real, label = env.window(args.window_id)[:2]
        infos = await asyncio.to_thread(env.hands.find_elements, real, args.query, args.limit)
        wmeta = env.ids.meta(env.ids.short_for(real) or "")
        lines = [_element_line(env, i, label, wmeta) for i in infos]
        text = "\n".join(lines) if lines else "No matching controls."
        return ToolResult(ok=True, output=truncate(text, env.config.max_output_chars), trust=Trust.UNTRUSTED)


class ElementArgs(Args):
    element_id: ElementRef = Field(description="A control id from find_elements, e.g. 'e3'.")


class ClickArgs(ElementArgs):
    about: str = Field(
        default="",
        max_length=300,
        description=(
            "For a pay, buy, send or delete button: what it will do, as the screen shows it "
            "(amount and currency, to whom, which card or account, what is sent or deleted). "
            "The user sees this when asked to confirm."
        ),
    )


class ClickElement(WorkerTool[ClickArgs]):
    name = "click_element"
    description = "Press a button, link, menu item or checkbox through accessibility (no mouse needed)."
    args_model = ClickArgs
    risk = RiskLevel.WRITE
    capabilities = (Capability.INPUT_CONTROL,)

    def assess(self, args: ClickArgs, env: HandsEnv) -> ToolAssessment:
        real, label = env.element(args.element_id)[:2]
        info = env.hands.describe(real)
        about = args.about.strip()
        # A price on a control is shopping (a product, the cart and its total). Its words ("Pay now"),
        # or the click described as paying an amount, make it a payment.
        risk = RiskLevel.PURCHASE if MONEY.search(about) else label_risk(info.name, info.text or "", about)
        return ToolAssessment(risk=risk, summary=f"{about} ({label})" if about else f"Click {label}")

    async def run(self, args: ClickArgs, env: HandsEnv) -> ToolResult:
        real, label, short = env.element(args.element_id)
        result = await asyncio.to_thread(env.hands.activate, real)
        return _report(f"Clicked {label}", result, env.config.max_output_chars, env.target(short))


class TypeArgs(Args):
    element_id: ElementRef
    text: str = Field(max_length=5_000)
    mode: Literal["replace", "append"] = "replace"


class TypeText(WorkerTool[TypeArgs]):
    name = "type_text"
    description = "Put text into a text field (replace its contents or append). Never used for passwords."
    args_model = TypeArgs
    risk = RiskLevel.WRITE
    capabilities = (Capability.INPUT_CONTROL,)

    def assess(self, args: TypeArgs, env: HandsEnv) -> ToolAssessment:
        real, label = env.element(args.element_id)[:2]
        info = env.hands.describe(real)
        preview = args.text if len(args.text) <= 40 else args.text[:40] + "…"
        summary = f"Type “{preview}” into {label}"
        if is_secret(info):
            return ToolAssessment(risk=self.risk, summary=summary, error="I don't type into password fields")
        return ToolAssessment(risk=self.risk, summary=summary, capabilities=_command(label, args.text))

    async def run(self, args: TypeArgs, env: HandsEnv) -> ToolResult:
        real, label, short = env.element(args.element_id)
        result = await asyncio.to_thread(env.hands.set_text, real, args.text, args.mode)
        return _report(f"Typed into {label}", result, env.config.max_output_chars, env.target(short))


class KeysArgs(Args):
    window_id: WindowRef
    keys: str = Field(max_length=60, description="A combo like 'ctrl+s', 'alt+tab' or 'enter'.")


class PressKeys(WorkerTool[KeysArgs]):
    name = "press_keys"
    description = "Press a key combo in a window. Only sent when that window is confirmed active."
    args_model = KeysArgs
    risk = RiskLevel.WRITE
    capabilities = (Capability.INPUT_CONTROL,)

    def assess(self, args: KeysArgs, env: HandsEnv) -> ToolAssessment:
        _, label = env.window(args.window_id)[:2]
        keys = parse_combo(args.keys)
        enter = "+".join(keys) in ("enter", "return", "ctrl+m")
        return ToolAssessment(
            risk=key_risk(keys, label),
            summary=f"Press {display(keys)} in {label}",
            error=uinput.available(),
            capabilities=_command(label, "\n" if enter else ""),
        )

    async def run(self, args: KeysArgs, env: HandsEnv) -> ToolResult:
        real, label, short = env.window(args.window_id)
        result = await asyncio.to_thread(env.hands.key, real, args.keys)
        verb = f"Pressed {display(parse_combo(args.keys))} in {label}"
        return _report(verb, result, env.config.max_output_chars, env.target(short))


class ScrollArgs(Args):
    window_id: WindowRef
    direction: Literal["up", "down", "left", "right"]
    amount: int = Field(default=3, ge=1, le=20)


class Scroll(WorkerTool[ScrollArgs]):
    name = "scroll"
    description = "Scroll the active window."
    args_model = ScrollArgs
    risk = RiskLevel.WRITE
    capabilities = (Capability.INPUT_CONTROL,)

    def assess(self, args: ScrollArgs, env: HandsEnv) -> ToolAssessment:
        _, label = env.window(args.window_id)[:2]
        return ToolAssessment(
            risk=self.risk, summary=f"Scroll {args.direction} in {label}", error=uinput.available()
        )

    async def run(self, args: ScrollArgs, env: HandsEnv) -> ToolResult:
        real, label, short = env.window(args.window_id)
        result = await asyncio.to_thread(env.hands.scroll, real, args.direction, args.amount)
        return _report(
            f"Scrolled {args.direction} in {label}", result, env.config.max_output_chars, env.target(short)
        )


class WindowArgs(Args):
    window_id: WindowRef


class FocusWindow(WorkerTool[WindowArgs]):
    name = "focus_window"
    description = "Bring a window to the front and make it active."
    args_model = WindowArgs
    risk = RiskLevel.WRITE
    capabilities = (Capability.INPUT_CONTROL,)

    def assess(self, args: WindowArgs, env: HandsEnv) -> ToolAssessment:
        _, label = env.window(args.window_id)[:2]
        return ToolAssessment(risk=self.risk, summary=f"Switch to {label}")

    async def run(self, args: WindowArgs, env: HandsEnv) -> ToolResult:
        real, label, short = env.window(args.window_id)
        result = await asyncio.to_thread(env.hands.focus, real)
        return _report(f"Switched to {label}", result, env.config.max_output_chars, env.target(short))


class MenuArgs(Args):
    window_id: WindowRef
    path: list[str] = Field(
        min_length=1, max_length=6, description="Menu names in order, e.g. ['File', 'Save']."
    )


class ChooseMenu(WorkerTool[MenuArgs]):
    name = "choose_menu"
    description = "Choose an item from a window's menu bar by its path."
    args_model = MenuArgs
    risk = RiskLevel.WRITE
    capabilities = (Capability.INPUT_CONTROL,)

    def assess(self, args: MenuArgs, env: HandsEnv) -> ToolAssessment:
        _, label = env.window(args.window_id)[:2]
        return ToolAssessment(
            risk=label_risk(*args.path), summary=f"Choose {' → '.join(args.path)} in {label}"
        )

    async def run(self, args: MenuArgs, env: HandsEnv) -> ToolResult:
        real, label, short = env.window(args.window_id)
        result = await asyncio.to_thread(env.hands.menu, real, args.path)
        return _report(
            f"Chose {' → '.join(args.path)} in {label}",
            result,
            env.config.max_output_chars,
            env.target(short),
        )


class ReadElement(WorkerTool[ElementArgs]):
    name = "read_element"
    description = "Read the full text of a control (password fields are never read)."
    args_model = ElementArgs
    risk = RiskLevel.READ
    capabilities = (Capability.SCREEN_READ,)

    def assess(self, args: ElementArgs, env: HandsEnv) -> ToolAssessment:
        _, label = env.element(args.element_id)[:2]
        return ToolAssessment(risk=self.risk, summary=f"Read {label}")

    async def run(self, args: ElementArgs, env: HandsEnv) -> ToolResult:
        real, _ = env.element(args.element_id)[:2]
        info = await asyncio.to_thread(env.hands.describe, real)
        if info.role == "password text":
            return ToolResult(ok=True, output="(password field: hidden)")
        text = info.text if info.text is not None else info.name
        return ToolResult(ok=True, output=truncate(text, env.config.max_output_chars), trust=Trust.UNTRUSTED)


class InsertArgs(Args):
    text: str = Field(min_length=1, max_length=10_000)


class InsertText(WorkerTool[InsertArgs]):
    name = "insert_text"
    description = "Type text at the cursor of the focused text field in the active app window (dictation)."
    args_model = InsertArgs
    risk = RiskLevel.WRITE
    capabilities = (Capability.INPUT_CONTROL,)

    def assess(self, args: InsertArgs, env: HandsEnv) -> ToolAssessment:
        preview = args.text if len(args.text) <= 80 else args.text[:80] + "…"
        return ToolAssessment(risk=self.risk, summary=f"Type “{preview}” into the focused field")

    async def run(self, args: InsertArgs, env: HandsEnv) -> ToolResult:
        result = await asyncio.to_thread(env.hands.insert_text, args.text)
        app = result.evidence.get("app", "the app")
        return _report(f"Typed into {app}", result, env.config.max_output_chars)


def is_secret(info: ElementInfo) -> bool:
    return info.role == "password text" or "password" in info.states


def _command(where: str, text: str) -> tuple[Capability, ...]:
    """Typing a line into a shell and pressing Enter runs a command: it needs shell.run too, so after
    reading outside content it asks, like any command."""
    return (Capability.SHELL_RUN,) if "\n" in text and runs_commands(where) else ()


class PasteArgs(Args):
    window_id: WindowRef
    text: str = Field(min_length=1, max_length=50_000)


PASTE_WAIT_S = 0.6
"""Long enough for the app to read the clipboard before the user's own is put back."""


class PasteText(WorkerTool[PasteArgs]):
    name = "paste_text"
    description = (
        "Paste text into the focused field of a window (click the field first). Fast, keeps line "
        "breaks, and never sends early: use it for anything long or multi-line, such as a prompt "
        "for an AI chat or an email body. The user's clipboard is put back afterwards."
    )
    args_model = PasteArgs
    risk = RiskLevel.WRITE
    capabilities = (Capability.INPUT_CONTROL,)

    def assess(self, args: PasteArgs, env: HandsEnv) -> ToolAssessment:
        _, label = env.window(args.window_id)[:2]
        preview = args.text if len(args.text) <= 60 else args.text[:60] + "…"
        lines = args.text.count("\n") + 1
        return ToolAssessment(
            risk=self.risk,
            summary=f"Paste “{preview}” ({lines} lines) into {label}",
            capabilities=_command(label, args.text),
        )

    async def run(self, args: PasteArgs, env: HandsEnv) -> ToolResult:
        real, label, short = env.window(args.window_id)
        await asyncio.to_thread(env.hands.focus, real)  # the field that will get it is that window's
        focused = await asyncio.to_thread(env.hands.focused_element)
        if focused is not None and is_secret(focused):
            return ToolResult(ok=False, output="Not pasted: the focused field is a password field.")
        before = await asyncio.to_thread(env.clipboard)
        if not await asyncio.to_thread(env.set_clipboard, args.text):
            return ToolResult(
                ok=False, output="Not pasted: the clipboard can't be used here; use type_text instead."
            )
        try:
            combo = "meta+v" if sys.platform == "darwin" else "ctrl+v"
            result = await asyncio.to_thread(env.hands.key, real, combo)
            await asyncio.sleep(PASTE_WAIT_S)
        finally:
            # Their copied text comes back. With nothing (or an image) there before, it's cleared, so
            # the brief doesn't stay behind; text too long to read whole is left as it was pasted.
            if before is not None and len(before) < clipboard.MAX_CHARS:
                await asyncio.to_thread(env.set_clipboard, before)
        chars = len(args.text)
        return _report(
            f"Pasted {chars} characters into {label}", result, env.config.max_output_chars, env.target(short)
        )
