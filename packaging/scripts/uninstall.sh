#!/bin/sh
# Remove the app, its data, its keychain entry and its start-at-login entry.
set -eu
APP=local-assistant  # = APP_ID in backend/contracts/app.py
PREFIX=${PREFIX:-"$HOME/.local/opt/$APP"}
BIN_DIR=${BIN_DIR:-"$HOME/.local/bin"}

if [ ! -x "$PREFIX/venv/bin/python" ]; then
  echo "$APP doesn't seem to be installed in $PREFIX" >&2
  exit 1
fi
# Deletes app data, the API key from the keychain and the login item (asks first; pass --yes to skip).
"$PREFIX/venv/bin/python" -m core.cli uninstall "$@"
if [ -L "$BIN_DIR/$APP" ] && [ "$(readlink "$BIN_DIR/$APP")" = "$PREFIX/venv/bin/$APP" ]; then
  rm "$BIN_DIR/$APP"
fi
rm -rf "$PREFIX/venv" "$PREFIX/uninstall.sh"
rmdir "$PREFIX" 2>/dev/null || true
echo "$APP is uninstalled."
