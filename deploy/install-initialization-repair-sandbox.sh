#!/bin/bash
set -euo pipefail
repair_deploy_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repair_profile_target=/etc/apparmor.d/doxagent-initialization-repair
install -o root -g root -m 0644 "$repair_deploy_dir/initialization-repair.apparmor" "$repair_profile_target"
apparmor_parser -r "$repair_profile_target"
echo "Installed the initialization repair Agent AppArmor profile."
