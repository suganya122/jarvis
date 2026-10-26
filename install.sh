#!/usr/bin/env bash
# Jarvis: one-line install for macOS and Linux.
#
#   curl -fsSL https://raw.githubusercontent.com/suganya122/jarvis/main/install.sh | bash
#
# What it does (nothing needs admin rights, except installing PortAudio on Linux if missing):
#  1. Works out the system (macOS / Linux, Intel / ARM).
#  2. Gets uv (a Python manager) from its GitHub release, checked against a pinned SHA-256,
#     and a private Python 3.12 with it. Your system Python is left alone.
#  3. Installs Jarvis from this project's latest GitHub Release, checked against SHA256SUMS.
#  4. Asks for your Anthropic API key (stored in a private .env in the app data folder).
#  5. Downloads the speech model and, if you want, the natural voice.
#  6. Sets up window placement, start at login, and starts Jarvis.
# Run it again to update. Uninstall: ~/.local/bin/jarvis-uninstall
#
# For tests/CI: JARVIS_YES=1 (no questions), JARVIS_WHEEL / JARVIS_REQUIREMENTS (local files
# instead of the release), JARVIS_SKIP_VOICE=1, JARVIS_SKIP_START=1, JARVIS_PREFIX.
set -euo pipefail

REPO="${JARVIS_REPO:-suganya122/jarvis}"
NAME="Jarvis"
PREFIX="${JARVIS_PREFIX:-$HOME/.local/share/jarvis-app}"
BIN_DIR="$HOME/.local/bin"
UV_VERSION="0.13.0"
YES="${JARVIS_YES:-0}"

has_tty() { (exec < /dev/tty) 2>/dev/null; }  # a terminal to ask on (not CI, not a pipe)
say() { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*" >&2; }
die() { printf '\033[1;31mxx\033[0m %s\n' "$*" >&2; exit 1; }
ask() {  # ask "Question?" default(y/n) -> 0 for yes. Reads the terminal, since stdin is this script.
  local answer default="$2"
  if [ "$YES" = "1" ] || ! has_tty; then [ "$default" = "y" ]; return; fi
  printf '%s [%s] ' "$1" "$([ "$default" = y ] && echo Y/n || echo y/N)" > /dev/tty
  read -r answer < /dev/tty || answer=""
  case "${answer:-$default}" in [yY]*) return 0 ;; *) return 1 ;; esac
}
sha256() { if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | cut -d' ' -f1; else shasum -a 256 "$1" | cut -d' ' -f1; fi; }
fetch() { curl -fsSL --retry 3 --proto '=https' --tlsv1.2 -o "$2" "$1"; }

# ---- 1. which system ------------------------------------------------------------------
case "$(uname -s)" in
  Darwin) OS=mac ;;
  Linux) OS=linux ;;
  *) die "This installer is for macOS and Linux. On Windows run install.ps1 (see the README)." ;;
esac
case "$(uname -m)" in
  arm64 | aarch64) ARCH=aarch64 ;;
  x86_64 | amd64) ARCH=x86_64 ;;
  *) die "Unsupported processor: $(uname -m)" ;;
esac
say "Installing $NAME for $OS ($ARCH) into $PREFIX"
mkdir -p "$PREFIX" "$BIN_DIR"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# ---- 2. system pieces -----------------------------------------------------------------
if [ "$OS" = linux ]; then
  missing=""
  LDCONFIG="$(command -v ldconfig || echo /sbin/ldconfig)"
  # No grep -q: with pipefail, grep stopping early would fail the whole pipe.
  if ! "$LDCONFIG" -p 2>/dev/null | grep 'libportaudio.so.2' >/dev/null; then missing="$missing portaudio"; fi
  if ! command -v wmctrl >/dev/null 2>&1; then missing="$missing wmctrl"; fi
  if [ -n "$missing" ]; then
    as_root() { if [ "$(id -u)" = 0 ]; then "$@"; elif has_tty; then sudo "$@" < /dev/tty; else sudo -n "$@"; fi; }
    if command -v apt-get >/dev/null 2>&1; then
      install_pkgs() { as_root apt-get update -qq && as_root apt-get install -y $(echo "$missing" | sed 's/portaudio/libportaudio2/'); }
    elif command -v dnf >/dev/null 2>&1; then install_pkgs() { as_root dnf install -y $missing; }
    elif command -v pacman >/dev/null 2>&1; then install_pkgs() { as_root pacman -S --noconfirm $missing; }
    else install_pkgs() { return 1; }; fi
    if ask "Install system packages for the microphone and windows (needs your password:$missing)?" y && install_pkgs; then
      say "Installed:$missing"
    else
      warn "Not installed:$missing. Voice needs PortAudio (e.g. sudo apt install libportaudio2)."
    fi
  fi
else
  if ! xcode-select -p >/dev/null 2>&1; then
    say "macOS needs Apple's command line tools once (to build the small Jarvis.app launcher)."
    xcode-select --install >/dev/null 2>&1 || true
    say "Finish the installer that just opened; waiting for it…"
    until xcode-select -p >/dev/null 2>&1; do sleep 5; done
  fi
fi

# ---- 3. uv and a private Python ---------------------------------------------------------
case "$OS-$ARCH" in
  mac-aarch64) UV_TARGET=aarch64-apple-darwin; UV_SHA=a9c1b29002cf3c83f07fa9cd8a887a3be0107d90e23189721221e7257db8e3d6 ;;
  mac-x86_64) UV_TARGET=x86_64-apple-darwin; UV_SHA=5f44dcbde809b632f47c36fadb241cb4d6f9af71d0c8f172b5d2026d3dde742c ;;
  linux-x86_64) UV_TARGET=x86_64-unknown-linux-gnu; UV_SHA=1468ebd5a5541121837c5a2817b9972ba6090fa6caa3d142620850a47fb75154 ;;
  linux-aarch64) UV_TARGET=aarch64-unknown-linux-gnu; UV_SHA=3ccfb6af6e242433eb552f7d9676abd5412c6595c497e990d9c8cb7b5bd4d2c3 ;;
esac
UV="$PREFIX/uv/uv"
if [ ! -x "$UV" ] || [ "$("$UV" --version 2>/dev/null | cut -d' ' -f2)" != "$UV_VERSION" ]; then
  say "Getting uv $UV_VERSION (Python manager)…"
  fetch "https://github.com/astral-sh/uv/releases/download/$UV_VERSION/uv-$UV_TARGET.tar.gz" "$TMP/uv.tar.gz"
  [ "$(sha256 "$TMP/uv.tar.gz")" = "$UV_SHA" ] || die "uv didn't match its pinned checksum; stopping."
  mkdir -p "$PREFIX/uv" && tar -xzf "$TMP/uv.tar.gz" -C "$TMP"
  cp "$TMP/uv-$UV_TARGET/uv" "$UV" && chmod +x "$UV"
fi
export UV_PYTHON_INSTALL_DIR="$PREFIX/python" UV_NO_CONFIG=1
say "Setting up Python 3.12…"
"$UV" venv --quiet --allow-existing --python 3.12 "$PREFIX/venv"
PY="$PREFIX/venv/bin/python"

# ---- 4. Jarvis itself -------------------------------------------------------------------
if [ -n "${JARVIS_WHEEL:-}" ]; then
  WHEEL="$JARVIS_WHEEL"; REQS="${JARVIS_REQUIREMENTS:?set JARVIS_REQUIREMENTS too}"
else
  BASE="https://github.com/$REPO/releases/latest/download"
  say "Downloading the latest $NAME release…"
  fetch "$BASE/SHA256SUMS" "$TMP/SHA256SUMS" || die "No release found at github.com/$REPO."
  WHEEL_NAME="$(awk '/\.whl$/ {print $2; exit}' "$TMP/SHA256SUMS")"
  [ -n "$WHEEL_NAME" ] || die "The release has no wheel."
  for file in "$WHEEL_NAME" requirements.txt; do
    fetch "$BASE/$file" "$TMP/$file"
    want="$(awk -v f="$file" '$2 == f {print $1}' "$TMP/SHA256SUMS")"
    [ "$(sha256 "$TMP/$file")" = "$want" ] || die "$file didn't match the release checksum; stopping."
  done
  WHEEL="$TMP/$WHEEL_NAME"; REQS="$TMP/requirements.txt"
fi
say "Installing $NAME and its libraries…"
"$UV" pip install --quiet --python "$PY" -r "$REQS"
"$UV" pip install --quiet --python "$PY" --no-deps --reinstall "$WHEEL"

cat > "$BIN_DIR/jarvis" <<EOF
#!/bin/sh
# Start $NAME (or run a setup command: jarvis-cli check-voice, jarvis-cli setup-windows, ...)
exec "$PY" -m core "\$@"
EOF
cat > "$BIN_DIR/jarvis-cli" <<EOF
#!/bin/sh
exec "$PY" -m core.cli "\$@"
EOF
cat > "$BIN_DIR/jarvis-uninstall" <<EOF
#!/bin/sh
# Remove $NAME. Your data (settings, chats, API key file) stays unless you add --all.
"$PY" -m core.cli autostart off >/dev/null 2>&1 || true
pkill -f "$PREFIX/venv/bin/python -m core" 2>/dev/null || true
if [ "\${1:-}" = "--all" ]; then "$PY" -m core.cli uninstall --yes || true; fi
rm -rf "$PREFIX" "$HOME/Applications/$NAME.app"
rm -f "$BIN_DIR/jarvis" "$BIN_DIR/jarvis-cli" "$BIN_DIR/jarvis-uninstall"
echo "$NAME was removed."
EOF
chmod +x "$BIN_DIR/jarvis" "$BIN_DIR/jarvis-cli" "$BIN_DIR/jarvis-uninstall"

# ---- 5. API key, voice --------------------------------------------------------------------
if "$PY" - <<'EOF'
import sys
from core import env_file, paths
values = env_file.load(env_file.find_env_file(paths.data_dir()))
sys.exit(0 if values.get("ANTHROPIC_API_KEY") else 1)
EOF
then
  say "Your API key is already set."
elif [ "$YES" != "1" ] && has_tty; then
  printf 'Paste your Anthropic API key (from console.anthropic.com; hidden while you type), or press Enter to skip: ' > /dev/tty
  read -rs KEY < /dev/tty || KEY=""
  printf '\n' > /dev/tty
  if [ -n "$KEY" ]; then printf '%s\n' "$KEY" | "$PY" -m core.cli set-key || warn "The key wasn't saved."; fi
  unset KEY
fi

if [ "${JARVIS_SKIP_VOICE:-0}" != "1" ]; then
  "$PY" -m core.cli setup-voice || warn "Speech model not installed; run: jarvis-cli setup-voice"
  if ask "Install the natural voice (about 100 MB; Piper, GPL-3.0, run as a separate program)?" y; then
    "$PY" -m core.cli install-voice --yes || warn "Natural voice not installed; it will use the computer's voice."
  fi
fi

# ---- 6. macOS: a small Jarvis.app, so macOS asks for the microphone and Accessibility -----
LAUNCH="$BIN_DIR/jarvis"
if [ "$OS" = mac ]; then
  APP="$HOME/Applications/$NAME.app"
  say "Building $APP…"
  mkdir -p "$APP/Contents/MacOS" "$TMP/swift"
  cat > "$TMP/swift/main.swift" <<EOF
// Starts the assistant as a child process, so macOS attributes the microphone and
// Accessibility to $NAME.app (and asks once), and stops it when this app is quit.
import Foundation
let child = Process()
child.executableURL = URL(fileURLWithPath: "$PY")
child.arguments = ["-m", "core"]
for sig in [SIGTERM, SIGINT] {
    signal(sig, SIG_IGN)
    let source = DispatchSource.makeSignalSource(signal: sig, queue: .main)
    source.setEventHandler { child.terminate(); exit(0) }
    source.resume()
}
child.terminationHandler = { exit(\$0.terminationStatus) }
try child.run()
dispatchMain()
EOF
  cat > "$APP/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleIdentifier</key><string>local.assistant.jarvis</string>
  <key>CFBundleName</key><string>$NAME</string>
  <key>CFBundleExecutable</key><string>$NAME</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>LSUIElement</key><true/>
  <key>NSMicrophoneUsageDescription</key><string>$NAME listens for its wake word and your requests. Speech is recognised on this Mac.</string>
  <key>NSAppleEventsUsageDescription</key><string>$NAME arranges and switches windows when you ask it to.</string>
</dict></plist>
EOF
  swiftc -O "$TMP/swift/main.swift" -o "$APP/Contents/MacOS/$NAME" || die "Couldn't build $NAME.app (are the command line tools installed?)"
  codesign --force --sign - "$APP" >/dev/null 2>&1 || warn "Couldn't sign $NAME.app; macOS may ask more often."
  LAUNCH="$APP/Contents/MacOS/$NAME"
fi

# ---- 7. windows, start at login, start now ------------------------------------------------
"$PY" -m core.cli setup-windows || warn "Window placement isn't set up yet; run: jarvis-cli setup-windows"
"$PY" -m core.cli autostart on --exec "$LAUNCH" >/dev/null || warn "Couldn't set start at login."
if [ "${JARVIS_SKIP_START:-0}" != "1" ]; then
  pkill -f "$PREFIX/venv/bin/python -m core" 2>/dev/null || true
  if [ "$OS" = mac ]; then open "$APP"; else nohup "$LAUNCH" > "$PREFIX/jarvis.log" 2>&1 & fi
  say "$NAME is running."
fi

echo
say "Done. Say \"Jarvis, what time is it?\""
case ":$PATH:" in *":$BIN_DIR:"*) ;; *) echo "   (Add $BIN_DIR to your PATH to use the jarvis commands.)" ;; esac
if [ "$OS" = mac ]; then
  echo "   macOS will ask to use the microphone: click Allow. To arrange windows, also allow $NAME in"
  echo "   System Settings → Privacy & Security → Accessibility."
fi
echo "   Check the microphone: jarvis-cli check-voice     Uninstall: jarvis-uninstall"
