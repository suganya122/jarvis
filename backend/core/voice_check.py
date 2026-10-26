"""`python -m core.cli check-voice`: is the microphone heard, and can it speak?

Opens the microphone through the ears process for a few seconds while you talk, reports how
loud you came through against the level the hands-free loop listens for, then says a test
sentence. Nothing is recorded or transcribed.
"""

import asyncio
from pathlib import Path

from core import voice_install
from core.ears_host import EarsHost
from core.hands_free_types import GATE_LEVEL, QUIET_LEVEL

LISTEN_S = 5.0


async def check(data_dir: Path, listen_s: float = LISTEN_S) -> int:
    levels: list[float] = []
    ears = EarsHost(lambda _pcm, level: levels.append(level))
    ears.piper, ears.voice = voice_install.installed_paths(data_dir)
    await ears.start()
    try:
        for _ in range(50):
            if ears.available is not None:
                break
            await asyncio.sleep(0.1)
        print(f"Microphone: {ears.detail}")
        if ears.available is not True:
            print("It can't listen, so hands-free voice won't work until this is fixed.")
        else:
            print(f"Say something for {listen_s:.0f} seconds (“testing, one, two, three”)…", flush=True)
            await ears.listen(True)
            await asyncio.sleep(listen_s)
            await ears.listen(False)
            print(_verdict(levels))
        voice = "the natural voice (Piper)" if ears.voice else "this computer's own voice"
        print(f"Now speaking a test sentence with {voice}…", flush=True)
        spoke = await ears.say("This is how I sound. If you can hear me, voice is working.", timeout_s=60)
        print(
            "Did you hear it? If not, check the speaker volume."
            if spoke
            else "It couldn't speak (see above)."
        )
        return 0 if ears.available and spoke and levels and max(levels) >= GATE_LEVEL else 1
    finally:
        await ears.stop()


def _verdict(levels: list[float]) -> str:
    if not levels:
        return "No sound arrived at all: the microphone may be muted, or another app has it."
    peak = max(levels)
    line = (
        f"Loudest: {peak:.2f} (it listens above {GATE_LEVEL:.2f}; speech is usually above {QUIET_LEVEL:.2f})."
    )
    if peak < GATE_LEVEL:
        return line + " Too quiet: raise the input volume in your sound settings, or move closer."
    if peak < QUIET_LEVEL:
        return line + " A bit quiet: it should work, but raising the input volume will help."
    return line + " Good: you came through clearly."
