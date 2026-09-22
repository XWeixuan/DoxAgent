#!/bin/sh
set -eu

service_uid=10001
service_gid=10001
profile_dir=/chrome-profile
runtime_dir=/run/chrome-control

if [ "$(id -u)" = 0 ]; then
  install -d -o "$service_uid" -g "$service_gid" -m 0700 "$profile_dir" "$runtime_dir"
  exec setpriv --reuid="$service_uid" --regid="$service_gid" --init-groups \
    env HOME=/home/chromecontrol LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8 \
        TZ=America/Los_Angeles \
        /usr/local/bin/barrons-chrome-control-entrypoint "$@"
fi

if [ "$(id -u)" != "$service_uid" ]; then
  echo "Chrome control must run as uid ${service_uid}" >&2
  exit 1
fi

exec 9>"$profile_dir/.control.lock"
if ! flock -n 9; then
  echo "The isolated Chrome Profile already has a writer" >&2
  exit 1
fi

export DISPLAY=:99
rm -f /tmp/.X99-lock /tmp/.X11-unix/X99
Xvfb "$DISPLAY" -screen 0 1440x1000x24 -nolisten tcp &
xvfb_pid=$!

attempts=0
while [ ! -S /tmp/.X11-unix/X99 ]; do
  if ! kill -0 "$xvfb_pid" 2>/dev/null; then
    wait "$xvfb_pid"
    exit $?
  fi
  attempts=$((attempts + 1))
  if [ "$attempts" -ge 100 ]; then
    echo "Xvfb did not become ready" >&2
    exit 1
  fi
  sleep 0.1
done

x11vnc -display "$DISPLAY" -forever -shared -listen 0.0.0.0 -rfbport 5900 -nopw &
vnc_pid=$!

chrome_pid=
shutdown() {
  if [ -n "$chrome_pid" ] && kill -0 "$chrome_pid" 2>/dev/null; then
    kill -TERM "$chrome_pid" 2>/dev/null || true
    remaining=30
    while kill -0 "$chrome_pid" 2>/dev/null && [ "$remaining" -gt 0 ]; do
      sleep 1
      remaining=$((remaining - 1))
    done
    kill -KILL "$chrome_pid" 2>/dev/null || true
  fi
  kill -TERM "$vnc_pid" "$xvfb_pid" 2>/dev/null || true
}
trap shutdown INT TERM EXIT

google-chrome-stable \
  --user-data-dir="$profile_dir" \
  --proxy-server="${CHROME_PROXY_SERVER:-http://doxagent-egress-clash:18081}" \
  --proxy-bypass-list='<-loopback>' \
  --window-size=1440,1000 \
  --no-first-run \
  --no-default-browser-check \
  "${CHROME_START_URL:-https://www.barrons.com/login}" &
chrome_pid=$!
wait "$chrome_pid"
status=$?
chrome_pid=
exit "$status"
