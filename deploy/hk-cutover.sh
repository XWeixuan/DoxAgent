#!/bin/bash
set -euo pipefail
cd /root/doxagent
export DOXAGENT_V2_IMAGE=doxagent-v2:hk
compose=(docker compose -p doxagent-v2 -f docker-compose.v2-production.yml -f deploy/docker-compose.hk.yml)
"${compose[@]}" config --quiet
"${compose[@]}" run --rm v2-migrate
volume=$(docker volume inspect doxagent-v2_v2-data --format '{{.Mountpoint}}')
install -d -m 700 "$volume/codex-home"
install -m 600 /root/doxagent-deploy-backup/codex-auth.json "$volume/codex-home/auth.json"
"${compose[@]}" up -d --no-build
python3 - <<'PY'
from pathlib import Path
p=Path('/etc/nginx/conf.d/doxagent-dashboard.conf')
s=p.read_text().replace('/api/dashboard/v1/events','/api/doxagent/v2/').replace('127.0.0.1:8780','127.0.0.1:8082')
p.write_text(s)
PY
nginx -t
systemctl reload nginx
"${compose[@]}" ps
