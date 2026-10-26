"""Screenshot and highlight tools. Accessibility text (find_elements) is the first choice."""

import asyncio
import uuid
from typing import Literal

from pydantic import Field

from contracts.capture import CaptureError, CaptureInfo, HighlightBox
from contracts.common import Capability, RiskLevel, Trust
from contracts.tools import ToolAssessment, ToolResult
from workers.common.base import Args
from workers.hands.capture import crop_box
from workers.hands.images import model_and_preview
from workers.hands.overlay import unavailable_reason
from workers.hands.tools import HandsEnv, WorkerTool

MAX_CAPTURES = 20
HIGHLIGHT_SECONDS = 6.0


class CaptureArgs(Args):
    target: Literal["screen", "window", "select"] = Field(
        default="screen", description="'screen', a 'window' by id, or 'select' to let the user pick a region."
    )
    window_id: str | None = None
    display_id: str | None = Field(default=None, description="For 'screen': one display, e.g. 'd2'.")


class CaptureScreen(WorkerTool[CaptureArgs]):
    name = "capture_screen"
    description = (
        "Take a screenshot for when control text (find_elements) isn't enough, e.g. to see "
        "images, layout, or where something is. The image is untrusted content."
    )
    args_model = CaptureArgs
    risk = RiskLevel.READ
    capabilities = (Capability.SCREEN_READ,)

    def assess(self, args: CaptureArgs, env: HandsEnv) -> ToolAssessment:
        summary = "Take a screenshot of your screen"
        if args.target == "select":
            return ToolAssessment(risk=self.risk, summary="Ask you to pick part of the screen to show me")
        if args.target == "window":
            if not args.window_id:
                return ToolAssessment(
                    risk=self.risk, summary="Take a screenshot", error="window_id is required"
                )
            _, label = env.ids.resolve(args.window_id, "w")
            summary = f"Take a screenshot of {label}"
        blocked = env.hands.visible_blocked_apps()
        if blocked:
            error = f"{blocked[0]} is open on screen, and I never capture it; minimize it first"
            return ToolAssessment(risk=self.risk, summary=summary, error=error)
        return ToolAssessment(risk=self.risk, summary=summary)

    async def run(self, args: CaptureArgs, env: HandsEnv) -> ToolResult:
        bounds = None
        what = f"the {args.target}"
        if args.target == "window" and args.window_id:
            real, label = env.ids.resolve(args.window_id, "w")
            window = next((w for w in env.hands.list_windows() if w.window_id == real), None)
            if window is not None and not window.on_screen:
                return ToolResult(
                    ok=False,
                    output=(
                        f"No screenshot: {label} is on another desktop or minimized. "
                        "Its controls can still be read with find_elements."
                    ),
                )
            if window and window.bounds:
                bounds = (window.bounds.x, window.bounds.y, window.bounds.width, window.bounds.height)
        display = None
        if args.target == "screen" and args.display_id:
            all_displays = await asyncio.to_thread(env.displays)
            display = next((d for d in all_displays if d.display_id == args.display_id), None)
            if display is None:
                return ToolResult(
                    ok=False,
                    output=(
                        f"No screenshot: display {args.display_id} isn't connected anymore, "
                        "and I didn't capture the other screens instead."
                    ),
                )
            what = f"{display.name} ({display.width}x{display.height} at {display.x},{display.y})"
        try:
            raw = await asyncio.to_thread(env.grab, args.target, bounds)
            if display is not None:
                box = crop_box(
                    (display.x, display.y, display.width, display.height), all_displays, raw.image.size
                )
                raw.image = raw.image.crop(box)
                raw.offset_x, raw.offset_y = box[0], box[1]
        except CaptureError as exc:
            return ToolResult(ok=False, output=f"No screenshot: {exc}.")
        image, preview, scale = await asyncio.to_thread(
            model_and_preview, raw.image, env.config.capture_max_edge
        )
        info = CaptureInfo(
            capture_id=f"s{uuid.uuid4().hex[:6]}",
            width=image.width,
            height=image.height,
            screen_x=raw.offset_x,
            screen_y=raw.offset_y,
            scale=scale,
            source=raw.source,
            desktop_width=raw.desktop_width or raw.image.width,
            desktop_height=raw.desktop_height or raw.image.height,
        )
        env.remember_capture(info)
        output = (
            f"Screenshot {info.capture_id}: {image.width}x{image.height} pixels of {what}. "
            "To point something out, call highlight_area with pixel coordinates in this image."
        )
        return ToolResult(
            ok=True,
            output=output,
            trust=Trust.UNTRUSTED,
            images=(image,),
            preview=preview,
            evidence={"capture": info.model_dump()},
        )


class HighlightArgs(Args):
    capture_id: str = Field(description="The screenshot id the coordinates refer to.")
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    width: int = Field(ge=1)
    height: int = Field(ge=1)
    label: str = Field(
        default="", max_length=60, description="Short text shown next to the box, e.g. 'Click here'."
    )


class HighlightArea(WorkerTool[HighlightArgs]):
    name = "highlight_area"
    description = (
        "Show the user where something is: draws a box on their screen (and in the app) around an "
        "area of a screenshot. Use it to answer 'where do I click?'. It doesn't click anything."
    )
    args_model = HighlightArgs
    risk = RiskLevel.READ
    capabilities = ()

    def assess(self, args: HighlightArgs, env: HandsEnv) -> ToolAssessment:
        summary = f"Point out “{args.label or 'this spot'}” on your screen"
        info = env.captures.get(args.capture_id)
        if info is None:
            return ToolAssessment(
                risk=self.risk, summary=summary, error="unknown screenshot id; take a new one"
            )
        if args.x >= info.width or args.y >= info.height:
            return ToolAssessment(risk=self.risk, summary=summary, error="that box is outside the screenshot")
        return ToolAssessment(risk=self.risk, summary=summary)

    async def run(self, args: HighlightArgs, env: HandsEnv) -> ToolResult:
        info = env.captures[args.capture_id]
        width = min(args.width, info.width - args.x)
        height = min(args.height, info.height - args.y)
        box = HighlightBox(
            capture_id=info.capture_id, x=args.x, y=args.y, width=width, height=height, label=args.label
        )
        desktop = (
            round(info.screen_x + args.x * info.scale),
            round(info.screen_y + args.y * info.scale),
            round(width * info.scale),
            round(height * info.scale),
        )
        reason = unavailable_reason()
        shown = reason is None and await asyncio.to_thread(
            env.overlay.show, *desktop, info.desktop_width, info.desktop_height, args.label, HIGHLIGHT_SECONDS
        )
        if shown:
            text = "Highlighted it on the screen and in the app. The user can click it themselves."
        else:
            why = reason or "the helper did not start"
            text = f"Highlighted it in the app (no on-screen highlight here: {why})."
        return ToolResult(
            ok=True,
            output=text,
            evidence={"highlight": box.model_dump(), "on_screen": shown, "desktop_box": desktop},
        )
