"""Read-only production HTTP acceptance. Uses Supabase login, never injected auth.

No ticker/profile/intent is created. --ticker additionally verifies a real SSE
keepalive through Nginx; the ticker must already exist in the business database.
"""

import argparse
import getpass
import json
import time
from pathlib import Path

import httpx


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8082")
    parser.add_argument(
        "--login", action="store_true", help="Prompt for actual Supabase credentials"
    )
    parser.add_argument("--ticker")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    results = {"url": args.url, "orders_submitted": 0}
    with httpx.Client(base_url=args.url, trust_env=False, timeout=25) as client:
        assert client.get("/healthz").status_code == 200
        assert client.get("/").status_code == 200
        prefix = "/api/doxagent/v2"
        assert client.get(prefix + "/auth/me").status_code == 401
        response = client.get(prefix + "/auth/config")
        response.raise_for_status()
        config = response.json()["data"]
        assert config["provider"] == "supabase"
        results["public_http"] = "PASS"
        results["unauthenticated_rejected"] = "PASS"
        results["authenticated_api"] = "NOT_RUN"
        results["sse"] = "NOT_RUN"
        if args.login:
            email = input("Supabase email: ").strip()
            password = getpass.getpass("Supabase password: ")
            with httpx.Client(timeout=20) as auth:
                response = auth.post(
                    config["supabase_url"] + "/auth/v1/token?grant_type=password",
                    headers={"apikey": config["supabase_publishable_key"]},
                    json={"email": email, "password": password},
                )
                password = ""
                if response.status_code != 200:
                    raise RuntimeError("Supabase login failed (credentials are not logged)")
                client.headers["Authorization"] = "Bearer " + response.json()["access_token"]
            response = client.get(prefix + "/auth/me")
            response.raise_for_status()
            assert response.json()["data"]["can_operate"]
            context = client.get(prefix + "/read-context", params={"page": "OVERVIEW"})
            context.raise_for_status()
            metrics = client.get(
                prefix + "/overview/metrics", params={"view_id": context.json()["data"]["view_id"]}
            )
            metrics.raise_for_status()
            results["authenticated_api"] = "PASS"
            if args.ticker:
                context = client.get(
                    prefix + "/read-context",
                    params={"page": "MESSAGE_BUS", "ticker": args.ticker, "period": "ALL"},
                )
                context.raise_for_status()
                view_id = context.json()["data"]["view_id"]
                baseline = client.get(
                    prefix + f"/tickers/{args.ticker}/messages", params={"view_id": view_id}
                )
                baseline.raise_for_status()
                cursor = baseline.json()["data"]["stream_cursor"]
                started = time.monotonic()
                with client.stream(
                    "GET",
                    prefix + f"/tickers/{args.ticker}/messages/events",
                    params={"view_id": view_id, "cursor": cursor},
                ) as stream:
                    stream.raise_for_status()
                    assert stream.headers["content-type"].startswith("text/event-stream")
                    for line in stream.iter_lines():
                        if line.startswith(": keepalive"):
                            results["sse"] = "PASS"
                            results["first_keepalive_seconds"] = round(
                                time.monotonic() - started, 2
                            )
                            break
                        if time.monotonic() - started > 22:
                            raise RuntimeError("SSE keepalive was not observed")
                    if results["sse"] != "PASS":
                        raise RuntimeError("SSE ended before keepalive")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
