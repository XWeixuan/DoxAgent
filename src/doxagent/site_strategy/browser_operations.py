"""A whole browser operation is the IPC unit, never a remote Page proxy."""

from dataclasses import asdict

from .browser_runtime import RuntimeProvenance
from .runtime import RuntimeResponse
from .schema import BrowserRuntimeKind

MAX_FRAME_BYTES = 21_000_000


def encode_response(response: RuntimeResponse) -> dict:
    return asdict(response)


def decode_response(value: dict) -> RuntimeResponse:
    value = dict(value)
    if value.get("provenance"):
        value["provenance"]["runtime_kind"] = BrowserRuntimeKind(
            value["provenance"]["runtime_kind"],
        )
        value["provenance"] = RuntimeProvenance(**value["provenance"])
    return RuntimeResponse(**value)


async def execute_browser_operation(runtime, request, resolved, identity, egress):
    """Run all recipes/gates/content normalization in the identity process."""
    return await runtime._browser(request, resolved, identity, egress)
