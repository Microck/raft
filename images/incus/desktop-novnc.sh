#!/bin/bash
set -euo pipefail
export DISPLAY=:99
# This dedicated Xvfb display has no login manager to wait for.
X11VNC_AVOID_WINDOWS=never x11vnc -display :99 -localhost -nopw -forever -shared -rfbport 5900 &
vnc_pid=$!
websockify_pid=
trap 'kill "$vnc_pid" ${websockify_pid:+"$websockify_pid"} 2>/dev/null || true' EXIT
websockify --web=/usr/share/novnc 0.0.0.0:6081 127.0.0.1:5900 &
websockify_pid=$!
# A failed VNC server must not leave an apparently healthy web frontend running.
wait -n "$vnc_pid" "$websockify_pid" || true
exit 1
