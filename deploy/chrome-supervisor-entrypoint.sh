#!/bin/sh
set -eu

service_uid=10001
service_gid=10001

if [ "$(id -u)" = 0 ]; then
  install -d -o "$service_uid" -g "$service_gid" -m 0770 /run/doxagent-chrome
  install -d -o "$service_uid" -g "$service_gid" -m 0700 \
    /site-data /site-data/profiles /home/siteaccess
  exec setpriv --reuid="$service_uid" --regid="$service_gid" --init-groups \
    env HOME=/home/siteaccess LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8 "$@"
fi

if [ "$(id -u)" != "$service_uid" ]; then
  echo "Chrome supervisor must run as uid ${service_uid}" >&2
  exit 1
fi

exec "$@"
