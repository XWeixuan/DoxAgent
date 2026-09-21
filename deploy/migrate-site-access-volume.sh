#!/bin/bash
set -euo pipefail

volume_name=${DOXAGENT_SITE_STRATEGY_VOLUME:-doxagent-v2_v2-site-strategy}
mountpoint=$(docker volume inspect --format '{{.Mountpoint}}' "$volume_name")
resolved=$(realpath -e "$mountpoint")
case "$resolved" in
  /var/lib/docker/volumes/*/_data) ;;
  *) echo "refusing unexpected Docker volume path: $resolved" >&2; exit 1 ;;
esac

if docker ps --format '{{.Names}}' | grep -Eq '(^|-)v2-site-access(-|$)'; then
  echo "stop v2-site-access before migrating the Profile volume" >&2
  exit 1
fi

install -d -o 10001 -g 10001 -m 0700 \
  "$resolved/registry" "$resolved/profiles" "$resolved/credentials" "$resolved/snapshots"
chown -R 10001:10001 "$resolved/registry" "$resolved/profiles" \
  "$resolved/credentials" "$resolved/snapshots"
find "$resolved/credentials" -type d -exec chmod 0700 {} +
find "$resolved/credentials" -type f -exec chmod 0600 {} +
find "$resolved/profiles" -type d -exec chmod 0700 {} +

echo "Migrated $volume_name at $resolved to Site Access uid/gid 10001."
