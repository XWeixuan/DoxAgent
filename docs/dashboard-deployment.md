# DoxAgent Dashboard Deployment

This deployment runs the React dashboard and the real Dashboard State API in one
FastAPI container. Production must use the real backend and Supabase auth; mock
mode remains local-only.

## Service

Build and start on the server from `/root/doxagent`:

```bash
docker compose build dashboard
docker compose up -d --force-recreate dashboard
```

Stop or restart only this service:

```bash
docker compose stop dashboard
docker compose restart dashboard
docker compose logs -f dashboard
```

The container binds to `127.0.0.1:8780:8780`. Nginx is the only public entry.

## Environment

Create `/root/doxagent/.env.dashboard` from `.env.dashboard.example`.
Production values should point at the existing DoxAtlas Supabase project:

```bash
DOXAGENT_DASHBOARD_API_MODE=real
DOXAGENT_DASHBOARD_AUTH_MODE=supabase
DOXAGENT_DASHBOARD_SUPABASE_URL=...
DOXAGENT_DASHBOARD_SUPABASE_PUBLISHABLE_KEY=...
DOXAGENT_DASHBOARD_USER_PROFILES_TABLE=user_profiles
DOXAGENT_DASHBOARD_DEV_TIER=DEVELOPER
```

The regular `/root/doxagent/.env` continues to provide DoxAgent runtime and
database settings such as `DOXAGENT_DATABASE_URL` and `DOXAGENT_STORAGE_MODE`.

## Nginx

Use a dedicated server block for `agent.doxatlas.com`; do not edit the existing
`doxatlas.com` locations except for normal certificate tooling.

```nginx
server {
    listen 80;
    server_name agent.doxatlas.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    server_name agent.doxatlas.com;

    ssl_certificate /etc/letsencrypt/live/agent.doxatlas.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/agent.doxatlas.com/privkey.pem;
    include /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;

    location /api/dashboard/v1/events {
        proxy_pass http://127.0.0.1:8780;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Connection "";
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
        add_header X-Accel-Buffering no;
    }

    location / {
        proxy_pass http://127.0.0.1:8780;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Before issuing the certificate, confirm DNS resolves to the HK server:

```bash
getent hosts agent.doxatlas.com
certbot --nginx -d agent.doxatlas.com
```

## Verification

```bash
curl -fsS http://127.0.0.1:8780/healthz
curl -fsS https://agent.doxatlas.com/healthz
curl -i https://agent.doxatlas.com/api/dashboard/v1/overview
curl -i -N https://agent.doxatlas.com/api/dashboard/v1/events?once=true
```

The unauthenticated API and SSE checks should return `401 UNAUTHORIZED`.
Authenticated dev checks require a real Supabase access token for a
`user_profiles.tier = 'DEVELOPER'` user.
