"""Replay exported Raw accident samples in a disposable LOCAL bus (no network).

Input: JSON array on stdin. Output: counts and linked business classifications.
Does not delete/replay production messages. Extraction outcomes are historical evidence,
not a claim that a current live request would succeed.
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from collections import Counter
from datetime import datetime
from html import unescape
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from doxagent.message_bus_v2.repository import MessageBusV2Repository
from doxagent.message_bus_v2.schema import RawMessageInput
from doxagent.message_bus_v2.service import MessageBusV2Service


async def replay(records):
    with TemporaryDirectory(prefix="doxagent-dedup-replay-") as directory:
        repository = MessageBusV2Repository(Path(directory) / "bus.sqlite3")
        bus = MessageBusV2Service(repository)
        bus.bootstrap()
        bus.start_ticker("MU")
        results = Counter()
        for row in sorted(records, key=lambda r: r["collected_at"]):
            source_id = row["source_id"]
            payload = row["raw_payload"]
            metadata = dict(row["metadata"])
            generic = "providers.php" in row["url"]
            metadata["identity_evidence"] = {
                "id_kind": "stable_article"
                if source_id == "yahoo_finance_news"
                else "provider_article",
                "url_kind": "generic" if generic else "article",
            }
            summary = payload.get("summary") or payload.get("description")
            native = None
            if source_id == "ibkr_news":
                native = (
                    re.sub(
                        r"\s+",
                        " ",
                        unescape(re.sub(r"<[^>]+>", " ", str(payload.get("articleText") or ""))),
                    ).strip()
                    or None
                )
            original = RawMessageInput(
                external_id=row.get("external_id"),
                source_item_key=row.get("source_item_key"),
                title=row.get("title"),
                body=native,
                summary=summary,
                source=row["source"],
                publisher_name=row.get("publisher_name"),
                url=row["url"],
                published_at=row["published_at"],
                publication_time_basis=row.get("publication_time_basis", "EXACT"),
                raw_payload=payload,
                metadata=metadata,
            )
            final = original.model_copy(update={"body": row["body"]})
            clock = datetime.fromisoformat(row["collected_at"].replace("Z", "+00:00"))
            with (
                patch("doxagent.message_bus_v2.service.utc_now", return_value=clock),
                patch("doxagent.message_bus_v2.repository.utc_now", return_value=clock),
            ):
                result = await bus.accept_message(
                    source=bus.require_source(source_id),
                    binding=repository.get_binding("MU:" + source_id),
                    message=final,
                    enrichment_input=original,
                    trusted_enrichment=True,
                    bootstrap=False,
                    collected_at=clock,
                )
                results[(source_id, result.decision.value)] += 1
        print(
            json.dumps(
                {
                    "sample_count": len(records),
                    "decisions": {f"{s}:{d}": n for (s, d), n in results.items()},
                    "published_stream_items": repository.latest_stream_offset("MU"),
                    "versions": dict(
                        Counter(
                            r.metadata["message_version"]["classification"]
                            for r in repository.list_raw(ticker="MU", limit=1000)
                        )
                    ),
                },
                sort_keys=True,
            )
        )


if __name__ == "__main__":
    asyncio.run(replay(json.load(sys.stdin)))
