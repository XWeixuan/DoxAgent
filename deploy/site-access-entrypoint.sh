#!/bin/sh
set -eu

if [ "${DOXAGENT_SITE_ACCESS_BROWSER_HEADLESS:-true}" = "false" ]; then
  export DISPLAY="${DISPLAY:-:99}"
  display_number=${DISPLAY#:}
  rm -f "/tmp/.X${display_number}-lock" "/tmp/.X11-unix/X${display_number}"
  Xvfb "$DISPLAY" -screen 0 1440x1000x24 -nolisten tcp &
  xvfb_pid=$!
  attempts=0
  while [ ! -S "/tmp/.X11-unix/X${display_number}" ]; do
    if ! kill -0 "$xvfb_pid" 2>/dev/null; then
      wait "$xvfb_pid"
      exit $?
    fi
    attempts=$((attempts + 1))
    if [ "$attempts" -ge 100 ]; then
      echo "Xvfb did not become ready on ${DISPLAY}" >&2
      exit 1
    fi
    sleep 0.1
  done
  # Docker publishes this port on host loopback only; listen on the container
  # interface so an operator can reach it through an SSH tunnel.
  x11vnc -display "$DISPLAY" -forever -shared -listen 0.0.0.0 -rfbport 5900 -nopw &
fi

exec "$@"
