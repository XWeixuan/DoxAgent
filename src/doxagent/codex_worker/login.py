"""Interactive ChatGPT device login for the worker's persistent CODEX_HOME."""

from __future__ import annotations

import asyncio

from openai_codex import AsyncCodex


async def login_device_code() -> None:
    async with AsyncCodex() as codex:
        handle = await codex.login_chatgpt_device_code()
        print(f"Verification URL: {handle.verification_url}", flush=True)
        print(f"User code: {handle.user_code}", flush=True)
        await handle.wait()
        print("Codex worker login completed.", flush=True)


def main() -> None:
    asyncio.run(login_device_code())


if __name__ == "__main__":
    main()
