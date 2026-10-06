#!/bin/sh
set -eu
export DISPLAY=:99
Xvfb :99 -screen 0 1280x720x24 -nolisten tcp &
xvfb_pid=$!
trap 'kill "$xvfb_pid" 2>/dev/null || true' EXIT
# Start clients as soon as X accepts requests, rather than sleeping on every launch.
attempt=0
until xdpyinfo >/dev/null 2>&1; do
  attempt=$((attempt + 1))
  if [ "$attempt" -ge 100 ]; then
    echo 'Xvfb did not become ready within five seconds' >&2
    exit 1
  fi
  sleep 0.05
done
runuser -u developer -- env DISPLAY=:99 openbox &
# This dedicated Xvfb display has no login manager to wait for.
X11VNC_AVOID_WINDOWS=never x11vnc -display :99 -localhost -nopw -forever -shared -rfbport 5900 &
websockify --web=/usr/share/novnc 0.0.0.0:6080 127.0.0.1:5900 &
wait
