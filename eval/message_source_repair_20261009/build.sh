#!/usr/bin/env bash
set -euo pipefail
cd /home/ubuntu/source-repair-20261009
test "$(sudo -n docker image inspect doxagent-site-access:source-recovery-20261006-r3 -f '{{.Id}}')" = sha256:e0a4204fbb90cc61f1de37b0454903512288361097f7d4a3ad871169d06699cf
test "$(sudo -n docker image inspect doxagent-v2:sweep-priority-20261006 -f '{{.Id}}')" = sha256:f4e02aa610f4382704e0f3167e005312c0d62ab6818b7464c7bcf36b1e89538b
test "$(sudo -n docker image inspect doxagent-v2:market-news-enrich-3ef317a4 -f '{{.Id}}')" = sha256:7efc5969a77b9ed71632ef2a23fdba0e0f860147fcb46b87c92141eafacb193d
test "$(df -B1 --output=avail / | tail -1)" -gt 21474836480
sudo -n docker build --pull=false -f Dockerfile.site -t doxagent-site-access:source-storage-20261009-r1 staging
sudo -n docker build --pull=false -f Dockerfile.bus -t doxagent-v2:source-storage-bus-20261009-r1 staging
sudo -n docker build --pull=false -f Dockerfile.enrich -t doxagent-v2:source-storage-enrich-20261009-r1 staging
