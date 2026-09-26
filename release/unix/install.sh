#!/bin/bash
# 露草 / Aobana for Linux: install for this user, or update. Run it from the unpacked folder:
#
#   ./install.sh
#
# The app goes to ~/.local/share/aobana-app, with a menu entry and the command `aobana`.
# Your library, databases and settings are elsewhere (~/.local/share/aobana) and stay.
set -e
SRC="$(cd "$(dirname "$0")" && pwd)"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
DEST="$DATA/aobana-app"
BIN="$HOME/.local/bin"
APPS="$DATA/applications"
ICONS="$DATA/icons/hicolor/512x512/apps"

if [ "$SRC" != "$DEST" ]; then
    mkdir -p "$DATA"
    rm -rf "$DEST.new"
    cp -a "$SRC" "$DEST.new"
    rm -rf "$DEST"
    mv "$DEST.new" "$DEST"
fi
mkdir -p "$BIN" "$APPS" "$ICONS"
ln -sf "$DEST/aobana" "$BIN/aobana"
cp "$DEST/aobana.png" "$ICONS/aobana.png"
sed "s|@EXEC@|\"$DEST/aobana\"|" "$DEST/aobana.desktop" > "$APPS/aobana.desktop"
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$APPS" 2>/dev/null || true

cat <<MSG

Aobana is installed in $DEST.

  Start it from your applications menu (Aobana), or run: aobana
  It opens in a terminal window; closing that window stops Aobana.

Update: unpack the new version and run its install.sh.
Uninstall: $DEST/uninstall.sh
MSG
case ":$PATH:" in *":$BIN:"*) ;; *) echo "(Add $BIN to your PATH to run \`aobana\` from a terminal.)";; esac
