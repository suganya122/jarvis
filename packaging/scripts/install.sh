#!/bin/sh
# Install from this offline bundle (macOS / Linux). Needs Python 3.12+; no internet, no admin.
#   sh install.sh            installs to ~/.local/opt/local-assistant
#   PREFIX=/some/dir sh install.sh
set -eu
APP=local-assistant  # = APP_ID in backend/contracts/app.py
HERE=$(cd "$(dirname "$0")" && pwd)
PREFIX=${PREFIX:-"$HOME/.local/opt/$APP"}
BIN_DIR=${BIN_DIR:-"$HOME/.local/bin"}

PY=${PYTHON:-}
if [ -z "$PY" ]; then
  for candidate in python3.13 python3.12 python3; do
    if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 12))'; then
      PY=$candidate; break
    fi
  done
fi
if [ -z "$PY" ]; then
  echo "Python 3.12 or newer is needed. Install it (python.org, Homebrew, or your package manager) and run this again." >&2
  exit 1
fi

echo "Installing $APP into $PREFIX"
mkdir -p "$PREFIX"
"$PY" -m venv "$PREFIX/venv"
"$PREFIX/venv/bin/python" -m pip install --quiet --no-index --find-links "$HERE/wheels" "$APP"
cp "$HERE/uninstall.sh" "$PREFIX/uninstall.sh"
mkdir -p "$BIN_DIR"
ln -sf "$PREFIX/venv/bin/$APP" "$BIN_DIR/$APP"

echo
echo "Done. Start it with:  $APP --open"
case ":$PATH:" in *":$BIN_DIR:"*) ;; *) echo "(Add $BIN_DIR to your PATH, or run $BIN_DIR/$APP --open)";; esac
echo "Uninstall later with: sh \"$PREFIX/uninstall.sh\""
