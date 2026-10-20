import asyncio

from ..schema import PollResult
from .http import PublicHttp
from .parsers import parse_listing
from .schema import endpoints


class CorporateIrAdapter:
    def __init__(self, http=None):
        self.http = http or PublicHttp()
        self.locks = {}

    def endpoint_lock(self, binding_id, endpoint_id):
        return self.locks.setdefault((binding_id, endpoint_id), asyncio.Lock())

    async def poll(self, context):
        """Explicit historical page chunks; realtime uses independent endpoint jobs."""
        checkpoint = dict(context.checkpoint)
        todo = checkpoint.get("endpoints")
        if todo is None:
            todo = {
                s.endpoint_id: s.entry_url
                for s in endpoints(context.source, context.binding)
                if s.enabled
            }
        messages = []
        errors = dict(checkpoint.get("errors", {}))
        coverages = list(checkpoint.get("coverages", []))
        # One page per chunk, releasing the host limit before the next dispatch.
        key, url = next(iter(todo.items())) if todo else (None, None)
        if key:
            spec = next(
                s for s in endpoints(context.source, context.binding) if s.endpoint_id == key
            )
            try:
                async with self.endpoint_lock(context.binding.binding_id, key):
                    capture = await self.http.fetch(spec, url=url)
                if capture.status != 200:
                    raise ValueError(f"IR sweep HTTP {capture.status}: {key}")
                parser_spec = (
                    spec.model_copy(update={"parser_ref": "coherent"})
                    if (spec.endpoint_id == "rklb.ir" and url != spec.entry_url)
                    else spec
                )
                messages, following = parse_listing(parser_spec, capture.body, capture.url)
            except Exception as exc:
                errors[key] = type(exc).__name__ + ": " + str(exc)[:200]
                todo = {k: v for k, v in todo.items() if k != key}
                coverages.append("PARTIAL")
                return PollResult(
                    next_checkpoint={"endpoints": todo, "coverages": coverages, "errors": errors},
                    window_done=not todo,
                    window_coverage="PARTIAL",
                )
            if (
                spec.fallback_urls
                and url == spec.entry_url
                and messages
                and min(m.published_at for m in messages) > context.window_start
            ):
                following = spec.fallback_urls[0]
            if following and (
                not messages or min(m.published_at for m in messages) >= context.window_start
            ):
                todo = {**todo, key: following}
            else:
                todo = {k: v for k, v in todo.items() if k != key}
                covered = bool(
                    messages and min(m.published_at for m in messages) <= context.window_start
                )
                coverages.append("COMPLETE" if covered else "PARTIAL")
        return PollResult(
            messages=messages,
            next_checkpoint={"endpoints": todo, "coverages": coverages, "errors": errors},
            window_done=not todo,
            window_coverage="PARTIAL" if "PARTIAL" in coverages else "COMPLETE",
        )

    async def close(self):
        await self.http.close()
