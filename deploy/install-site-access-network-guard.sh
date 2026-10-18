#!/bin/sh
set -eu
repo_root=${DOXAGENT_REPO_ROOT:-/home/ubuntu/doxagent}
install -o root -g root -m 0755 "$repo_root/deploy/site-access-network-guard.py" /usr/local/sbin/doxagent-site-access-network-guard
install -o root -g root -m 0644 "$repo_root/deploy/doxagent-site-access-network-guard.service" /etc/systemd/system/
install -o root -g root -m 0644 "$repo_root/deploy/doxagent-site-access-network-guard.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now doxagent-site-access-network-guard.timer
