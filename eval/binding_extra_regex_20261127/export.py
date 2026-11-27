import base64
import gzip
import json
import os
import sqlite3

from doxagent.v2_read.native_content import NativeContent

p = os.environ["DOXAGENT_MESSAGE_BUS_V2_SQLITE_PATH"]
c = sqlite3.connect("file:" + p + "?mode=ro", uri=True, timeout=5)
c.execute("pragma query_only=on")
codec = NativeContent(p)
sids = ["huggingnews_rss", "technews_hardware", "tomshardware_rss", "quantum_zeitgeist"]
out = {"bindings": [], "cases": []}
for (raw,) in c.execute(
    "select data_json from ticker_source_bindings where enabled=1 and tombstoned_at is null"
):
    b = json.loads(raw)
    if b["source_id"] not in sids:
        continue
    out["bindings"].append(
        {
            "ticker": b["ticker"],
            "source_id": b["source_id"],
            "binding_id": b["binding_id"],
            "version": b["version"],
        }
    )
    for (raw,) in c.execute(
        "select data_json from standard_messages where ticker=? and source_id=? order by published_at",
        (b["ticker"], b["source_id"]),
    ):
        d = codec.decode(json.loads(raw))
        md = d.get("metadata", {})
        out["cases"].append(
            {
                "id": d.get("standard_message_id"),
                "ticker": b["ticker"],
                "source_id": b["source_id"],
                "title": d.get("title") or "",
                "summary": md.get("content_evidence", {}).get("summary") or "",
                "body": d.get("body") or "",
                "url": d.get("url"),
                "published_at": d.get("published_at"),
            }
        )
print(base64.b64encode(gzip.compress(json.dumps(out, ensure_ascii=False).encode())).decode())
