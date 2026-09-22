"""Small loopback-only TCP relay whose target is selected by maintenance state."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path


async def relay(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter, target_file: Path
) -> None:
    upstream_writer: asyncio.StreamWriter | None = None
    try:
        port = int(target_file.read_text(encoding="ascii").strip())
        if port < 5901 or port > 5999:
            raise ValueError("invalid relay target")
        upstream_reader, upstream_writer = await asyncio.open_connection("127.0.0.1", port)

        async def copy(source: asyncio.StreamReader, destination: asyncio.StreamWriter) -> None:
            while data := await source.read(64 * 1024):
                destination.write(data)
                await destination.drain()

        left = asyncio.create_task(copy(reader, upstream_writer))
        right = asyncio.create_task(copy(upstream_reader, writer))
        done, pending = await asyncio.wait({left, right}, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        await asyncio.gather(*done, *pending, return_exceptions=True)
    except Exception:
        pass
    finally:
        if upstream_writer is not None:
            upstream_writer.close()
            await upstream_writer.wait_closed()
        writer.close()
        await writer.wait_closed()


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target-file", required=True)
    parser.add_argument("--port", type=int, default=5900)
    args = parser.parse_args()
    target = Path(args.target_file)
    server = await asyncio.start_server(
        lambda reader, writer: relay(reader, writer, target), "0.0.0.0", args.port
    )
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
