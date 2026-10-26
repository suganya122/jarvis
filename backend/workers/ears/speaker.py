"""Speaking text aloud: the Piper natural voice if it's installed, else the computer's own
voice (spd-say on Linux, `say` on macOS, System.Speech on Windows).

Piper is a separate downloaded program run as its own process; nothing of it is imported.
Text is always given on stdin, never on a command line.
"""

import contextlib
import json
import os
import shutil
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import IO, Any

from contracts.ears import EarsSaid, EarsSay

DEFAULT_PIPER_RATE = 22_050
WINDOWS_SPEAK = (
    "Add-Type -AssemblyName System.Speech; "
    "(New-Object System.Speech.Synthesis.SpeechSynthesizer).Speak([Console]::In.ReadToEnd())"
)


def system_voice_command() -> list[str] | None:
    """The OS's own text-to-speech program, reading text from stdin."""
    if sys.platform == "darwin":
        return ["say"] if shutil.which("say") else None
    if sys.platform == "win32":
        return ["powershell", "-NoProfile", "-Command", WINDOWS_SPEAK]
    return ["spd-say", "-w", "-e"] if shutil.which("spd-say") else None


def _quiet_speech_server() -> None:
    with contextlib.suppress(OSError, subprocess.SubprocessError):
        subprocess.run(["spd-say", "-S"], capture_output=True, check=False, timeout=5)  # noqa: S607


def piper_rate(voice: str) -> int:
    try:
        data = json.loads(Path(voice + ".json").read_text(encoding="utf-8"))
        return int(data["audio"]["sample_rate"])
    except (OSError, ValueError, KeyError, TypeError):
        return DEFAULT_PIPER_RATE


class Speaker:
    def __init__(self, done: Callable[[EarsSaid], None]) -> None:
        self._done = done
        self._gen = 0
        self._proc: subprocess.Popen[bytes] | None = None
        self._lock = threading.Lock()

    def say(self, msg: EarsSay) -> None:
        self.stop()
        with self._lock:
            gen = self._gen
        threading.Thread(target=self._run, args=(msg, gen), daemon=True).start()

    def stop(self) -> None:
        """Cut off what we're saying. Never blocks: the speech server is told in the background."""
        with self._lock:
            self._gen += 1
            proc, self._proc = self._proc, None
        if proc is None or proc.poll() is not None:
            return  # nothing of ours is speaking
        proc.terminate()
        if isinstance(proc.args, list) and proc.args[:1] == [
            "spd-say"
        ]:  # it hands the text to a server: stop that too
            threading.Thread(target=_quiet_speech_server, daemon=True).start()

    def _current(self, gen: int) -> bool:
        return gen == self._gen

    def _run(self, msg: EarsSay, gen: int) -> None:
        voice, ok, detail = "none", False, ""
        try:
            sink = os.environ.get("ASSISTANT_EARS_SINK")
            if sink:  # tests: record what would be said instead of making sound
                with open(sink, "a", encoding="utf-8") as handle:
                    handle.write(msg.text + "\n")
                threading.Event().wait(0.15)
                voice, ok = "test", True
            elif msg.piper and msg.voice and Path(msg.piper).is_file() and Path(msg.voice).is_file():
                voice, ok = "piper", self._piper(msg, gen)
            else:
                command = system_voice_command()
                if command is None:
                    detail = "no voice is available on this computer"
                else:
                    voice, ok = "system", self._pipe(command, msg.text, gen)
        except Exception as exc:
            detail = f"{type(exc).__name__}: {exc}"[:200]
        if not self._current(gen):
            ok, detail = False, "stopped"
        self._done(EarsSaid(say_id=msg.say_id, ok=ok, detail=detail, voice=voice))

    def _spawn(self, argv: list[str], stdout: int | IO[Any]) -> subprocess.Popen[bytes]:
        proc = subprocess.Popen(  # noqa: S603 - fixed program names and paths, no shell
            argv, stdin=subprocess.PIPE, stdout=stdout, stderr=subprocess.DEVNULL
        )
        with self._lock:
            self._proc = proc
        return proc

    def _pipe(self, argv: list[str], text: str, gen: int) -> bool:
        proc = self._spawn(argv, subprocess.DEVNULL)
        assert proc.stdin is not None
        proc.stdin.write(text.encode("utf-8"))
        proc.stdin.close()
        proc.wait()
        return self._current(gen) and proc.returncode == 0

    def _piper(self, msg: EarsSay, gen: int) -> bool:
        import sounddevice as sd

        proc = self._spawn([msg.piper, "--model", msg.voice, "--output-raw"], subprocess.PIPE)
        assert proc.stdin is not None and proc.stdout is not None
        proc.stdin.write(msg.text.encode("utf-8") + b"\n")
        proc.stdin.close()
        leftover = b""
        with sd.RawOutputStream(samplerate=piper_rate(msg.voice), channels=1, dtype="int16") as out:
            while self._current(gen) and (chunk := proc.stdout.read(4096)):
                chunk = leftover + chunk
                usable = len(chunk) - len(chunk) % 2
                out.write(chunk[:usable])
                leftover = chunk[usable:]
        proc.wait()
        return self._current(gen)
