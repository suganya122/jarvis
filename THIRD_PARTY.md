# Third-party dependencies

Every dependency must have a permissive license (MIT / BSD / Apache-2.0 or similar).
Anything else is flagged here. Exact pins live in `backend/requirements.txt` and
`frontend/package-lock.json`.

No models, voices, or other weights are bundled yet. Each one gets its own row when it
is added (milestone 7).

## Backend runtime (Python)

| Package | Version | License |
|---|---|---|
| aiosqlite | 0.22.1 | MIT |
| annotated-doc | 0.0.5 | MIT |
| annotated-types | 0.8.0 | MIT |
| anthropic | 2.3.0 | Apache-2.0 |
| anyio | 4.15.1 | MIT |
| certifi | 2026.10.23 | **MPL-2.0** (see note) |
| cffi | 2.1.1 | MIT-0 |
| charset-normalizer | 3.5.2 | MIT |
| click | 8.5.0 | BSD-3-Clause |
| cryptography | 50.0.2 | Apache-2.0 OR BSD-3-Clause |
| docstring-parser | 0.18.0 | MIT |
| fastapi | 0.162.0 | MIT |
| h11 | 0.16.0 | MIT |
| httpcore / httpcore2 | 1.0.9 / 2.16.0 | BSD-3-Clause |
| httptools | 0.9.0 | MIT |
| httpx / httpx2 | 0.28.1 / 2.16.0 | BSD-3-Clause |
| idna | 3.20 | BSD-3-Clause |
| jaraco.classes / .context / .functools | 3.4.0 / 6.1.2 / 4.6.0 | MIT |
| jeepney (Linux, direct: AT-SPI and D-Bus) | 0.9.0 | MIT |
| jiter | 0.18.0 | MIT |
| keyring | 25.7.0 | MIT |
| more-itertools | 11.2.1 | MIT |
| opentelemetry-api | 1.45.1 | Apache-2.0 |
| pillow | 13.0.0 | MIT-CMU (HPND, permissive) |
| playwright | 1.64.1 | Apache-2.0 (drives the browser; includes a Node.js driver, MIT) |
| greenlet | 3.5.6 | MIT AND PSF-2.0 (Playwright dependency) |
| pyee | 14.0.0 | MIT (Playwright dependency) |
| platformdirs | 4.13.5 | MIT |
| pycparser | 3.12 | BSD-3-Clause |
| pydantic / pydantic-core | 2.14.1 / 2.50.1 | MIT |
| python-dotenv (reads `.env`) | 1.2.4 | BSD-3-Clause |
| requests | 2.34.2 | Apache-2.0 |
| PyYAML | 6.0.3 | MIT |
| SecretStorage (Linux) | 3.5.0 | BSD-3-Clause |
| sniffio | 1.3.1 | MIT OR Apache-2.0 |
| srt | 3.5.3 | MIT |
| starlette | 1.10.0 | BSD-3-Clause |
| tqdm | 4.70.1 | **MPL-2.0 AND MIT** (see note) |
| truststore | 0.10.5 | MIT |
| typing-extensions | 4.16.0 | PSF-2.0 |
| typing-inspection | 0.4.4 | MIT |
| urllib3 | 2.8.0 | MIT |
| uvicorn | 0.54.0 | BSD-3-Clause |
| uvloop (not Windows) | 0.23.0 | MIT OR Apache-2.0 |
| sounddevice (microphone and speaker for hands-free voice) | 0.5.6 | MIT |
| tzdata (Windows only: time zones for reminders and routines) | 2026.4 | Apache-2.0 |
| vosk (speech-to-text engine; bundles Kaldi, OpenFST, OpenBLAS) | 0.3.45 (0.3.44 on macOS: the last with a Mac build) | Apache-2.0 (Kaldi/OpenFST Apache-2.0, OpenBLAS BSD-3) |
| watchfiles | 1.3.0 | MIT |
| websockets | 17.3 | BSD-3-Clause |

**Flag: certifi (MPL-2.0).** This is the Mozilla CA bundle. httpx needs it, so it is a
transitive dependency we can't avoid. MPL-2.0 is weak, file-level copyleft. Using and
redistributing it unmodified is allowed, and it does not affect our MIT code. If we ever
modify certifi's files, those modified files must stay MPL-2.0.

**Flag: tqdm (MPL-2.0 AND MIT).** Vosk pulls it in. It's the same weak, file-level
copyleft as certifi: it's used unmodified, and our code is unaffected.

## Browsers (not bundled)

Web tasks use a browser already on the computer: Google Chrome or Microsoft Edge (their
vendors' licenses), or Chromium (BSD-3-Clause) if the user installs it with
`python -m playwright install chromium`. Nothing is downloaded automatically.

## Standard-library GUI (voice overlay)

| Component | License | Notes |
|---|---|---|
| tkinter / Tcl/Tk 8.6 | PSF-2.0 / Tcl/Tk license (BSD-style) | Part of Python; on Debian/Ubuntu it's the `python3-tk` system package. Not bundled by this project. |

## Models (downloaded only when the user clicks, never bundled)

| Model | Size | License |
|---|---|---|
| vosk-model-small-en-us-0.15 | 41 MB | Apache-2.0 (Alpha Cephei) |
| vosk-model-en-us-0.22-lgraph | 131 MB | Apache-2.0 (Alpha Cephei) |

**Rejected for license reasons:**
- **sherpa-onnx** (its native libraries statically include espeak-ng, GPL-3).
- **Kokoro and Matcha TTS packages** (they phonemize with espeak-ng, GPL-3).
- **Piper voices with restrictive datasets:** Lessac (Blizzard licence), Ryan and HFC
  (CC BY-NC-SA, non-commercial).
- **Kroko ASR models** (unclear license).

## Audio library (system)

| Library | License | Notes |
|---|---|---|
| PortAudio | MIT | Used by sounddevice. Included in its macOS and Windows wheels; on Linux it's the `libportaudio2` system package. |

## Used by the one-line installers (downloaded at install time, never bundled)

| Component | License | Notes |
|---|---|---|
| uv 0.13.0 (Astral) | MIT OR Apache-2.0 | Gets Python and installs packages. Downloaded from its GitHub release and checked against a pinned SHA-256 in `install.sh` / `install.ps1`. |
| python-build-standalone (via uv, when the system has no Python 3.12) | PSF-2.0, plus its bundled libraries' permissive licenses (OpenSSL Apache-2.0, Tcl/Tk BSD-style, …) | A private Python 3.12 in the install folder. |
| wmctrl (Linux, non-GNOME X11 desktops) | **GPL-2.0** (flag) | The distribution's own package, run as a command; never bundled. |
| Apple command line tools (`swiftc`, `codesign`) | Apple's license | Only to build the small `Jarvis.app` launcher on your Mac; not shipped. |

The GNOME Shell extension in `backend/workers/common/wm/gnome_extension/` is part of this project
(MIT). It runs inside GNOME Shell and only answers the assistant's list / place / maximize /
focus calls on your session bus.

## Optional natural voice (installed only when you run `core.cli install-voice`)

**Flag: Piper is GPL-3.0.** It is never imported, bundled or shipped with this program.
`python -m core.cli install-voice` downloads it, after you confirm, into its own Python
environment in the app data folder. It runs there as a separate program that reads text
on stdin and writes audio on stdout (`workers/ears/speaker.py`), so this program stays
MIT. If you ever ship a bundle that *includes* Piper, the GPL applies to that bundle.

| Component | Version | License |
|---|---|---|
| piper-tts (includes espeak-ng for pronunciation) | 1.8.0 | **GPL-3.0-or-later** |
| onnxruntime (Piper's dependency) | 1.31 | MIT |
| numpy (Piper's dependency) | 2.5 | BSD-3-Clause |
| Voice: en_US-ljspeech-medium (the default) | 64 MB | Trained on LJ Speech, public domain. SHA-256 pinned. |
| Voice: en_US-joe-medium (`--voice joe`) | 63 MB | Trained on CC0 recordings. SHA-256 pinned. |

Without it, spoken replies use the operating system's own voice program, run as a separate
process: `say` (macOS), System.Speech (Windows), or `spd-say` on Linux. **Flag:**
speech-dispatcher (`spd-say`) is GPL-2.0-or-later; it is the distribution's own program,
called as a command and never bundled.

## Test-only system packages (Linux; not installed in the venv, never shipped)

The Hands tests drive a GTK fixture app in a private headless desktop. These come from
the OS package manager, are only used while testing, and are not bundled or distributed:

| Package | License |
|---|---|
| python3-gi (PyGObject) + GTK 3 | LGPL-2.1-or-later |
| Xvfb | MIT/X11 |
| dbus-daemon, at-spi2-core | AFL-2.1 or GPL-2.0 / LGPL-2.1 |
| espeak / espeak-ng (generates test speech only) | GPL-3.0 |
| ffmpeg (converts test speech to 16 kHz only) | LGPL-2.1 / GPL-2.0 |

At runtime the app needs none of these. The one optional exception: if python3-gi is
present, the on-screen highlight helper (`overlay_gtk.py`) uses the system's GTK. It is
never bundled, and without it the highlight only shows in the app. It talks to the system accessibility bus over
D-Bus with jeepney.

## Backend dev only

pytest (MIT), pytest-asyncio (Apache-2.0), ruff (MIT), mypy (MIT), pip-audit (Apache-2.0).

## Frontend runtime (bundled into the UI)

| Package | License |
|---|---|
| react | MIT |
| react-dom | MIT |

## Fonts (bundled into the UI, `frontend/public/fonts/`)

| Font | License | Notes |
|---|---|---|
| DotGothic16 (Fontworks) | SIL OFL 1.1 | Brand and headings. Latin subset (`DotGothic16-latin.woff2`, 9 KB); license text alongside as `DotGothic16-OFL.txt`. No Reserved Font Name, so the subset keeps its name. |
| Inter (Rasmus Andersson) | SIL OFL 1.1 | Body text. Latin subset (`Inter-latin.woff2`, 57 KB); license text alongside as `Inter-OFL.txt`. No Reserved Font Name. |

**Flag: OFL is not MIT/BSD.** It allows bundling and modifying fonts in any software,
including commercial software. It has two conditions: the font can't be sold on its own,
and modified fonts stay under the OFL. Both are met: the fonts ship only inside the UI,
and the subsets keep the OFL text next to them. The fonts are served by the daemon itself;
no font CDN is used.

The subsets were made once with fonttools `pyftsubset` and brotli (both MIT) in a
throwaway environment. These tools aren't dependencies of this project.

## Frontend dev only

vite (MIT), @vitejs/plugin-react (MIT), typescript (Apache-2.0), vitest (MIT),
jsdom (MIT), oxlint (MIT), @types/* (MIT).

## Release tooling (CI only, never shipped)

| Tool | License | Use |
|---|---|---|
| pip (`pip wheel`) | MIT | builds the wheel and the per-OS wheelhouse |
| sigstore/cosign, sigstore/cosign-installer | Apache-2.0 | keyless signing of `SHA256SUMS` |
| GitHub Actions (checkout, setup-python, setup-node, upload/download-artifact) | MIT | build runners |

Install bundles contain exactly the runtime wheels listed above for one OS (pinned by
`backend/requirements.txt`), plus `LICENSE` and this file. No PyInstaller (GPL with an
exception) and no paid signing.

## Services

- Anthropic API: paid, user-supplied key. This is the only paid component.
- Telegram Bot API (optional, off by default, free): used only if you enable the
  Telegram plugin with your own bot. The plugin uses httpx (already listed) and no
  Telegram SDK.
- No other network services are used.
