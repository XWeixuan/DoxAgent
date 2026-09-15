"""Bounded, single-table evidence reads for one Case and its pinned artifacts."""

import json

from doxagent.v2_read.native_content import MARKER
from doxagent.v2_read.runtime import timing


class CaseEvidence:
    MAX_FIELD_BYTES = 65536

    def __init__(self, store, ticker, case_id, seq):
        self.store, self.ticker, self.case_id, self.seq = store, ticker, case_id, seq

    def fields(self, kind, identity, paths):
        # Paths are internal constants, never request input. Do not select payload:
        # its row codec would dereference unrelated frozen inputs/article bodies.
        columns = ",".join("json_extract(payload,'$." + path + "')" for path in paths)
        with self.store.connect() as db:
            row = db.execute(
                "SELECT " + columns + " FROM objects WHERE kind=? AND ticker=? AND id=? "
                "AND valid_from<=? AND (valid_to IS NULL OR valid_to>?) "
                "ORDER BY valid_from DESC LIMIT 1",
                (kind, self.ticker, identity, self.seq, self.seq),
            ).fetchone()
        if row is None:
            return None
        result = {}
        for path, value in zip(paths, row, strict=True):
            if (
                isinstance(value, str)
                and value.startswith(("{", "["))
                and (
                    path
                    in {
                        "version_pin",
                        "w1_final",
                        "w2_final",
                        "w2_round1",
                        "w3_result",
                        "output",
                        "candidate",
                    }
                    or MARKER in value
                )
            ):
                try:
                    value = json.loads(value)
                except json.JSONDecodeError:
                    result[path] = value
                    continue
                if isinstance(value, dict) and MARKER in value:
                    if value.get("bytes", self.MAX_FIELD_BYTES + 1) > self.MAX_FIELD_BYTES:
                        value = None
                    else:
                        value = self.store.content_codec.decode(value)
            result[path] = value
        return result

    def attempt_groups(self):
        with self.store.connect() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT route AS node, json_extract(payload,'$.round') AS round, "
                    "COUNT(*) AS count, "
                    "COUNT(json_extract(payload,'$.timing.first_started_at.value')) AS starts, "
                    "COUNT(json_extract(payload,'$.timing.completed_at.value')) AS ends, "
                    "MIN(rtrim(json_extract(payload,'$.timing.first_started_at.value'),'Z')) "
                    "|| 'Z' AS first_at, "
                    "MAX(rtrim(json_extract(payload,'$.timing.completed_at.value'),'Z')) "
                    "|| 'Z' AS last_at "
                    "FROM objects WHERE kind='attempt' AND ticker=? AND parent=? "
                    "AND valid_from<=? AND (valid_to IS NULL OR valid_to>?) GROUP BY route,round",
                    (self.ticker, self.case_id, self.seq, self.seq),
                )
            ]

    def interval(self, groups, node=None):
        selected = (
            [g for g in groups if g["node"] == node]
            if node
            else [g for g in groups if g["node"] in {"W1", "W2"} and g["round"] in {"R1", "R2"}]
        )
        if not selected or any(
            g["starts"] != g["count"] or g["ends"] != g["count"] for g in selected
        ):
            return timing(None, None)
        from datetime import datetime

        result = timing(
            min((g["first_at"] for g in selected), key=datetime.fromisoformat),
            max((g["last_at"] for g in selected), key=datetime.fromisoformat),
        )
        result["basis"] = "DERIVED_FROM_RECORDED_INTERVALS"
        return result

    def rounds(self, groups, node, skipped=False, empty_recall=False):
        counts = {g["round"]: g["count"] for g in groups if g["node"] == node}
        rounds = ["R1", "R2"] + (["R3"] if "R3" in counts else [])
        return [
            {
                "round": name,
                "attempt_count": counts.get(name, 0),
                "not_executed_reason": "W2_SKIPPED"
                if skipped
                else "NO_POLICY_CANDIDATE"
                if node == "W2" and name == "R2" and empty_recall and not counts.get(name)
                else None,
            }
            for name in rounds
        ]

    def latest_success(self, node, round_name):
        with self.store.connect() as db:
            row = db.execute(
                "SELECT id FROM objects WHERE kind='attempt' AND ticker=? AND parent=? AND route=? "
                "AND json_extract(payload,'$.round')=? "
                "AND json_extract(payload,'$.status')='SUCCEEDED' "
                "AND valid_from<=? AND (valid_to IS NULL OR valid_to>?) "
                "ORDER BY sort_key DESC,id DESC LIMIT 1",
                (self.ticker, self.case_id, node, round_name, self.seq, self.seq),
            ).fetchone()
        return self.fields("native:runtime_v2_turns", row[0], ["output"]) if row else None
