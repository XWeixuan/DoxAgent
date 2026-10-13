"""Synthetic exact-model turns in the Worker account; no tools or business data."""
import asyncio
import json
from datetime import UTC, datetime

from openai_codex.async_client import AsyncCodexClient
from pydantic import BaseModel, ConfigDict


class Wire(BaseModel):
    model_config = ConfigDict(extra="allow")


async def probe(model, effort):
    client = AsyncCodexClient()
    result = dict(model=model, effort=effort, checked_at=datetime.now(UTC).isoformat())
    try:
        await client.start()
        await client.initialize()

        async def request(method, params):
            return (await client.request(method, params, response_model=Wire)).model_dump(mode="json")

        started = await request("thread/start", dict(
            cwd="/tmp", model=model, approvalPolicy="never", sandbox="read-only",
            config={"web_search": "disabled"},
        ))
        result.update(thread_id=started["thread"]["id"], actual_model=started.get("model"))
        await request("turn/start", dict(
            threadId=result["thread_id"], effort=effort,
            input=[{"type": "text", "text": 'Synthetic model access check. Do not use tools. Return only {"ok":true}.'}],
        ))
        for _ in range(45):
            await asyncio.sleep(1)
            read = await request("thread/read", dict(threadId=result["thread_id"], includeTurns=True))
            turns = read["thread"].get("turns", [])
            if turns and turns[-1]["status"] in {"completed", "failed", "interrupted"}:
                result.update(status=turns[-1]["status"], error=turns[-1].get("error"))
                break
        else:
            result["status"] = "timeout"
            await request("turn/interrupt", dict(threadId=result["thread_id"], turnId=turns[-1]["id"]))
    except Exception as exc:
        result.update(error_type=type(exc).__name__, error=str(exc))
    finally:
        await client.close()
    print(json.dumps(result), flush=True)


async def main():
    await asyncio.gather(probe("gpt-6-luna", "max"), probe("gpt-6-sol", "medium"))


asyncio.run(main())
