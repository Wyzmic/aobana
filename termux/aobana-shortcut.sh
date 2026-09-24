#!/data/data/com.termux/files/usr/bin/bash
#   AOBANA_BROWSER=<package>   the browser to open (default Firefox, where Yomitan works);
#                              when it is not installed, the default browser opens instead
source /data/data/com.termux/files/usr/etc/profile

AOBANA_DIR="${AOBANA_DIR:-/storage/emulated/0/Aobana}"
DISTRO="aobana"
BROWSER_PKG="${AOBANA_BROWSER:-org.mozilla.firefox}"

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
    exit 0
fi

echo "露草 / Aobana - $URL"
echo "Close Termux to stop the server."
proot-distro login "$DISTRO" -- python3 "$AOBANA_DIR/app.py" &
PID=$!

for _ in $(seq 1 120); do
    up && break
    kill -0 "$PID" 2>/dev/null || { echo "The server stopped; see the messages above."; exit 1; }
    sleep 0.5
done
if up; then open_page; else echo "The server did not answer on port $PORT."; fi
wait "$PID"
