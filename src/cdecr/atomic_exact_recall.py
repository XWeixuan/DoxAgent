"""Exact, deterministic matrix cosine recall for persisted Atomic embeddings."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import numpy as np


class ExactCosineRecallIndex:
    def __init__(self, vectors: Mapping[str, Sequence[float]]) -> None:
        valid = [
            (event_id, np.asarray(vector, dtype=np.float64))
            for event_id, vector in sorted(vectors.items())
            if vector and np.isfinite(vector).all()
        ]
        dimension = valid[0][1].size if valid else 0
        valid = [(event_id, vector) for event_id, vector in valid if vector.size == dimension]
        self.event_ids = tuple(event_id for event_id, _ in valid)
        if not valid:
            self.matrix = np.empty((0, 0), dtype=np.float64)
            return
        matrix = np.vstack([vector for _, vector in valid])
        norms = np.linalg.norm(matrix, axis=1)
        norms[norms == 0.0] = 1.0
        self.matrix = matrix / norms[:, None]

    def scores(self, query: Sequence[float]) -> dict[str, float]:
        vector = np.asarray(query, dtype=np.float64)
        if (
            self.matrix.size == 0
            or vector.size != self.matrix.shape[1]
            or not np.isfinite(vector).all()
        ):
            return {event_id: 0.0 for event_id in self.event_ids}
        norm = float(np.linalg.norm(vector))
        if norm == 0.0:
            return {event_id: 0.0 for event_id in self.event_ids}
        values = self.matrix @ (vector / norm)
        return {
            event_id: max(-1.0, min(1.0, float(score)))
            for event_id, score in zip(self.event_ids, values, strict=True)
        }

    def scores_many(
        self, queries: Mapping[str, Sequence[float]], *, chunk_size: int = 128
    ) -> dict[str, dict[str, float]]:
        output: dict[str, dict[str, float]] = {}
        ordered = sorted(queries.items())
        for offset in range(0, len(ordered), max(1, chunk_size)):
            chunk = ordered[offset : offset + max(1, chunk_size)]
            valid: list[tuple[str, np.ndarray]] = []
            for query_id, values in chunk:
                vector = np.asarray(values, dtype=np.float64)
                if (
                    self.matrix.size
                    and vector.size == self.matrix.shape[1]
                    and np.isfinite(vector).all()
                    and float(np.linalg.norm(vector)) > 0.0
                ):
                    valid.append((query_id, vector / np.linalg.norm(vector)))
                else:
                    output[query_id] = {
                        event_id: 0.0 for event_id in self.event_ids
                    }
            if valid:
                score_matrix = np.vstack([vector for _, vector in valid]) @ self.matrix.T
                for (query_id, _), row in zip(valid, score_matrix, strict=True):
                    output[query_id] = {
                        event_id: max(-1.0, min(1.0, float(score)))
                        for event_id, score in zip(self.event_ids, row, strict=True)
                    }
        return output
