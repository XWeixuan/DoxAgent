#!/usr/bin/env bash
set -euo pipefail
cd /home/ubuntu/doxagent
sudo -n docker compose --env-file .env.v2 -p doxagent-v2 \
  -f docker-compose.v2-production.yml -f deploy/docker-compose.server.yml \
  -f /home/ubuntu/barrons_ticker_fix_20260930/compose.override.yml \
  -f /home/ubuntu/source-repair-20261004/eval/message_source_repair_20261004/compose.override.yml \
  -f /home/ubuntu/source-repair-20261006/compose.override.yml \
  -f /home/ubuntu/source-repair-20261009/rollback.override.yml \
  up -d --no-deps --no-build v2-site-access v2-message-bus v2-content-enrichment
