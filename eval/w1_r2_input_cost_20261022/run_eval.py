"""Frozen W1R2 paired replay. Credentials are process environment only. No runtime writes."""

import argparse
import hashlib
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import httpx
import tiktoken
from openai import OpenAI

from doxagent.persistent_runtime_v2.bailian_chat import chat_body
from doxagent.persistent_runtime_v2.prompts import RuntimeV2PromptSet
from doxagent.persistent_runtime_v2.schema import W1NoveltyResult
from doxagent.persistent_runtime_v2.transport import RuntimeResponsesRequest, _cache_optimized_input
from doxagent.persistent_runtime_v2.w1_r2_context import build_input
from doxagent.settings import DoxAgentSettings

ROOT = Path(__file__).parent
SOURCE = ROOT.parent / "w12_r2_model_low_20261017"
MODEL = "ZHIPU/GLM-5.3-FlashX"
ENC = tiktoken.get_encoding("cl100k_base")


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def write(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf8")


def tokens(text):
    return len(ENC.encode(text))


def prepare():
    source_text = (SOURCE / "dataset.json").read_text(encoding="utf8")
    cases = json.loads(source_text)["cases"]
    old_calls = {
        r["case_id"]: r
        for line in (SOURCE / "run01/calls.jsonl").read_text(encoding="utf8").splitlines()
        if (r := json.loads(line))["arm"] == "W1"
    }
    # Change only format instructions, preserving the frozen business rules and exact contract.
    current = RuntimeV2PromptSet.load().w1_r2
    format_description = current.split("上一轮已经", 1)[1].split("你的任务不是", 1)[0]
    format_description = "上一轮已经" + format_description
    cohort = []
    for case in cases:
        frozen = case["nodes"]["W1"]["frozen"]
        old_system = old_calls[case["case_id"]]["provider_response"]["instructions"]
        old_input, _ = _cache_optimized_input(
            frozen["payload"], tuple(frozen["cache_context_keys"])
        )
        assert sha(old_input) == old_calls[case["case_id"]]["wire_input_sha256"]
        assert sha(old_system) == old_calls[case["case_id"]]["wire_instructions_sha256"]
        before, tail = old_system.split("上一轮已经", 1)
        _, after = tail.split("你的任务不是", 1)
        new_system = before + format_description + "你的任务不是" + after
        payload, diagnostic = build_input(
            frozen["payload"]["source_message"], frozen["payload"]["event_details"]
        )
        new_input, _ = _cache_optimized_input(payload, ("known_fact_view",))
        requests = {}
        for arm, system, content in [
            ("original", old_system, old_input),
            ("optimized", new_system, new_input),
        ]:
            request = RuntimeResponsesRequest(
                instructions="unused",
                payload=payload if arm == "optimized" else frozen["payload"],
                cache_context_keys=("known_fact_view",)
                if arm == "optimized"
                else tuple(frozen["cache_context_keys"]),
                output_model=W1NoveltyResult,
                schema_name="w1_novelty_result",
                model=MODEL,
                reasoning_effort="high",
                timeout_seconds=120,
            )
            body, _ = chat_body(request)
            body["messages"][0]["content"] = system
            assert body["messages"][-1]["content"] == content
            requests[arm] = body
        system_contract = old_system.split("# Exact Output Contract", 1)[1]
        details = frozen["payload"]["event_details"]
        cohort.append(
            dict(
                ordinal=case["ordinal"],
                case_id=case["case_id"],
                ticker=case["ticker"],
                title=case["title"],
                requests=requests,
                details=details,
                diagnostics=diagnostic,
                baseline=case["nodes"]["W1"]["baseline"],
                token_estimates=dict(
                    original_system=tokens(old_system),
                    optimized_system=tokens(new_system),
                    output_contract=tokens(system_contract),
                    original_user=tokens(old_input),
                    optimized_user=tokens(new_input),
                    original_facts=tokens(
                        json.dumps(details, ensure_ascii=False, separators=(",", ":"))
                    ),
                    optimized_facts=tokens(payload["known_fact_view"]),
                    original_body=tokens(frozen["payload"]["source_message"].get("body") or ""),
                    optimized_body=tokens(payload["source_message"].get("body") or ""),
                ),
                hashes={
                    arm: {m["role"]: sha(m["content"]) for m in req["messages"]}
                    for arm, req in requests.items()
                },
            )
        )
    write(ROOT / "cohort.json", dict(source_sha256=sha(source_text), cases=cohort))
    assert len({c["hashes"]["original"]["system"] for c in cohort}) == 2
    assert len({c["hashes"]["optimized"]["system"] for c in cohort}) == 2
    return cohort


def validate(text, details):
    output = W1NoveltyResult.model_validate_json(text)
    canonical = {
        e["event_id"]: {f["fact_id"] for f in e["facts"]} for e in details["canonical_events"]
    }
    provisional = {e["provisional_event_id"] for e in details["provisional_events"]}
    assert set(output.reference_ids) <= set(canonical) | provisional, "Unknown reference Event"
    for attribution in output.fact_attributions or []:
        if attribution.event_id in provisional:
            assert not any("provisional" not in f.lower() for f in attribution.fact_ids), (
                "Invented provisional Fact"
            )
        else:
            assert set(attribution.fact_ids) <= canonical.get(attribution.event_id, set()), (
                "Unknown canonical Fact"
            )
    return output.model_dump(mode="json")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--arm", choices=["prepare", "original", "optimized", "explicit"], required=True
    )
    parser.add_argument("--ordinals", help="Optional comma-separated subset")
    parser.add_argument(
        "--label", default="", help="Separate receipt namespace; never overwrites first attempts"
    )
    args = parser.parse_args()
    cohort = prepare()
    if args.arm == "prepare":
        print(
            json.dumps(
                dict(cases=len(cohort), token_estimates=[c["token_estimates"] for c in cohort])
            )
        )
        return
    endpoint = DoxAgentSettings().dashscope_chat_base_url
    # Match the previous GLM evaluation endpoint; don't silently switch workspaces.
    client = OpenAI(
        api_key=os.environ["DOXAGENT_BAILIAN_GLM_API_KEY"],
        base_url=endpoint,
        max_retries=0,
        timeout=httpx.Timeout(120, connect=15, write=30, pool=30),
    )
    write(
        ROOT / "config.json",
        dict(
            model=MODEL,
            effort="high",
            endpoint=endpoint,
            stream=True,
            max_tokens=16384,
            timeout_seconds=120,
            concurrency=15,
            automatic_retries=0,
            serial_ordinals=[1, 2, 3, 5, 7, 8],
            tokenizer="cl100k_base estimate only",
            input_view="w1-r2-fact-view-v1",
            no_production_writes=True,
            price_cny_per_million=dict(input=2, cached=0.57, output=7),
        ),
    )
    lock = threading.Lock()

    def call(case):
        arm = args.arm
        namespace = arm + ("-" + args.label if args.label else "")
        target = ROOT / "calls" / f"{namespace}-{case['ordinal']:02}.json"
        if target.exists():
            return json.loads(target.read_text(encoding="utf8"))
        body = json.loads(json.dumps(case["requests"]["optimized" if arm == "explicit" else arm]))
        if arm == "explicit":
            system = body["messages"][0]["content"]
            body["messages"][0]["content"] = [
                dict(type="text", text=system, cache_control=dict(type="ephemeral"))
            ]
        write(target.with_name(target.stem + "-request.json"), body)
        record = {k: case[k] for k in ("ordinal", "case_id", "ticker", "title")}
        record.update(arm=arm, started_at=datetime.now(UTC).isoformat(), status="ERROR")
        start = time.perf_counter()
        first = last = answer_first = None
        content, reasoning, chunks = [], [], []
        usage, finish = None, None
        try:
            with client.chat.completions.create(**body) as stream:
                for chunk in stream:
                    elapsed = time.perf_counter() - start
                    chunks.append(dict(at_seconds=elapsed, chunk=chunk.model_dump(mode="json")))
                    if chunk.usage:
                        usage = chunk.usage.model_dump(mode="json", exclude_none=True)
                    for choice in chunk.choices:
                        if choice.finish_reason:
                            finish = choice.finish_reason
                        thought = getattr(choice.delta, "reasoning_content", None) or ""
                        answer = choice.delta.content or ""
                        if thought or answer:
                            first = elapsed if first is None else first
                            last = elapsed
                        if answer:
                            answer_first = elapsed if answer_first is None else answer_first
                        content.append(answer)
                        reasoning.append(thought)
            record["output"] = validate("".join(content), case["details"])
            assert finish == "stop", "Incomplete generation"
            record["status"] = "OK"
        except Exception as exc:
            record.update(
                error_type=type(exc).__name__,
                error=str(exc)[:1500],
                http_status=getattr(exc, "status_code", None),
            )
        finally:
            record.update(
                wall_seconds=time.perf_counter() - start,
                first_token_seconds=first,
                first_answer_seconds=answer_first,
                last_token_seconds=last,
                usage=usage,
                finish_reason=finish,
                raw_output="".join(content),
                reasoning_text="".join(reasoning),
            )
            out = (usage or {}).get("completion_tokens")
            record["generation_tps_including_reasoning"] = (
                out / (last - first)
                if out and first is not None and last and last > first
                else None
            )
            write(target, record)
            write(target.with_name(target.stem + "-chunks.json"), chunks)
            with lock:
                print(
                    json.dumps(
                        {
                            k: record.get(k)
                            for k in (
                                "ordinal",
                                "arm",
                                "status",
                                "wall_seconds",
                                "http_status",
                                "error",
                            )
                        }
                    ),
                    flush=True,
                )
        return record

    try:
        if args.ordinals:
            requested = {int(n) for n in args.ordinals.split(",")}
            for case in cohort:
                if case["ordinal"] in requested:
                    call(case)
            return
        serial = [c for c in cohort if c["ordinal"] in {1, 2, 3, 5, 7, 8}]
        assert len({c["hashes"]["optimized"]["system"] for c in serial}) == 1
        for case in serial:
            result = call(case)
            if result.get("http_status") in {400, 401, 403, 404}:
                return
        with ThreadPoolExecutor(max_workers=15) as pool:
            list(pool.map(call, [c for c in cohort if c not in serial]))
    finally:
        client.close()


if __name__ == "__main__":
    main()
