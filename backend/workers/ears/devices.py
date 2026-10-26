"""Which microphone to listen to.

Computers often have an input that is only static (an empty jack turned up) or silence, and
it may well be the system default. Automatic choice listens to each input for under a second
and skips those, preferring the system default when it sounds like a real room.

Linux (PipeWire): inputs are PipeWire sources, opened through PipeWire's ALSA device with
`PIPEWIRE_NODE`, so other apps can still use the same microphone. Elsewhere: PortAudio devices.
"""

import json
import os
import shutil
import statistics
import subprocess
import sys
import time
from collections.abc import Callable
from typing import Any, Literal

from contracts.ears import EARS_SAMPLE_RATE, Microphone

Health = Literal["ok", "static", "silent"]
PROBE_S = 0.8
STATIC_LEVEL = 0.6
STATIC_SPREAD = 0.05
"""Loud and flat (it barely changes from one tenth of a second to the next): hiss, not a room."""
SILENT_LEVEL = 0.03


def pipewire() -> bool:
    return sys.platform.startswith("linux") and shutil.which("pw-dump") is not None


def list_microphones() -> tuple[list[Microphone], str]:
    """(inputs, the system default's id)."""
    if pipewire():
        return _pipewire_sources()
    try:
        import sounddevice as sd

        devices = sd.query_devices()
        default = sd.default.device[0]
    except Exception:
        return [], ""
    mics = [
        Microphone(id=str(d["name"]), name=str(d["name"])) for d in devices if d["max_input_channels"] > 0
    ]
    default_id = str(devices[default]["name"]) if isinstance(default, int) and default >= 0 else ""
    return mics, default_id


def _pipewire_sources() -> tuple[list[Microphone], str]:
    try:
        out = subprocess.run(["pw-dump"], capture_output=True, text=True, timeout=5, check=True).stdout  # noqa: S607
        objects: list[dict[str, Any]] = json.loads(out)
    except (OSError, subprocess.SubprocessError, ValueError):
        return [], ""
    mics, default = [], ""
    for obj in objects:
        props = (obj.get("info") or {}).get("props") or {}
        if props.get("media.class") == "Audio/Source" and props.get("node.name"):
            mics.append(
                Microphone(id=props["node.name"], name=props.get("node.description") or props["node.name"])
            )
        if obj.get("type") == "PipeWire:Interface:Metadata":
            for entry in obj.get("metadata") or []:
                if entry.get("key") == "default.audio.source":
                    value = entry.get("value")
                    if isinstance(value, dict):
                        default = str(value.get("name", ""))
    return mics, default


def open_args(mic_id: str) -> dict[str, Any]:
    """sounddevice arguments for this microphone ("" = the system default)."""
    if pipewire():
        if mic_id:
            os.environ["PIPEWIRE_NODE"] = mic_id  # read by PipeWire's ALSA device when it opens
        else:
            os.environ.pop("PIPEWIRE_NODE", None)
        return {"device": "pipewire"}
    return {"device": mic_id} if mic_id else {}


def health(levels: list[float]) -> Health:
    if not levels:
        return "silent"
    mean = statistics.mean(levels)
    if mean < SILENT_LEVEL:
        return "silent"
    if mean > STATIC_LEVEL and statistics.pstdev(levels) < STATIC_SPREAD:
        return "static"
    return "ok"


def probe(mic_id: str, level_of: Callable[[bytes], float], seconds: float = PROBE_S) -> Health:
    try:
        import sounddevice as sd

        blocks: list[bytes] = []
        rate = EARS_SAMPLE_RATE

        def keep(data: Any, _frames: int, _time: Any, _status: Any) -> None:
            blocks.append(bytes(data))

        args = open_args(mic_id)
        with sd.RawInputStream(
            samplerate=rate, channels=1, dtype="int16", blocksize=rate // 10, callback=keep, **args
        ):
            time.sleep(seconds)
        return health([level_of(b) for b in blocks[1:]])  # the first block can hold a start-up click
    except Exception:
        return "silent"


def choose(
    preferred: str, level_of: Callable[[bytes], float], listing: tuple[list[Microphone], str] | None = None
) -> tuple[Microphone | None, str]:
    """The microphone to use and why. A chosen one is used as is; otherwise the default, unless
    it's only static or silence and another input sounds like a real room."""
    mics, default = listing if listing is not None else list_microphones()
    by_id = {m.id: m for m in mics}
    if preferred:
        if preferred in by_id:
            return by_id[preferred], "chosen in Settings"
        return None, "the microphone chosen in Settings isn't connected; using the default"
    ordered = sorted(mics, key=lambda m: m.id != default)  # the default first
    verdicts: list[str] = []
    for mic in ordered:
        verdict = probe(mic.id, level_of)
        if verdict == "ok":
            if mic.id == default or not default:
                return mic, "the system default"
            skipped = "; ".join(verdicts) or "the default input didn't work"
            return mic, f"chosen automatically ({skipped})"
        verdicts.append(f"{by_id[mic.id].name} is only {verdict}")
    if ordered:
        first = by_id.get(default) or ordered[0]
        return first, "no input sounded like a room; using the default (" + "; ".join(verdicts) + ")"
    return None, "no microphones found"
