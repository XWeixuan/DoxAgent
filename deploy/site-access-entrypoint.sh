#!/bin/sh
set -eu

if [ "${DOXAGENT_SITE_ACCESS_BROWSER_HEADLESS:-true}" = "false" ]; then
  export DISPLAY="${DISPLAY:-:99}"
  Xvfb "$DISPLAY" -screen 0 1440x1000x24 -nolisten tcp &
  # Docker publishes this port on host loopback only; listen on the container
  # interface so an operator can reach it through an SSH tunnel.
  x11vnc -display "$DISPLAY" -forever -shared -listen 0.0.0.0 -rfbport 5900 -nopw &
fi

exec "$@"
