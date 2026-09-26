#!/bin/bash
# 露草 / Aobana for Linux: remove the app, its menu entry and the `aobana` command.
# Your library, databases and settings (~/.local/share/aobana) are kept; delete that folder
# yourself if you want them gone.
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
rm -f "$HOME/.local/bin/aobana" "$DATA/applications/aobana.desktop" "$DATA/icons/hicolor/512x512/apps/aobana.png"
rm -rf "$DATA/aobana-app"
echo "Aobana is uninstalled. Your library and databases are still in $DATA/aobana."
