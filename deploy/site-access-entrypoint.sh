#!/bin/sh
set -eu

service_uid=10001
service_gid=10001
runtime_dir=/run/doxagent-site-access

if [ "$(id -u)" = 0 ]; then
  install -d -o "$service_uid" -g "$service_gid" -m 0700 "$runtime_dir"
  install -d -o "$service_uid" -g "$service_gid" -m 0700 \
    /site-data /site-data/registry /site-data/profiles /site-data/credentials
  install -d -o "$service_uid" -g "$service_gid" -m 0770 /run/doxagent-chrome
  worker_source=${DOXAGENT_SITE_ACCESS_WORKER_TOKEN_FILE:-/run/secrets/site_access_worker_token}
  admin_source=${DOXAGENT_SITE_ACCESS_ADMIN_TOKEN_FILE:-/run/secrets/site_access_admin_token}
  install -o "$service_uid" -g "$service_gid" -m 0400 "$worker_source" "$runtime_dir/worker-token"
  install -o "$service_uid" -g "$service_gid" -m 0400 "$admin_source" "$runtime_dir/admin-token"
  exec setpriv --reuid="$service_uid" --regid="$service_gid" --init-groups \
    env DOXAGENT_SITE_ACCESS_WORKER_TOKEN_FILE="$runtime_dir/worker-token" \
        DOXAGENT_SITE_ACCESS_ADMIN_TOKEN_FILE="$runtime_dir/admin-token" \
        HOME=/home/siteaccess LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8 \
        /usr/local/bin/site-access-entrypoint "$@"
fi

if [ "$(id -u)" != "$service_uid" ]; then
  echo "Site Access must run as uid ${service_uid}" >&2
  exit 1
fi

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
  x11vnc -display "$DISPLAY" -forever -shared -listen 127.0.0.1 -rfbport 5910 -nopw &
  printf '%s\n' 5910 > /run/doxagent-chrome/vnc-target
  python /usr/local/bin/site-login-vnc-relay.py \
    --target-file /run/doxagent-chrome/vnc-target --port 5900 &
fi

exec "$@"
