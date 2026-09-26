#!/bin/bash
RES="$(cd "$(dirname "$0")" && pwd)"
printf '\033]0;露草 / Aobana\007'
clear
exec "$RES/app/python/bin/python3" "$RES/app/launcher.py"
