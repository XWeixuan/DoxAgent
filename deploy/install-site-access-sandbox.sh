#!/bin/bash
set -euo pipefail

seccomp_dir=/etc/doxagent
seccomp_target=$seccomp_dir/site-access-seccomp.json
seccomp_url=https://raw.githubusercontent.com/microsoft/playwright/v1.63.0/utils/docker/seccomp_profile.json
seccomp_sha256=cc3e61cabda6bbc1e53e54d27ba4d55a9d3be829b6dd1a596f4a7b31b1cc7849
temporary=$(mktemp)
trap 'rm -f "$temporary"' EXIT

curl -fsSL "$seccomp_url" -o "$temporary"
echo "$seccomp_sha256  $temporary" | sha256sum -c -
python3 -m json.tool "$temporary" >/dev/null
install -d -o root -g root -m 0755 "$seccomp_dir"
install -o root -g root -m 0644 "$temporary" "$seccomp_target"

apparmor_target=/etc/apparmor.d/doxagent-site-access
cat >"$temporary" <<'APPARMOR'
#include <tunables/global>

profile doxagent-site-access flags=(attach_disconnected,mediate_deleted) {
  #include <abstractions/base>
  network,
  capability,
  file,
  umount,
  signal,
  ptrace (trace,read) peer=doxagent-site-access,
  unix,
  userns,
}
APPARMOR
install -o root -g root -m 0644 "$temporary" "$apparmor_target"
apparmor_parser -r "$apparmor_target"

echo "Installed the pinned Site Access seccomp and AppArmor profiles."
