"""The natural voice (Piper): where it lives once installed, and how to install it.

`python -m core.cli install-voice [--voice ljspeech|joe]` sets it up in the app data folder:
a private Python environment with Piper in it, and one voice model checked against a pinned
SHA-256. Piper is GPL-3.0, so it is never imported or bundled with this (MIT) program: it's
only downloaded when you ask, kept in its own folder, and run as a separate program
(workers/ears/speaker.py). The voices offered are trained on public-domain / CC0 recordings.
"""

import hashlib
import shutil
import subprocess
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path

VOICE_DIR = "piper"
PROGRAM = "piper"
MODEL = "voice.onnx"
PIPER_PACKAGE = "piper-tts==1.8.0"
VOICES_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main"


@dataclass(frozen=True)
class Voice:
    key: str
    name: str
    path: str
    onnx_sha256: str
    json_sha256: str
    describe: str
    license: str
    size_mb: int


VOICES = {
    v.key: v
    for v in (
        Voice("ljspeech", "en_US-ljspeech-medium", "en/en_US/ljspeech/medium",
              "6f52a751e2349abe7a76735eb09dc1875298c77ea2342ffd2fef79ff81b87f22",
              "141d612cc0a95ed7efc1ca936b845c2364967f2e9217c5dbfcf69fc4d6c65860",
              "US English, female", "trained on the LJ Speech recordings (public domain)", 64),
        Voice("joe", "en_US-joe-medium", "en/en_US/joe/medium",
              "58afce0321b8d9c46d7cdf9c16500cc55a793b4220212dba6b70fb788b3baf06",
              "3d6d5410b3795cb1950595247ef8f06190719e6fdbfa3a2356d8ec368e1aad33",
              "US English, male", "trained on recordings released as CC0 (public domain)", 64),
    )
}  # fmt: skip
DEFAULT_VOICE = "ljspeech"


def voice_dir(data_dir: Path) -> Path:
    return data_dir / VOICE_DIR


def _program(root: Path) -> Path:
    if sys.platform == "win32":
        return root / "venv" / "Scripts" / f"{PROGRAM}.exe"
    return root / "venv" / "bin" / PROGRAM


def installed_paths(data_dir: Path) -> tuple[str, str]:
    """(Piper program, voice model), or ("", "") when the natural voice isn't installed."""
    root = voice_dir(data_dir)
    program, model = _program(root), root / MODEL
    return (str(program), str(model)) if program.is_file() and model.is_file() else ("", "")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _download(url: str, dest: Path, sha256: str, say: "Say") -> None:
    part = dest.with_name(dest.name + ".part")
    with urllib.request.urlopen(url, timeout=60) as resp, part.open("wb") as out:  # noqa: S310 - fixed https URL
        total, done, shown = int(resp.headers.get("Content-Length") or 0), 0, -1
        while block := resp.read(1 << 16):
            out.write(block)
            done += len(block)
            percent = done * 100 // total if total else -1
            if percent >= shown + 10:
                shown = percent
                say(f"  {dest.name}: {percent}%" if total else f"  {dest.name}: {done >> 20} MB")
    if _sha256(part) != sha256:
        part.unlink()
        raise RuntimeError(f"{dest.name} didn't match its pinned checksum; nothing was installed")
    part.replace(dest)


class Say:
    def __call__(self, text: str) -> None:
        print(text, flush=True)


def install(data_dir: Path, voice_key: str = DEFAULT_VOICE, yes: bool = False, say: Say | None = None) -> int:
    say = say or Say()
    voice = VOICES[voice_key]
    root = voice_dir(data_dir)
    say(f"Natural voice: {voice.describe} ({voice.name}), {voice.license}.")
    say(f"It downloads Piper ({PIPER_PACKAGE}, GPL-3.0, run as a separate program) and its speech")
    say(f"runtime (about 35 MB, about 200 MB on disk) plus the voice (about {voice.size_mb} MB).")
    say(f"Everything goes into {root} and stays on this computer.")
    if not yes and input("Install it? [y/N] ").strip().lower() not in ("y", "yes"):
        say("Nothing was installed.")
        return 1
    root.mkdir(parents=True, exist_ok=True)
    try:
        if not _program(root).is_file():
            say("Setting up Piper…")
            subprocess.run([sys.executable, "-m", "venv", str(root / "venv")], check=True)  # noqa: S603
            pip = root / "venv" / ("Scripts" if sys.platform == "win32" else "bin") / "pip"
            subprocess.run([str(pip), "install", "--disable-pip-version-check", "-q", PIPER_PACKAGE],  # noqa: S603
                           check=True)  # fmt: skip
        say("Downloading the voice…")
        base = f"{VOICES_URL}/{voice.path}/{voice.name}"
        _download(f"{base}.onnx.json", root / f"{MODEL}.json", voice.json_sha256, say)
        _download(f"{base}.onnx", root / MODEL, voice.onnx_sha256, say)
        (root / "VOICE.txt").write_text(
            f"{voice.name}: {voice.describe}, {voice.license}.\nSource: {base}.onnx\n"
            f"Spoken by Piper ({PIPER_PACKAGE}), GPL-3.0: https://github.com/OHF-Voice/piper1-gpl\n",
            encoding="utf-8",
        )
        if not _speaks(root):
            raise RuntimeError("Piper installed but couldn't speak a test sentence")
    except (OSError, subprocess.CalledProcessError, RuntimeError) as exc:
        say(f"The natural voice couldn't be installed: {exc}")
        return 1
    say("Done. The assistant uses the natural voice from its next sentence (no restart needed).")
    return 0


def _speaks(root: Path) -> bool:
    done = subprocess.run(  # noqa: S603 - our own install
        [str(_program(root)), "--model", str(root / MODEL), "--output-raw"],
        input=b"Hello.\n",
        capture_output=True,
        timeout=120,
        check=False,
    )
    return done.returncode == 0 and len(done.stdout) > 1000


def remove(data_dir: Path) -> int:
    root = voice_dir(data_dir)
    if root.exists():
        shutil.rmtree(root)
        print("The natural voice was removed; the computer's own voice is used again.")
    else:
        print("The natural voice isn't installed.")
    return 0
