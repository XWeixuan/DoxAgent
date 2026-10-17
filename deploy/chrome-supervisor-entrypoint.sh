#!/bin/sh
set -eu

service_uid=10001
service_gid=10001

if [ "$(id -u)" = 0 ]; then
  install -d -o "$service_uid" -g "$service_gid" -m 0770 /run/doxagent-chrome
  install -d -o "$service_uid" -g "$service_gid" -m 0700 \
    /site-data /site-data/profiles /home/siteaccess
  exec setpriv --reuid="$service_uid" --regid="$service_gid" --init-groups \
    env HOME=/home/siteaccess LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8 "$0" "$@"
fi

if [ "$(id -u)" != "$service_uid" ]; then
  echo "Chrome supervisor must run as uid ${service_uid}" >&2
  exit 1
fi

# Keep the namespace alive if only the Python supervisor crashes. Its replacement
# can adopt PID/start-time fenced Chrome instances; a container restart still
# loses native pages and is deliberately not presented as a lossless operation.
stopping=0
supervisor_pid=""
shutdown() {
  stopping=1
  if [ -n "$supervisor_pid" ]; then
    kill -TERM "$supervisor_pid" 2>/dev/null || true
  fi
}
trap shutdown TERM INT
while [ "$stopping" = 0 ]; do
  "$@" &
  supervisor_pid=$!
  wait "$supervisor_pid" || true
  if [ "$stopping" = 1 ]; then
    wait "$supervisor_pid" 2>/dev/null || true
    break
  fi
  sleep 2
done
