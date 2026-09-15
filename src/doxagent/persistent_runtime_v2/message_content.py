"""Read latest article content once, within the first Runtime task claim.

No mutating Bus repository is constructed. Retries and CLOSED frozen rosters
retain their original inputs; stream identity and admission times never change.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from doxagent.message_bus_v2.schema import (
    MaterializedStreamItem,
    MaterializedStreamMember,
    StandardMessage,
    StreamItem,
)
from doxagent.v2_read.native_content import NativeContent

from .schema import SourceMessageEnvelope


def prepare_message_inputs(inputs: dict[str, Any], *, path: str | Path) -> dict[str, Any]:
    if inputs.get("mode") != "REALTIME" or inputs.get("message_content_frozen"):
        return inputs
    source = SourceMessageEnvelope.model_validate(inputs["source"])
    codec = NativeContent(path)
    with sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=5) as db:
        db.row_factory = codec.row
        db.execute("BEGIN")
        item_row = db.execute(
            "select data_json from stream_items where stream_item_id=? and ticker=?",
            (source.stream_item_id, source.snapshot.ticker),
        ).fetchone()
        if item_row is None:
            raise LookupError("Runtime source stream is unavailable")
        rows = db.execute(
            "select sm.member_index, s.data_json from stream_members sm "
            "join standard_messages s using(standard_message_id) "
            "where sm.stream_item_id=? order by sm.member_index",
            (source.stream_item_id,),
        ).fetchall()
        members = []
        revisions = {}
        for row in rows:
            standard = StandardMessage.model_validate_json(row["data_json"])
            members.append(
                MaterializedStreamMember(
                    **{
                        k: v
                        for k, v in standard.model_dump().items()
                        if k in MaterializedStreamMember.model_fields
                    },
                    stream_item_id=source.stream_item_id,
                    member_index=row["member_index"],
                )
            )
            revisions[standard.standard_message_id] = standard.metadata.get(
                "message_version", {}
            ).get("content_revision", 1)
        if [m.standard_message_id for m in members] != source.member_message_ids:
            raise ValueError("Runtime source membership changed")
        current = SourceMessageEnvelope.from_stream_item(
            MaterializedStreamItem(
                item=StreamItem.model_validate_json(item_row["data_json"]),
                members=members,
            )
        )
        if current.source_message_id != source.source_message_id:
            raise ValueError("Runtime source identity changed")
    return {
        **inputs,
        "source": json.loads(
            source.model_copy(update={"snapshot": current.snapshot}).model_dump_json()
        ),
        "message_content_frozen": True,
        "message_content_revisions": revisions,
    }
