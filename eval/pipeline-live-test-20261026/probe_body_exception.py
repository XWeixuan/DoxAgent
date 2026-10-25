"""Replay one failed public extraction without altering job, Raw, or Stream rows."""
import asyncio
import json
import sqlite3
import traceback

from doxagent.content_enrichment.extractor import SharedContentExtractor
from doxagent.content_enrichment.schema import EnrichmentJob
from doxagent.monitoring.media_enrichment import MediaEnrichmentRecord
from doxagent.settings import DoxAgentSettings
from doxagent.site_strategy.client import SiteAccessClient
from doxagent.site_strategy.tokens import read_token
from doxagent.v2_read.native_content import NativeContent

s = DoxAgentSettings()
db = sqlite3.connect('file:'+s.message_bus_v2_sqlite_path+'?mode=ro', uri=True)
db.row_factory = NativeContent(s.message_bus_v2_sqlite_path).row
row = db.execute("SELECT data_json FROM content_enrichment_jobs WHERE json_extract(data_json,'$.source.source_id')='lakehouse_platform_official_rss' AND status='failed' ORDER BY created_at DESC LIMIT 1").fetchone()
assert row
job = EnrichmentJob.model_validate_json(row[0])
db.close()

async def main():
    site = SiteAccessClient(s.site_access_url, token=read_token(s.site_access_worker_token, s.site_access_worker_token_file))
    extractor = SharedContentExtractor(
        concurrency=1, pipeline_enabled=True, site_access_client=site, close_site_access_client=True,
        proxy_url=s.crawler_egress_proxy_url, browser_enabled=s.content_enrichment_browser_enabled,
        trusted_proxy_dns=s.content_enrichment_trusted_proxy_dns,
    )
    record = MediaEnrichmentRecord(standard_message_id=job.job_id, raw_message_id=job.job_id,
        source_id=job.source.source_id, ticker=None, title=job.message.title, body=job.message.fallback_body,
        url=job.message.url, raw_url=job.message.url, source_name=job.message.source)
    print(json.dumps({'job_id':job.job_id, 'source':job.source.source_id, 'url':job.message.url}))
    try:
        async with asyncio.timeout(45):
            result = await extractor.extract_version(record, job.pipeline_version)
        print(json.dumps({'success':result.succeeded,'reason':result.reason,'diagnostics':result.diagnostics}))
    except Exception:
        traceback.print_exc()
    finally:
        await extractor.close()
asyncio.run(main())
