"""Native OpenAI R2 transport; orchestration owns all retries and fallback."""
import copy
import json
from time import perf_counter

import httpx
from pydantic import ValidationError

from .transport import RuntimeResponsesError, RuntimeResponsesResult, _validation_error_summary

ENDPOINT = "https://api.openai.com/v1/responses"


def build_request(request):
    from .openrouter import build_case_schema_request
    if (request.model != "gpt-6-luna" or request.metadata.get("round") != "R2"
        or request.metadata.get("lane") not in {"W1", "W2"}
        or request.provider_endpoint not in {None, ENDPOINT}):
        raise RuntimeResponsesError("provider_route_invalid", "OpenAI route requires W1/W2 Luna R2", retryable=False)
    original, fingerprint = build_case_schema_request(request)
    messages = []
    for message in original["messages"]:
        if isinstance(message["content"], str):
            content = [{"type": "input_text", "text": message["content"]}]
        else:
            content = []
            for block in message["content"]:
                item = {"type": "input_text", "text": block["text"]}
                if "cache_control" in block:
                    item["prompt_cache_breakpoint"] = {"mode": "explicit", "ttl": request.cache_ttl or "30m"}
                content.append(item)
        messages.append({"role": message["role"], "content": content})
    schema = copy.deepcopy(original["response_format"]["json_schema"]["schema"])
    def tighten(value):
        if isinstance(value, dict):
            if isinstance(value.get("properties"), dict):
                value["required"] = list(value["properties"])
            for child in value.values():tighten(child)
        elif isinstance(value, list):
            for child in value:tighten(child)
    tighten(schema)
    return dict(model=request.model, reasoning={"effort": request.reasoning_effort or "high"},
        service_tier=request.metadata.get("service_tier", "fast"), input=messages,
        text={"format": {"type": "json_schema", "name": request.schema_name, "strict": True, "schema": schema}},
        prompt_cache_options={"mode": "explicit", "ttl": request.cache_ttl or "30m"},
        prompt_cache_key="w12-"+fingerprint[:56], max_output_tokens=65536, stream=True, store=False), fingerprint


class OpenAIPlatformClient:
    def __init__(self, api_key, *, client=None):
        self._api_key = api_key
        self._client = client or httpx.Client()

    def close(self):self._client.close()

    def complete(self, request):
        body, fingerprint = build_request(request)
        if not self._api_key:
            raise RuntimeResponsesError("provider_auth_failed", "OPENAI_API_KEY is missing", retryable=False)
        started = perf_counter();pieces = [];response = {};request_id = None
        def receipt():
            usage = response.get("usage") or {};details = usage.get("input_tokens_details") or {}
            return dict(response_id=response.get("id"),raw_output="".join(pieces),
                input_tokens=usage.get("input_tokens"),output_tokens=usage.get("output_tokens"),
                reasoning_tokens=(usage.get("output_tokens_details") or {}).get("reasoning_tokens"),
                cached_input_tokens=details.get("cached_tokens"),cache_write_input_tokens=details.get("cache_write_tokens"),
                provider_usage={**usage,"service_tier":response.get("service_tier"),
                    "response_model":response.get("model"),"response_status":response.get("status"),
                    "request_id":request_id,"cache_ttl":body["prompt_cache_options"]["ttl"]})
        try:
            with self._client.stream("POST",ENDPOINT,headers={"Authorization":"Bearer "+self._api_key},
                    json=body,timeout=request.timeout_seconds or 300) as stream:
                request_id = stream.headers.get("x-request-id")
                if stream.status_code != 200:
                    try:error = json.loads(stream.read()).get("error") or {}
                    except (ValueError,TypeError):error = {}
                    status = stream.status_code
                    code = ("provider_balance_unavailable" if error.get("code")=="insufficient_quota" or status==402 else
                        "provider_auth_failed" if status in {401,403} else
                        "provider_rate_limited" if status==429 else "provider_request_failed")
                    try:retry_after = max(0,float(stream.headers.get("retry-after",0)))
                    except ValueError:retry_after = 0
                    raise RuntimeResponsesError(code,f"OpenAI request failed: HTTP {status}; code={error.get('code') or error.get('type') or 'unknown'}",
                        retryable=status==429 or status>=500,retry_after_seconds=retry_after,usage=receipt())
                for line in stream.iter_lines():
                    if not line.startswith("data:"):continue
                    raw = line[5:].strip()
                    if not raw or raw=="[DONE]":continue
                    event = json.loads(raw);kind = event.get("type")
                    if kind=="response.output_text.delta":pieces.append(event["delta"])
                    if isinstance(event.get("response"),dict):response = event["response"]
                    if kind=="error":
                        raise RuntimeResponsesError("provider_stream_failed", "OpenAI stream returned an error",retryable=True,usage=receipt())
        except RuntimeResponsesError:raise
        except httpx.TimeoutException as exc:
            raise RuntimeResponsesError("provider_timeout","OpenAI request timed out",retryable=True,usage=receipt()) from exc
        except httpx.TransportError as exc:
            raise RuntimeResponsesError("provider_request_failed","OpenAI transport failed",retryable=True,usage=receipt()) from exc
        except (ValueError,TypeError,KeyError) as exc:
            raise RuntimeResponsesError("provider_stream_failed","Invalid OpenAI stream",retryable=True,usage=receipt()) from exc
        observed = receipt()
        if not response.get("id") or response.get("status")!="completed" or not observed["raw_output"].strip():
            raise RuntimeResponsesError("incomplete_structured_output","OpenAI response incomplete",retryable=True,usage=observed)
        try:value = request.output_model.model_validate_json(observed["raw_output"])
        except (ValidationError,ValueError) as exc:
            raise RuntimeResponsesError("structured_output_validation_failed",_validation_error_summary(exc),retryable=True,usage=observed) from exc
        return RuntimeResponsesResult(value=value,latency_ms=round((perf_counter()-started)*1000),
            prefix_fingerprint=fingerprint,**observed)
