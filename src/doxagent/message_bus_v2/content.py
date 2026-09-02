"""Content-materialization bridge using the proven article extraction pipeline."""

from __future__ import annotations

from doxagent.message_bus_v2.schema import RawMessageInput, new_id
from doxagent.monitoring.media_enrichment import (
    MediaEnrichmentRecord,
    enrich_media_records,
    media_enrichment_metadata,
)


class ArticleContentMaterializer:
    """Enrich media-like items before immutable StandardMessage creation.

    The extraction implementation is currently shared with the v1 namespace as a
    library only; no v1 database, poller, binding or stream semantics are used.
    """

    _PROVIDERS = {"benzinga", "finnhub", "rss"}

    async def materialize(self, message: RawMessageInput) -> RawMessageInput:
        if message.metadata.get("provider") not in self._PROVIDERS:
            return message
        record = MediaEnrichmentRecord(
            standard_message_id=new_id("materialize"),
            raw_message_id=new_id("materialize_raw"),
            source_id=str(message.metadata.get("provider")),
            ticker="MATERIALIZE",
            title=message.title,
            body=message.body,
            url=message.url,
            raw_url=message.url,
            source_name=message.source,
        )
        _, results = await enrich_media_records([record], concurrency=1)
        if not results or not results[0].succeeded or not results[0].content:
            if results:
                metadata = media_enrichment_metadata(message.metadata, results[0])
                return message.model_copy(update={"metadata": metadata})
            return message
        result = results[0]
        return message.model_copy(
            update={
                "body": result.content,
                "url": result.final_url or message.url,
                "source": result.source_name or message.source,
                "metadata": media_enrichment_metadata(message.metadata, result),
            }
        )


__all__ = ["ArticleContentMaterializer"]
