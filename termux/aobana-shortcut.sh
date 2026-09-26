#!/data/data/com.termux/files/usr/bin/bash
#   AOBANA_BROWSER=<package>   the browser to open (default Firefox, where Yomitan works);
#                              when it is not installed, the default browser opens instead
source /data/data/com.termux/files/usr/etc/profile

main() {
AOBANA_DIR="${AOBANA_DIR:-/storage/emulated/0/Aobana}"
DISTRO="aobana"
BROWSER_PKG="${AOBANA_BROWSER:-org.mozilla.firefox}"
INSTALL_URL="https://raw.githubusercontent.com/Wyzmic/aobana/main/termux/install.sh"
UPDATE_EXIT_CODE=75

PORT="$AOBANA_PORT"
if [ -z "$PORT" ] && [ -f "$AOBANA_DIR/config.json" ]; then
    PORT=$(sed -n 's/.*"port"[[:space:]]*:[[:space:]]*\([0-9][0-9]*\).*/\1/p' "$AOBANA_DIR/config.json" | head -n 1)
fi
PORT="${PORT:-5000}"
URL="http://127.0.0.1:$PORT/"

up() { (exec 3<>"/dev/tcp/127.0.0.1/$PORT") 2>/dev/null; }

open_page() {
    if am start -a android.intent.action.VIEW -d "$URL" -p "$BROWSER_PKG" 2>&1 | grep -qi "error"; then
        termux-open-url "$URL"
    fi
}

if up; then
    open_page
    return 0
fi

first=1
while :; do
    proot-distro login "$DISTRO" -- env AOBANA_TERMUX=1 AOBANA_UPDATER=1 \
        python3 "$AOBANA_DIR/app.py" &
    PID=$!

    for _ in $(seq 1 120); do
        up && break
        kill -0 "$PID" 2>/dev/null || break
        sleep 0.5
    done
    if up; then
        [ "$first" = 1 ] && open_page
    elif kill -0 "$PID" 2>/dev/null; then
        echo "The server did not answer on port $PORT."
    fi
    first=0

    wait "$PID"
    code=$?
    if [ "$code" != "$UPDATE_EXIT_CODE" ]; then
        [ "$code" = 0 ] || echo "The server stopped; see the messages above."
        return "$code"
    fi
    printf '\n== Updating Aobana\n'
    if ! curl -fsSL "$INSTALL_URL" | AOBANA_DIR="$AOBANA_DIR" bash; then
        echo "The update did not finish (see above); starting the version that is installed."
    fi
done
}

main "$@"; exit
