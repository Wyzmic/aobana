#!/data/data/com.termux/files/usr/bin/bash
# 露草 / Aobana on Android: install or update, in one command, from Termux.
#
#   curl -fsSL https://raw.githubusercontent.com/Wyzmic/aobana/main/termux/install.sh | bash
#
#   AOBANA_DIR=<folder>   where Aobana goes (default /storage/emulated/0/Aobana)
#   AOBANA_SRC=<folder>   take the files from this copy of the repository instead of downloading
set -e

AOBANA_DIR="${AOBANA_DIR:-/storage/emulated/0/Aobana}"
TARBALL="https://codeload.github.com/Wyzmic/aobana/tar.gz/refs/heads/main"
DISTRO="aobana"
PHONE_FILES="app.py engine.py utils.py paths.py library.py indexer.py epub_indexer.py index.html
requirements.txt LICENSE THIRD_PARTY_NOTICES.md data/ruby static"
OLD_CLONE_FILES=".git .gitattributes .gitignore assets release termux Aobana.bat aobana.sh
launcher.py README.md README.ja.md CHANGELOG.md"
PREFIX="${PREFIX:-/data/data/com.termux/files/usr}"

say() { printf '\n== %s\n' "$1"; }

say "Storage permission"
if [ ! -d "$HOME/storage/shared" ]; then
    termux-setup-storage
    for _ in $(seq 1 60); do [ -d "$HOME/storage/shared" ] && break; sleep 1; done
fi
if [ ! -w /storage/emulated/0 ]; then
    echo "Termux cannot write to shared storage. Allow the storage permission and run this again."
    exit 1
fi

say "Termux packages"
pkg update -y
pkg install -y proot-distro curl tar

say "Ubuntu for Aobana (proot-distro container: $DISTRO)"
PD_DIR="$PREFIX/var/lib/proot-distro"
if [ -d "$PD_DIR/containers/$DISTRO/rootfs" ] || [ -d "$PD_DIR/installed-rootfs/$DISTRO" ]; then
    echo "already installed"
else
    proot-distro install --name "$DISTRO" ubuntu:26.04
fi

say "Aobana -> $AOBANA_DIR"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
if [ -n "$AOBANA_SRC" ]; then
    SRC="$AOBANA_SRC"
else
    curl -fsSL "$TARBALL" | tar -xz -C "$WORK"
    SRC="$WORK/aobana-main"
fi
for f in $PHONE_FILES; do
    if [ ! -e "$SRC/$f" ]; then
        echo "$f is missing from the download; nothing was changed."
        exit 1
    fi
done
mkdir -p "$AOBANA_DIR"
if [ -d "$AOBANA_DIR/.git" ]; then
    for f in $OLD_CLONE_FILES; do rm -rf "${AOBANA_DIR:?}/$f"; done
    echo "removed the old full copy of the repository (your databases and media stay)"
fi
for f in $PHONE_FILES; do
    rm -rf "${AOBANA_DIR:?}/$f"
    mkdir -p "$(dirname "$AOBANA_DIR/$f")"
    cp -r "$SRC/$f" "$AOBANA_DIR/$f"
done

say "Python and the pinned packages, inside Ubuntu"
proot-distro login "$DISTRO" -- env DEBIAN_FRONTEND=noninteractive \
    bash -c 'apt-get update && apt-get install -y python3 python3-pip'
proot-distro login "$DISTRO" -- pip3 install --break-system-packages --no-cache-dir \
    -r "$AOBANA_DIR/requirements.txt"

say "Home-screen shortcut (Termux:Widget)"
mkdir -p "$HOME/.shortcuts"
sed "s|^AOBANA_DIR=.*|AOBANA_DIR=\"\${AOBANA_DIR:-$AOBANA_DIR}\"|" \
    "$SRC/termux/aobana-shortcut.sh" > "$HOME/.shortcuts/Aobana"
chmod +x "$HOME/.shortcuts/Aobana"

cat <<EOF

Aobana is installed.

  1. Put the index in $AOBANA_DIR: copy subs.db and epub.db from a PC (fastest), or put
     subtitles in content/字幕 and books in content/書籍 there and press "Index library"
     in the Library tab (slow on a phone).
  2. Add the Termux:Widget widget to your home screen and tap "Aobana".
     It opens in Firefox if it is installed (Yomitan works there), else your default browser.
     If no browser opens, allow Termux "Display over other apps" in Android's settings.
  3. Closing Termux stops the server.

Update: run the same command again.
Uninstall: curl -fsSL https://raw.githubusercontent.com/Wyzmic/aobana/main/termux/uninstall.sh | bash
EOF
