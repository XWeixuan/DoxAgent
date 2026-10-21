"""Native Vertex Claude adapter. Orchestration owns every retry and provider choice."""

from __future__ import annotations

import json
from dataclasses import replace
from threading import Lock
from time import perf_counter

import httpx

from .openrouter import build_w1_r1_cached_request, build_w2_r1_cached_request
from .transport import RuntimeResponsesError, RuntimeResponsesResult, _validation_error_summary


def vertex_body(request):
    if request.metadata.get("round") != "R1" or request.metadata.get("lane") not in {"W1", "W2"}:
        raise RuntimeResponsesError(
            "provider_route_invalid", "Vertex recall supports W1R1/W2R1", retryable=False
        )
    if request.previous_response_id or request.inherit_context or request.replay_parent:
        raise RuntimeResponsesError(
            "provider_route_invalid", "Vertex recall requires explicit context", retryable=False
        )
    builder = (
        build_w1_r1_cached_request
        if request.metadata["lane"] == "W1"
        else build_w2_r1_cached_request
    )
    original, fingerprint = builder(replace(request, model="anthropic/claude-haiku-5.5"))
    body = {
        "anthropic_version": "vertex-2023-10-16",
        "max_tokens": original["max_tokens"],
        "system": original["messages"][0]["content"],
        "messages": original["messages"][1:],
        "stream": True,
        "thinking": {"type": "adaptive"},
        "output_config": {
            "effort": request.reasoning_effort or "high",
            "format": {
                "type": "json_schema",
                "schema": original["response_format"]["json_schema"]["schema"],
            },
        },
    }
    return body, fingerprint


def merge_usage(current, update):
    """SSE snapshots are cumulative. Replace values, never sum repeated input counts."""
    for key, value in update.items():
        if isinstance(value, dict):
            nested = dict(current.get(key) or {})
            nested.update(value)
            current[key] = nested
        else:
            current[key] = value


class VertexClaudeClient:
    def __init__(self, project, *, client=None, token_provider=None, api_key=None):
        self.project = project
        self.client = client or httpx.Client()
        self.token_provider = token_provider
        # API-key availability is endpoint/account dependent. Never silently fall
        # back to ADC or OpenRouter after an explicit credential is rejected.
        self.api_key = api_key
        self.credentials = None
        self.lock = Lock()

    def token(self):
        if self.token_provider:
            return self.token_provider()
        import google.auth
        from google.auth.transport.requests import Request

        with self.lock:
            if self.credentials is None:
                self.credentials, _ = google.auth.default(
                    scopes=["https://www.googleapis.com/auth/cloud-platform"]
                )
            if not self.credentials.valid:
                self.credentials.refresh(Request())
            return self.credentials.token

    def complete(self, request):
        project = request.metadata.get("vertex_project") or self.project
        if not project or request.model != "claude-haiku-5-5":
            raise RuntimeResponsesError(
                "provider_route_invalid", "Vertex project/model is not configured", retryable=False
            )
        location = request.provider_endpoint or "global"
        if location != "global":
            # Regional endpoints have a distinct tariff. Only explicitly named regions are allowed.
            if location not in {"us-east5", "europe-west1", "asia-southeast1", "asia-east1"}:
                raise RuntimeResponsesError(
                    "provider_route_invalid", "Unsupported Vertex location", retryable=False
                )
            host = location + "-aiplatform.googleapis.com"
        else:
            host = "aiplatform.googleapis.com"
        url = f"https://{host}/v1/projects/{project}/locations/{location}/publishers/anthropic/models/{request.model}:streamRawPredict"
        body, fingerprint = vertex_body(request)
        started = perf_counter()
        native, content, response_id, stopped = {}, [], "", False
        output_final, stop_reason = False, None

        def receipt():
            from doxagent.v2_read.pricing import normalize

            raw = {
                "usage": native,
                "response_provider": "Google",
                "project": project,
                "region": location,
                "output_complete": output_final and (stopped or stop_reason is not None),
            }
            normalized = normalize("vertex", {"provider_usage": raw}, ttl=request.cache_ttl)
            return {
                "input_tokens": normalized["input_tokens"],
                "output_tokens": normalized["output_tokens"],
                "cached_input_tokens": normalized["cached_input_tokens"],
                "cache_write_input_tokens": normalized["cache_write_input_tokens"],
                "provider_usage": raw,
                "response_id": response_id or None,
            }

        try:
            headers = (
                {"x-goog-api-key": self.api_key}
                if self.api_key
                else {"Authorization": "Bearer " + self.token()}
            )
            with self.client.stream(
                "POST",
                url,
                headers=headers,
                json=body,
                timeout=request.timeout_seconds or 180,
            ) as response:
                if response.is_error:
                    response.read()
                response.raise_for_status()
                for line in response.iter_lines():
                    if not line.startswith("data:"):
                        continue
                    event = json.loads(line[5:])
                    if event.get("type") == "message_start":
                        message = event.get("message") or {}
                        response_id = message.get("id") or response_id
                        merge_usage(native, message.get("usage") or {})
                    elif event.get("type") == "message_delta":
                        merge_usage(native, event.get("usage") or {})
                        output_final = "output_tokens" in (event.get("usage") or {})
                        stop_reason = (event.get("delta") or {}).get("stop_reason") or stop_reason
                    elif (
                        event.get("type") == "content_block_delta"
                        and event.get("delta", {}).get("type") == "text_delta"
                    ):
                        content.append(event["delta"]["text"])
                    elif event.get("type") == "message_stop":
                        stopped = True
                    elif event.get("type") == "error":
                        raise RuntimeResponsesError(
                            "provider_request_failed",
                            "Vertex returned a stream error",
                            retryable=True,
                            usage=receipt(),
                        )
        except RuntimeResponsesError:
            raise
        except httpx.HTTPStatusError as exc:
            # Stream bodies must be read before extracting Google's diagnostic.
            # Retain only the provider error, never request headers/credentials.
            diagnostic = ""
            try:
                payload = json.loads(exc.response.read())
                envelopes = payload if isinstance(payload, list) else [payload]
                messages = []
                for envelope in envelopes:
                    if not isinstance(envelope, dict):
                        continue
                    error = envelope.get("error")
                    if isinstance(error, dict) and isinstance(error.get("message"), str):
                        messages.append(error["message"])
                diagnostic = "; ".join(messages)
                if self.api_key:
                    diagnostic = diagnostic.replace(self.api_key, "[REDACTED]")
            except (ValueError, httpx.HTTPError):
                pass
            raise RuntimeResponsesError(
                "provider_auth_failed"
                if exc.response.status_code == 401
                else "provider_request_failed",
                f"Vertex HTTP {exc.response.status_code}"
                + (": " + diagnostic[:500] if diagnostic else ""),
                retryable=exc.response.status_code in {408, 429} or exc.response.status_code >= 500,
                usage=receipt(),
            ) from exc
        except Exception as exc:
            raise RuntimeResponsesError(
                "provider_request_failed",
                "Vertex request did not complete",
                retryable=True,
                usage=receipt(),
            ) from exc
        observed = receipt()
        if not stopped or not response_id:
            raise RuntimeResponsesError(
                "provider_request_failed",
                "Vertex stream ended without a complete response",
                retryable=True,
                usage=observed,
            )
        text = "".join(content)
        try:
            value = request.output_model.model_validate_json(text)
        except ValueError as exc:
            raise RuntimeResponsesError(
                "structured_output_validation_failed",
                _validation_error_summary(exc),
                retryable=True,
                usage={**observed, "raw_output": text},
            ) from exc
        return RuntimeResponsesResult(
            value=value,
            response_id=response_id,
            latency_ms=round((perf_counter() - started) * 1000),
            input_tokens=observed["input_tokens"],
            output_tokens=observed["output_tokens"],
            cached_input_tokens=observed["cached_input_tokens"],
            cache_write_input_tokens=observed["cache_write_input_tokens"],
            reasoning_tokens=None,
            provider_usage=observed["provider_usage"],
            raw_output=text,
            prefix_fingerprint=fingerprint,
        )
