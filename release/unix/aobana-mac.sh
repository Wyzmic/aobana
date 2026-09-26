#!/bin/bash
CONTENTS="$(cd "$(dirname "$0")/.." && pwd)"
RES="$CONTENTS/Resources"
xattr -dr com.apple.quarantine "$(dirname "$CONTENTS")" 2>/dev/null
"$RES/app/python/bin/python3" "$RES/app/launcher.py" --open-only && exit 0
exec open -a Terminal "$RES/Aobana.command"
