#!/bin/sh
set -eu

base=http://127.0.0.1
html=$(wget -q -T 2 -O - "$base/index.html")
assets=$(printf '%s' "$html" | grep -oE '(src|href)="/assets/[^"]+"' | cut -d '"' -f 2)
test -n "$assets"
for asset in $assets; do
  wget -q -T 2 --spider "$base$asset"
done
wget -q -T 2 -O /dev/null "$base/api/doxagent/v2/auth/config"
