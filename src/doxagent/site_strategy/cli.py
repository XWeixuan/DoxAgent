"""Administrative CLI for Site Strategy and the Site Access owner."""

from __future__ import annotations

import argparse
import getpass
import json
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import uvicorn
import yaml

from doxagent.settings import DoxAgentSettings

from .api import create_app
from .credentials import CredentialStore
from .egress import render_fixed_listeners
from .factory import build_site_strategy_service
from .schema import (
    AccessCombination,
    AccessMode,
    AccessRequest,
    BrowserProfile,
    ProxyEgress,
    SitePurpose,
    SiteStrategySpec,
)
from .tokens import read_token


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="doxagent-site-strategy")
    parser.add_argument(
        "--url",
        dest="base_url",
        default=os.getenv("DOXAGENT_SITE_ACCESS_URL", "http://127.0.0.1:8011"),
    )
    parser.add_argument("--token", default=os.getenv("DOXAGENT_SITE_ACCESS_ADMIN_TOKEN"))
    parser.add_argument("--worker-token", default=os.getenv("DOXAGENT_SITE_ACCESS_WORKER_TOKEN"))
    commands = parser.add_subparsers(dest="command", required=True)

    server = commands.add_parser("run-server")
    server.add_argument("--host", default="0.0.0.0")
    server.add_argument("--port", type=int, default=8011)

    resolve = commands.add_parser("resolve")
    resolve.add_argument("url")
    resolve.add_argument("--purpose", choices=("body", "crawler"), default="body")

    site = commands.add_parser("site")
    site_commands = site.add_subparsers(dest="action", required=True)
    site_commands.add_parser("list")
    for action in ("show", "export", "history"):
        item = site_commands.add_parser(action)
        item.add_argument("site_id")
        item.add_argument("--revision", type=int)
    validate = site_commands.add_parser("validate")
    validate.add_argument("file")
    apply = site_commands.add_parser("apply")
    apply.add_argument("file")
    apply.add_argument("--expected-revision", type=int)
    apply.add_argument("--actor", default="cli")
    rollback = site_commands.add_parser("rollback")
    rollback.add_argument("site_id")
    rollback.add_argument("target_revision", type=int)
    rollback.add_argument("expected_revision", type=int)

    egress = commands.add_parser("egress")
    egress_commands = egress.add_subparsers(dest="action", required=True)
    egress_commands.add_parser("list")
    egress_commands.add_parser("status")
    import_egress = egress_commands.add_parser("import")
    import_egress.add_argument("file")
    render = egress_commands.add_parser("render")
    render.add_argument("config")
    render.add_argument("output")
    probe = egress_commands.add_parser("probe")
    probe.add_argument("egress_id")

    combination = commands.add_parser("combination")
    combination_commands = combination.add_subparsers(dest="action", required=True)
    add = combination_commands.add_parser("add")
    add.add_argument("site_id")
    add.add_argument("combination_id")
    add.add_argument("profile_id")
    add.add_argument("egress_id")
    add.add_argument("--priority", type=int, default=100)
    disable = combination_commands.add_parser("disable")
    disable.add_argument("site_id")
    disable.add_argument("combination_id")
    activate = combination_commands.add_parser("activate")
    activate.add_argument("runtime_key")
    activate.add_argument("combination_id")
    activate.add_argument("--expected-generation", type=int)
    status = combination_commands.add_parser("status")
    status.add_argument("runtime_key")
    comb_probe = combination_commands.add_parser("probe")
    comb_probe.add_argument("combination_id")
    comb_probe.add_argument("url")
    comb_probe.add_argument("--purpose", choices=("body", "crawler"), default="crawler")
    comb_probe.add_argument("--mode", choices=("http", "browser"), default="http")

    profile = commands.add_parser("profile")
    profile_commands = profile.add_subparsers(dest="action", required=True)
    create = profile_commands.add_parser("create")
    create.add_argument("profile_id")
    create.add_argument("site_id")
    create.add_argument("egress_id")
    create.add_argument("--login-url")
    create.add_argument("--credential-ref")
    profile_commands.add_parser("status").add_argument("--site-id")
    login_open = profile_commands.add_parser("login-open")
    login_open.add_argument("profile_id")
    verify = profile_commands.add_parser("verify")
    verify.add_argument("profile_id")
    verify.add_argument("--article-url", required=True)
    login_close = profile_commands.add_parser("login-close")
    login_close.add_argument("login_token")

    credential = commands.add_parser("credential")
    credential_commands = credential.add_subparsers(dest="action", required=True)
    for action in ("import", "update"):
        item = credential_commands.add_parser(action)
        item.add_argument("credential_id")
        item.add_argument("--username")
    cred_status = credential_commands.add_parser("status")
    cred_status.add_argument("credential_id")

    stats = commands.add_parser("stats")
    stats.add_argument("--since")
    stats.add_argument("--site-id")
    failures = commands.add_parser("failures")
    failures.add_argument("--site-id")
    events = commands.add_parser("events")
    events.add_argument("--site-id")
    events.add_argument("--since")
    events.add_argument("--limit", type=int, default=200)
    commands.add_parser("generic-report")

    migrate = commands.add_parser("migrate")
    migrate.add_argument("action", choices=("seed", "audit", "reconcile-outcomes"))
    return parser


class AdminClient:
    def __init__(self, url: str, token: str | None) -> None:
        if not token:
            raise SystemExit("--token or DOXAGENT_SITE_ACCESS_ADMIN_TOKEN is required")
        self.client = httpx.Client(
            base_url=url.rstrip("/"),
            headers={"Authorization": f"Bearer {token}"},
            timeout=190,
        )

    def request(self, method: str, path: str, **kwargs: Any) -> Any:
        response = self.client.request(method, path, **kwargs)
        if response.is_error:
            raise SystemExit(f"HTTP {response.status_code}: {response.text}")
        return response.json()


def _load(path: str) -> Any:
    text = Path(path).read_text(encoding="utf-8")
    if Path(path).suffix.casefold() in {".yaml", ".yml"}:
        return yaml.safe_load(text)
    return json.loads(text)


def _dump(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def _site_spec(client: AdminClient, site_id: str) -> dict[str, Any]:
    return dict(client.request("GET", f"/v1/sites/{site_id}"))


def _apply_spec(client: AdminClient, spec: dict[str, Any], expected: int) -> Any:
    spec["revision"] = expected
    return client.request(
        "POST",
        "/v1/sites:apply",
        json={"spec": spec, "expected_revision": expected, "actor": "cli"},
    )


def _run_remote(args: argparse.Namespace) -> Any:
    client = AdminClient(args.base_url, args.token)
    if args.command == "resolve":
        worker = AdminClient(args.base_url, args.worker_token)
        return worker.request("GET", "/v1/resolve", params={"url": args.url})
    if args.command == "site":
        if args.action == "list":
            return client.request("GET", "/v1/sites")
        if args.action in {"show", "export"}:
            params = {"revision": args.revision} if args.revision else None
            return client.request("GET", f"/v1/sites/{args.site_id}", params=params)
        if args.action == "history":
            return client.request("GET", f"/v1/sites/{args.site_id}/history")
        if args.action == "validate":
            validate_model = SiteStrategySpec.model_validate(_load(args.file))
            return client.request(
                "POST",
                "/v1/sites:validate",
                json=validate_model.model_dump(mode="json"),
            )
        if args.action == "apply":
            apply_model = SiteStrategySpec.model_validate(_load(args.file))
            return client.request(
                "POST",
                "/v1/sites:apply",
                json={
                    "spec": apply_model.model_dump(mode="json"),
                    "expected_revision": args.expected_revision,
                    "actor": args.actor,
                },
            )
        return client.request(
            "POST",
            f"/v1/sites/{args.site_id}:rollback",
            json={
                "target_revision": args.target_revision,
                "expected_revision": args.expected_revision,
                "actor": "cli",
            },
        )
    if args.command == "egress":
        if args.action in {"list", "status"}:
            return client.request("GET", "/v1/egresses")
        if args.action == "probe":
            return client.request("POST", f"/v1/egresses/{args.egress_id}:probe")
        if args.action == "import":
            values = _load(args.file)
            if isinstance(values, dict):
                values = [values]
            return [
                client.request(
                    "PUT",
                    f"/v1/egresses/{value['egress_id']}",
                    json=ProxyEgress.model_validate(value).model_dump(mode="json"),
                )
                for value in values
            ]
        render_egresses = [
            ProxyEgress.model_validate(value) for value in client.request("GET", "/v1/egresses")
        ]
        rendered = render_fixed_listeners(
            Path(args.config).read_text(encoding="utf-8"), render_egresses
        )
        Path(args.output).write_text(rendered, encoding="utf-8")
        return {"output": str(Path(args.output).resolve()), "listeners": len(render_egresses)}
    if args.command == "combination":
        if args.action == "status":
            return client.request("GET", f"/v1/combinations/{args.runtime_key}")
        if args.action == "activate":
            return client.request(
                "POST",
                f"/v1/combinations/{args.runtime_key}/{args.combination_id}:activate",
                json={"expected_generation": args.expected_generation},
            )
        if args.action == "probe":
            worker = AdminClient(args.base_url, args.worker_token)
            resolved = worker.request("GET", "/v1/resolve", params={"url": args.url})
            site_data = _site_spec(client, resolved["site_id"])
            excluded = [
                item["id"]
                for item in site_data["access"]["combinations"]
                if item["id"] != args.combination_id
            ]
            request = AccessRequest(
                operation_id=f"manual-probe:{uuid4().hex}",
                purpose=(SitePurpose.BODY if args.purpose == "body" else SitePurpose.CRAWLER),
                url=args.url,
                mode=(AccessMode.BROWSER if args.mode == "browser" else AccessMode.HTTP_PUBLIC),
                excluded_combinations=excluded,
            )
            return worker.request(
                "POST", "/v1/access/execute", json=request.model_dump(mode="json")
            )
        site_data = _site_spec(client, args.site_id)
        revision = int(site_data["revision"])
        combinations = list(site_data["access"]["combinations"])
        if args.action == "add":
            new_combination = AccessCombination(
                id=args.combination_id,
                profile_id=args.profile_id,
                egress_id=args.egress_id,
                priority=args.priority,
            )
            combinations.append(new_combination.model_dump(mode="json", by_alias=True))
        else:
            found = False
            for value in combinations:
                if value.get("id") == args.combination_id:
                    value["enabled"] = False
                    found = True
            if not found:
                raise SystemExit("combination not found")
        site_data["access"]["combinations"] = combinations
        return _apply_spec(client, site_data, revision)
    if args.command == "profile":
        if args.action == "status":
            return client.request("GET", "/v1/profiles", params={"site_id": args.site_id})
        if args.action == "create":
            profile_value = BrowserProfile(
                profile_id=args.profile_id,
                site_id=args.site_id,
                bound_egress_id=args.egress_id,
                directory_key=args.profile_id,
                login_url=args.login_url,
                credential_ref=args.credential_ref,
            )
            return client.request(
                "PUT",
                f"/v1/profiles/{args.profile_id}",
                json=profile_value.model_dump(mode="json"),
            )
        if args.action == "login-open":
            return client.request("POST", f"/v1/profiles/{args.profile_id}/login:open")
        if args.action == "verify":
            return client.request(
                "POST",
                f"/v1/profiles/{args.profile_id}:verify",
                json={"article_url": args.article_url},
            )
        return client.request(
            "POST", "/v1/profiles/login:close", json={"login_token": args.login_token}
        )
    if args.command == "credential":
        if args.action == "status":
            return client.request("GET", f"/v1/credentials/{args.credential_id}")
        username = args.username or input("Username: ")
        password = getpass.getpass("Password: ")
        return client.request(
            "PUT",
            f"/v1/credentials/{args.credential_id}",
            json={"username": username, "password": password},
        )
    if args.command == "stats":
        return client.request(
            "GET", "/v1/stats", params={"since": args.since, "site_id": args.site_id}
        )
    if args.command in {"events", "failures", "generic-report"}:
        params = {
            "site_id": getattr(args, "site_id", None),
            "since": getattr(args, "since", None),
            "limit": getattr(args, "limit", 1000),
        }
        values = client.request("GET", "/v1/events", params=params)
        if args.command == "failures":
            return [value for value in values if "FAILURE" in value["category"]]
        if args.command == "generic-report":
            return [value for value in values if value["site_id"] == "generic"]
        return values
    if args.command == "migrate":
        sites = client.request("GET", "/v1/sites")
        if args.action == "seed":
            return {"seeded": [value["head"]["site_id"] for value in sites]}
        if args.action == "reconcile-outcomes":
            return {"status": "worker outbox reconciliation is automatic"}
        egress_ids = {item["egress_id"] for item in client.request("GET", "/v1/egresses")}
        profile_ids = {item["profile_id"] for item in client.request("GET", "/v1/profiles")}
        errors: list[str] = []
        for item in sites:
            for combo in item["spec"]["access"]["combinations"]:
                if combo["egress_id"] not in egress_ids:
                    errors.append(f"{item['head']['site_id']}: missing egress {combo['egress_id']}")
                if combo["profile_id"] not in profile_ids:
                    errors.append(
                        f"{item['head']['site_id']}: missing profile {combo['profile_id']}"
                    )
        return {"ok": not errors, "errors": errors}
    raise SystemExit("unsupported command")


def main() -> None:
    args = _parser().parse_args()
    if args.command == "run-server":
        settings = DoxAgentSettings()
        worker_token = read_token(
            settings.site_access_worker_token,
            settings.site_access_worker_token_file,
        )
        admin_token = read_token(
            settings.site_access_admin_token,
            settings.site_access_admin_token_file,
        )
        if not worker_token or not admin_token:
            raise SystemExit("Site Access worker/admin tokens are required")
        service = build_site_strategy_service(settings)
        app = create_app(
            service,
            worker_token=worker_token,
            admin_token=admin_token,
            credential_store=CredentialStore(settings.site_access_credential_root),
        )
        uvicorn.run(app, host=args.host, port=args.port)
        return
    _dump(_run_remote(args))


if __name__ == "__main__":
    main()
