"""Microphone capture: 16 kHz mono 16-bit in 100 ms blocks, from the default input device.

`WavMic` stands in for the microphone in tests: it plays WAV files as if spoken.
"""

import array
import math
import sys
import threading
import time
import wave
from collections.abc import Callable
from typing import Any

from contracts.ears import EARS_SAMPLE_RATE, Microphone

from . import devices

BLOCK_MS = 100
BLOCK_FRAMES = EARS_SAMPLE_RATE * BLOCK_MS // 1000
PORTAUDIO_HINT = " (Ubuntu/Debian: sudo apt install libportaudio2)"

Emit = Callable[[bytes], None]


def level_of(pcm: bytes) -> float:
    """Loudness 0-1: RMS on a log scale; -60 dBFS and below is 0, full scale is 1."""
    samples = array.array("h")
    samples.frombytes(pcm[: len(pcm) - len(pcm) % 2])
    if sys.byteorder == "big":
        samples.byteswap()
    if not samples:
        return 0.0
    rms = math.sqrt(sum(s * s for s in samples) / len(samples)) / 32768
    return 0.0 if rms <= 0.001 else min(1.0, 1 + math.log10(rms) / 3)


def resample(pcm: bytes, source_rate: int) -> bytes:
    """Mono 16-bit to 16 kHz by linear interpolation (only when a device refuses 16 kHz)."""
    if source_rate == EARS_SAMPLE_RATE:
        return pcm
    samples = array.array("h")
    samples.frombytes(pcm)
    count = len(samples)
    if count == 0:
        return b""
    ratio = source_rate / EARS_SAMPLE_RATE
    out = array.array("h")
    for i in range(count * EARS_SAMPLE_RATE // source_rate):
        pos = i * ratio
        j = int(pos)
        a, b = samples[j], samples[min(j + 1, count - 1)]
        out.append(int(a + (b - a) * (pos - j)))
    return out.tobytes()


class Mic:
    def __init__(
        self, emit: Emit, on_error: Callable[[str], None], on_chosen: Callable[[Microphone, str], None]
    ) -> None:
        self._emit = emit
        self._on_error = on_error
        self._on_chosen = on_chosen
        self._stream: Any = None

    @property
    def running(self) -> bool:
        return self._stream is not None

    def start(self, microphone: str = "") -> str | None:
        """Open the microphone ("" = choose automatically). Returns why it couldn't, or None."""
        if self._stream is not None:
            return None
        try:
            import sounddevice as sd
        except (ImportError, OSError) as exc:
            return f"audio isn't available: {exc}{PORTAUDIO_HINT if sys.platform.startswith('linux') else ''}"
        mic, reason = devices.choose(microphone, level_of)
        args = devices.open_args(mic.id if mic else "")
        try:
            self._stream = self._open(sd, EARS_SAMPLE_RATE, args)
        except Exception as first:
            try:  # some devices only do their own rate: take that and convert
                rate = int(sd.query_devices(kind="input")["default_samplerate"])
                self._stream = self._open(sd, rate, args)
            except Exception:
                return f"can't open the microphone: {first}"
        self._on_chosen(mic or Microphone(id="", name="system default"), reason)
        return None

    def _open(self, sd: Any, rate: int, args: dict[str, Any]) -> Any:
        def callback(data: Any, _frames: int, _time: Any, status: Any) -> None:
            if status and getattr(status, "input_overflow", False):
                return
            self._emit(resample(bytes(data), rate))

        stream = sd.RawInputStream(
            samplerate=rate,
            channels=1,
            dtype="int16",
            blocksize=rate * BLOCK_MS // 1000,
            callback=callback,
            **args,
        )
        stream.start()
        return stream

    def stop(self) -> None:
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception as exc:
                self._on_error(str(exc))


class WavMic:
    """Test microphone: the first file is "spoken" when the mic opens, each next one after the
    assistant finishes speaking (like a person answering); silence in between."""

    def __init__(self, emit: Emit, paths: list[str]) -> None:
        self._emit = emit
        self._clips = [_read_wav(p) for p in paths]
        self._on = False
        self._clip = -1
        self._pos = 0
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        return self._on

    def start(self, microphone: str = "") -> str | None:
        with self._lock:
            self._on = True
            if self._clip < 0:
                self._clip, self._pos = 0, 0
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, daemon=True)
                self._thread.start()
        return None

    def stop(self) -> None:
        self._on = False

    def advance(self) -> None:
        """The assistant finished speaking: "say" the next file, if there is one."""
        with self._lock:
            if 0 <= self._clip < len(self._clips) and self._pos < len(self._clips[self._clip]):
                return  # still speaking the current one
            if self._clip + 1 < len(self._clips):
                self._clip, self._pos = self._clip + 1, 0

    def _run(self) -> None:
        silence = bytes(BLOCK_FRAMES * 2)
        while True:
            time.sleep(BLOCK_MS / 1000)
            with self._lock:
                if not self._on:
                    continue
                block = silence
                if 0 <= self._clip < len(self._clips):
                    clip = self._clips[self._clip]
                    chunk = clip[self._pos : self._pos + BLOCK_FRAMES * 2]
                    self._pos += BLOCK_FRAMES * 2
                    block = chunk + bytes(BLOCK_FRAMES * 2 - len(chunk))
            self._emit(block)


def _read_wav(path: str) -> bytes:
    with wave.open(path, "rb") as wav:
        if (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) != (EARS_SAMPLE_RATE, 1, 2):
            raise ValueError(f"{path}: need 16 kHz mono 16-bit")
        return wav.readframes(wav.getnframes())
