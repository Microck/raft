#!/bin/bash
set -euo pipefail
export DISPLAY=:99
Xvfb :99 -screen 0 1280x720x24 -nolisten tcp -shmem &
xvfb_pid=$!
openbox_pid=
trap 'kill "$xvfb_pid" ${openbox_pid:+"$openbox_pid"} 2>/dev/null || true' EXIT
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
openbox &
openbox_pid=$!
# Type=notify keeps dependent streaming services behind X readiness.
systemd-notify --ready
# Any child exit, including a clean one, means this desktop is no longer usable.
wait -n "$xvfb_pid" "$openbox_pid" || true
exit 1
