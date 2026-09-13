"""Isolated paired replay. No business DB, secrets, LLM calls or publication."""

from __future__ import annotations

import argparse
import asyncio
import csv
import gzip
import hashlib
import json
from collections import Counter
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from doxagent.content_enrichment.pipeline import ArticlePipeline
from doxagent.content_enrichment.transport import PublicTransport
from doxagent.monitoring import media_enrichment as legacy


class Capture:
    def __init__(self, session: Any, directory: Path, item_id: str, controller: Any = None) -> None:
        self.session, self.directory, self.item_id = session, directory, item_id
        self.responses: list[dict[str, Any]] = []
        self.controller = controller

    async def get(self, url: str, **kwargs: Any) -> Any:
        if self.controller:
            async with self.controller.enter(url, phase="direct"):
                response = await self.session.get(url, **kwargs)
        else:
            response = await self.session.get(url, **kwargs)
        text = str(response.text or "")
        filename = f"{self.item_id}_{len(self.responses)}.txt.gz"
        (self.directory / filename).write_bytes(gzip.compress(text.encode("utf-8")))
        self.responses.append(
            {
                "url": url,
                "final_url": str(response.url),
                "status": response.status_code,
                "headers": {
                    k: v
                    for k, v in response.headers.items()
                    if k.lower() in {"location", "content-type", "retry-after"}
                },
                "file": filename,
                "sha256": hashlib.sha256(text.encode()).hexdigest(),
            }
        )
        return response


class Recorded:
    def __init__(
        self, directory: Path, responses: list[dict[str, Any]], fallback: Any = None
    ) -> None:
        self.directory, self.responses = directory, responses
        self.fallback = fallback

    async def get(self, url: str, **kwargs: Any) -> Any:
        def normalize(value: str) -> str:
            return value.replace("r.jina.ai/http://https://", "r.jina.ai/https://")

        for item in self.responses:
            if normalize(item["url"]) == normalize(url) or item["final_url"] == url:
                text = gzip.decompress((self.directory / item["file"]).read_bytes()).decode()
                if hashlib.sha256(text.encode()).hexdigest() != item["sha256"]:
                    raise ValueError("fixture_hash_mismatch")
                if (
                    item["url"] == url
                    and item["final_url"] != url
                    and "r.jina.ai/" not in url
                    and not kwargs.get("allow_redirects", True)
                ):
                    return SimpleNamespace(
                        text="", url=url, status_code=302, headers={"location": item["final_url"]}
                    )
                return SimpleNamespace(
                    text=text,
                    url=item["final_url"],
                    status_code=item["status"],
                    headers=item["headers"],
                )
        if self.fallback:
            return await self.fallback.get(url, **kwargs)
        raise ValueError("offline_response_missing")


class OfflineController:
    @asynccontextmanager
    async def enter(self, *args: Any, **kwargs: Any):
        yield "recorded"


async def run(args: argparse.Namespace) -> None:
    rows = list(csv.DictReader(args.input.open(encoding="utf-8-sig")))
    if args.limit:
        rows = rows[: args.limit]
    args.output.mkdir(parents=True, exist_ok=True)
    run_name = args.name or args.mode
    output = args.output / f"{run_name}.jsonl"
    previous = (
        [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
        if output.exists()
        else []
    )
    done = {row["id"] for row in previous}
    baseline_file = args.output / "baseline.jsonl"
    baseline = (
        {
            r["id"]: r
            for r in (
                json.loads(line) for line in baseline_file.read_text(encoding="utf-8").splitlines()
            )
        }
        if baseline_file.exists()
        else {}
    )
    if args.include_supplements:
        for line in (
            (args.output / "candidate-hybrid.jsonl").read_text(encoding="utf-8").splitlines()
        ):
            item = json.loads(line)
            if item["id"] in baseline:
                baseline[item["id"]]["responses"].extend(item.get("responses", []))
    slots = asyncio.Semaphore(args.concurrency)
    controller = legacy.DomainFetchController() if args.mode == "baseline" else OfflineController()
    network_controller = legacy.DomainFetchController()
    async with legacy._default_session_factory()() as session:

        async def item(row: dict[str, str]) -> None:
            item_id = row["standard_message_id"]
            if item_id in done:
                return
            if args.mode != "baseline" and item_id not in baseline:
                return
            async with slots:
                url = row["resolved_url"] or row["url"]
                record = legacy.MediaEnrichmentRecord(
                    item_id,
                    item_id,
                    "replay",
                    row["ticker"],
                    row["title"],
                    "",
                    url,
                    url,
                    row["source_name"],
                )
                capture = Capture(
                    session,
                    args.output,
                    item_id if args.mode == "baseline" else "supplement_" + item_id,
                    network_controller if args.mode == "candidate-hybrid" else None,
                )
                try:
                    async with asyncio.timeout(90):
                        if args.mode in {"baseline", "baseline-offline"}:
                            result = await legacy.extract_media_record(
                                record,
                                capture
                                if args.mode == "baseline"
                                else Recorded(
                                    args.output, baseline.get(item_id, {}).get("responses", [])
                                ),
                                legacy._default_extractor(),
                                fetch_controller=controller,
                            )
                        else:
                            transport = PublicTransport(
                                Recorded(
                                    args.output,
                                    baseline.get(item_id, {}).get("responses", []),
                                    capture if args.mode == "candidate-hybrid" else None,
                                ),
                                controller,
                                validate_urls=args.mode == "candidate-hybrid",
                                trusted_proxy_dns=args.trusted_proxy_dns,
                            )
                            result = await ArticlePipeline(transport).extract(record)
                    data = {
                        "id": item_id,
                        "historical_reason": row["failure_reason"],
                        "succeeded": result.succeeded,
                        "reason": result.reason,
                        "content_chars": len(result.content or ""),
                        "content_sha256": hashlib.sha256(
                            (result.content or "").encode()
                        ).hexdigest(),
                        "final_url": result.final_url,
                        "method": result.extraction_method,
                        "latency_ms": result.latency_ms,
                        "diagnostics": result.diagnostics,
                        "attempts": [a.to_payload() for a in result.attempts],
                        "responses": capture.responses,
                        "fallback_text_missing": True,
                    }
                except Exception as exc:
                    data = {
                        "id": item_id,
                        "succeeded": False,
                        "reason": type(exc).__name__,
                        "responses": capture.responses,
                    }
                with output.open("a", encoding="utf-8") as file:
                    file.write(json.dumps(data, ensure_ascii=False) + "\n")
                previous.append(data)
                if len(previous) % 50 == 0:
                    print(
                        json.dumps(
                            {
                                "completed": len(previous),
                                "accepted": sum(r["succeeded"] for r in previous),
                            }
                        ),
                        flush=True,
                    )

        await asyncio.gather(*(item(row) for row in rows))
    summary = {
        "mode": args.mode,
        "rows": len(previous),
        "accepted_by_program": sum(r["succeeded"] for r in previous),
        "reason_counts": dict(Counter(r.get("reason") or "accepted" for r in previous)),
        "input_sha256": hashlib.sha256(args.input.read_bytes()).hexdigest(),
        "gold_fulltext_verified": False,
        "fallback_text_missing": True,
        "supplemental_responses_reused": args.include_supplements,
        "code_sha256": {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in Path("src/doxagent/content_enrichment").glob("*.py")
        },
    }
    (args.output / f"{run_name}_summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--mode",
        choices=["baseline", "baseline-offline", "candidate-offline", "candidate-hybrid"],
        required=True,
    )
    parser.add_argument("--name")
    parser.add_argument("--trusted-proxy-dns", action="store_true")
    parser.add_argument("--include-supplements", action="store_true")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--concurrency", type=int, default=4)
    asyncio.run(run(parser.parse_args()))
