#!/bin/bash
set -euo pipefail

repo_root=${DOXAGENT_REPO_ROOT:-/home/ubuntu/doxagent}
desktop_user=doxagent-desktop
desktop_home=/home/doxagent-desktop
sudoers_target=/etc/sudoers.d/doxagent-site-login
sudoers_temp=$(mktemp)
trap 'rm -f "$sudoers_temp"' EXIT

if ! id "$desktop_user" >/dev/null 2>&1; then
  echo "missing desktop user: $desktop_user" >&2
  exit 1
fi

missing=()
dpkg-query -W -f='${Status}' tigervnc-viewer 2>/dev/null | grep -q 'install ok installed' || missing+=(tigervnc-viewer)
dpkg-query -W -f='${Status}' python3-tk 2>/dev/null | grep -q 'install ok installed' || missing+=(python3-tk)
dpkg-query -W -f='${Status}' python3-gi 2>/dev/null | grep -q 'install ok installed' || missing+=(python3-gi)
dpkg-query -W -f='${Status}' gir1.2-gtk-3.0 2>/dev/null | grep -q 'install ok installed' || missing+=(gir1.2-gtk-3.0)
if ((${#missing[@]})); then
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "${missing[@]}"
fi

install -o root -g root -m 0755 "$repo_root/deploy/site-login-admin.py" \
  /usr/local/sbin/doxagent-site-login-admin
install -o root -g root -m 0755 "$repo_root/deploy/site-login-ui.py" \
  /usr/local/bin/doxagent-site-login-ui
install -o root -g root -m 0755 "$repo_root/deploy/site-login-clipboard-bridge.py" \
  /usr/local/bin/doxagent-site-login-clipboard-bridge

printf '%s\n' \
  'doxagent-desktop ALL=(root) NOPASSWD: /usr/local/sbin/doxagent-site-login-admin *' \
  > "$sudoers_temp"
chmod 0440 "$sudoers_temp"
visudo -cf "$sudoers_temp"
install -o root -g root -m 0440 "$sudoers_temp" "$sudoers_target"
visudo -cf "$sudoers_target"

install -d -o "$desktop_user" -g "$desktop_user" -m 0700 "$desktop_home/Desktop"
rm -f "$desktop_home/Desktop/消息源登录维护.desktop"
install -o "$desktop_user" -g "$desktop_user" -m 0755 \
  "$repo_root/deploy/doxagent-site-login.desktop" \
  "$desktop_home/Desktop/Site Login Maintenance.desktop"

python3 -m py_compile /usr/local/sbin/doxagent-site-login-admin \
  /usr/local/bin/doxagent-site-login-ui \
  /usr/local/bin/doxagent-site-login-clipboard-bridge
echo "site login desktop tool installed"
