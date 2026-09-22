#!/bin/bash
set -euo pipefail

repo_root=${DOXAGENT_REPO_ROOT:-/home/ubuntu/doxagent}
desktop_user=doxagent-desktop
desktop_home=/home/doxagent-desktop

if ! id "$desktop_user" >/dev/null 2>&1; then
  echo "missing desktop user: $desktop_user" >&2
  exit 1
fi
if ! dpkg-query -W -f='${Status}' tigervnc-viewer 2>/dev/null | grep -q 'install ok installed'; then
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends tigervnc-viewer
fi

install -d -o "$desktop_user" -g "$desktop_user" -m 0700 "$desktop_home/Desktop"
install -o "$desktop_user" -g "$desktop_user" -m 0755 \
  "$repo_root/deploy/doxagent-barrons-playwright-chrome-control.desktop" \
  "$desktop_home/Desktop/Barrons Playwright Chrome Test.desktop"

echo "Barrons Playwright Chrome control desktop shortcut installed"
