"""Postgres audit-plane persistence for observations and text annotations."""

from __future__ import annotations

import hashlib
import json
from importlib import import_module
from typing import Any

from doxagent.agents.runtime.memory.observations import ObservationService
from doxagent.annotations.models import CitationAnnotation, TimeAnnotation
from doxagent.postgres import connect_postgres


class PostgresObservationAnnotationStore:
    """Independent best-effort store; callers decide how to handle failures."""

    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    def save_task(
        self,
        *,
        run_id: str,
        task_id: str,
        observations: ObservationService,
    ) -> None:
        psycopg = import_module("psycopg")
        with connect_postgres(psycopg, self.database_url) as conn:
            with conn.cursor() as cursor:
                for record in observations.raw_store.records():
                    result = record.result
                    cursor.execute(
                        """
                        insert into doxagent.raw_tool_results
                            (run_id, task_id, tool_call_id, tool_name, status,
                             input_payload, output_payload, raw_payload, output_summary)
                        values (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s)
                        on conflict (run_id, task_id, tool_call_id) do update set
                            tool_name = excluded.tool_name,
                            status = excluded.status,
                            input_payload = excluded.input_payload,
                            output_payload = excluded.output_payload,
                            raw_payload = excluded.raw_payload,
                            output_summary = excluded.output_summary
                        """,
                        (
                            run_id,
                            task_id,
                            record.tool_call_id,
                            result.tool_name,
                            result.status.value,
                            _json(record.input_payload),
                            _json(result.output),
                            _json(result.raw) if result.raw is not None else None,
                            result.output_summary,
                        ),
                    )
                for block in observations.block_store.records():
                    cursor.execute(
                        """
                        insert into doxagent.observation_blocks
                            (run_id, task_id, block_id, tool_call_id, parent_block_id,
                             locator, content, context_envelope, content_hash,
                             block_type, metadata)
                        values (%s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb,
                                %s, %s, %s::jsonb)
                        on conflict (run_id, task_id, block_id) do update set
                            parent_block_id = excluded.parent_block_id,
                            locator = excluded.locator,
                            content = excluded.content,
                            context_envelope = excluded.context_envelope,
                            content_hash = excluded.content_hash,
                            block_type = excluded.block_type,
                            metadata = excluded.metadata
                        """,
                        (
                            run_id,
                            task_id,
                            block.block_id,
                            block.tool_call_id,
                            block.parent_block_id,
                            block.locator,
                            _json(block.content),
                            _json(block.context_envelope),
                            block.content_hash,
                            block.block_type,
                            _json(block.metadata),
                        ),
                    )

    def save_citations(self, records: list[CitationAnnotation]) -> None:
        if not records:
            return
        psycopg = import_module("psycopg")
        with connect_postgres(psycopg, self.database_url) as conn:
            with conn.cursor() as cursor:
                for item in records:
                    cursor.execute(
                        """
                        insert into doxagent.citation_annotations
                            (annotation_id, run_id, task_id, result_id, payload_path,
                             text_hash, span_start, span_end, observation_block_id, created_at)
                        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        on conflict (annotation_id) do nothing
                        """,
                        (
                            item.annotation_id,
                            item.run_id,
                            item.task_id,
                            item.result_id,
                            item.payload_path,
                            item.text_hash,
                            item.span_start,
                            item.span_end,
                            item.observation_block_id,
                            item.created_at,
                        ),
                    )

    def save_times(self, records: list[TimeAnnotation]) -> None:
        if not records:
            return
        psycopg = import_module("psycopg")
        with connect_postgres(psycopg, self.database_url) as conn:
            with conn.cursor() as cursor:
                for item in records:
                    cursor.execute(
                        """
                        insert into doxagent.time_annotations
                            (annotation_id, run_id, task_id, result_id, payload_path,
                             text_hash, span_start, span_end, occurred_at, published_at,
                             created_at)
                        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        on conflict (annotation_id) do nothing
                        """,
                        (
                            item.annotation_id,
                            item.run_id,
                            item.task_id,
                            item.result_id,
                            item.payload_path,
                            item.text_hash,
                            item.span_start,
                            item.span_end,
                            item.occurred_at,
                            item.published_at,
                            item.created_at,
                        ),
                    )

    def times_for_text(self, plain_text: str) -> list[TimeAnnotation]:
        text_hash = hashlib.sha256(plain_text.encode("utf-8")).hexdigest()
        psycopg = import_module("psycopg")
        with connect_postgres(psycopg, self.database_url) as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    select annotation_id, run_id, task_id, result_id, payload_path,
                           text_hash, span_start, span_end, occurred_at, published_at,
                           created_at
                    from doxagent.time_annotations
                    where text_hash = %s
                    order by created_at asc, annotation_id asc
                    """,
                    (text_hash,),
                )
                rows = cursor.fetchall()
        return [
            TimeAnnotation(
                annotation_id=row[0],
                run_id=row[1],
                task_id=row[2],
                result_id=row[3],
                payload_path=row[4],
                text_hash=row[5],
                span_start=row[6],
                span_end=row[7],
                occurred_at=row[8],
                published_at=row[9],
                created_at=row[10],
            )
            for row in rows
        ]


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


__all__ = ["PostgresObservationAnnotationStore"]
