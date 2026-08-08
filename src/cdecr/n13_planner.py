"""Indexed, immutable N13 candidate planning for large BULK_EPOCH runs."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime

import numpy as np

from cdecr.cross_document_contracts import RecallRoute

N13_PLANNER_VERSION = "indexed_v2"
N13_CHEAP_UNIVERSE_CAP = 64
N13_VECTOR_TOP_K = 16
N13_PLAN_CHUNK_SIZE = 128


@dataclass(frozen=True)
class N13ParentBlock:
    message_id: str
    originators: frozenset[str]
    field: str
    start_char: int
    end_char: int


@dataclass(frozen=True)
class N13PackageCard:
    package_id: str
    member_event_ids: frozenset[str]
    artifact_ids: frozenset[str]
    anchor_ids: frozenset[str]
    entity_ids: frozenset[str]
    issuer_ids: frozenset[str]
    institution_ids: frozenset[str]
    instrument_ids: frozenset[str]
    object_ids: frozenset[str]
    market_sessions: frozenset[str]
    market_measures: frozenset[str]
    member_identity_hashes: frozenset[str]
    source_ids: frozenset[str]
    parent_blocks: tuple[N13ParentBlock, ...]
    event_families: frozenset[str]
    package_kind: str
    package_family: str
    anchor_period_id: str | None
    lifecycle_state: str | None
    anchor_conflict: bool
    time_start: date | None
    time_end: date | None
    vector: tuple[float, ...] | None
    representative_propositions: tuple[str, ...] = ()
    package_version: int = 1
    profile_hash: str = ""


@dataclass(frozen=True)
class N13CandidatePair:
    left_package_id: str
    right_package_id: str
    routes: tuple[RecallRoute, ...]
    similarity: float | None


@dataclass(frozen=True)
class N13PlanChunk:
    chunk_index: int
    touched_package_ids: tuple[str, ...]
    pairs: tuple[N13CandidatePair, ...]


@dataclass(frozen=True)
class N13Plan:
    planner_version: str
    chunks: tuple[N13PlanChunk, ...]
    candidate_pair_count: int
    strong_bucket_overflow_count: int
    vector_query_count: int

    @property
    def pairs(self) -> tuple[N13CandidatePair, ...]:
        return tuple(pair for chunk in self.chunks for pair in chunk.pairs)


def build_indexed_n13_plan(
    cards: Iterable[N13PackageCard],
    *,
    touched_package_ids: Iterable[str],
    cheap_universe_cap: int = N13_CHEAP_UNIVERSE_CAP,
    vector_top_k: int = N13_VECTOR_TOP_K,
    chunk_size: int = N13_PLAN_CHUNK_SIZE,
) -> N13Plan:
    """Generate bounded candidates without any persistence or model access in pair loops."""

    by_id = {card.package_id: card for card in cards}
    ordered = [by_id[key] for key in sorted(by_id)]
    touched = [by_id[key] for key in sorted(set(touched_package_ids)) if key in by_id]
    strong: dict[str, list[str]] = defaultdict(list)
    structured: dict[str, list[str]] = defaultdict(list)
    for card in ordered:
        for key in _strong_keys(card):
            strong[key].append(card.package_id)
        for key in _structured_keys(card):
            structured[key].append(card.package_id)
    for buckets in (strong, structured):
        for values in buckets.values():
            values.sort()

    vector_neighbors = _vector_neighbors(ordered, touched, top_k=vector_top_k)
    seen_pairs: set[tuple[str, str]] = set()
    chunks: list[N13PlanChunk] = []
    overflow_count = 0
    for chunk_index, offset in enumerate(range(0, len(touched), max(1, chunk_size))):
        chunk_cards = touched[offset : offset + max(1, chunk_size)]
        chunk_pairs: list[N13CandidatePair] = []
        for left in chunk_cards:
            candidate_support: dict[str, int] = defaultdict(int)
            candidate_ids: set[str] = set(vector_neighbors.get(left.package_id, ()))
            for key in _strong_keys(left):
                bucket = strong.get(key, ())
                if len(bucket) > cheap_universe_cap:
                    overflow_count += 1
                    for candidate_id in bucket:
                        if candidate_id != left.package_id and _has_second_route(
                            left, by_id[candidate_id]
                        ):
                            candidate_ids.add(candidate_id)
                            candidate_support[candidate_id] += 2
                else:
                    for candidate_id in bucket:
                        if candidate_id != left.package_id:
                            candidate_ids.add(candidate_id)
                            candidate_support[candidate_id] += 2
            for key in _structured_keys(left):
                for candidate_id in structured.get(key, ())[:cheap_universe_cap]:
                    if candidate_id != left.package_id:
                        candidate_ids.add(candidate_id)
                        candidate_support[candidate_id] += 1
            ranked_ids = sorted(
                candidate_ids,
                key=lambda candidate_id: (-candidate_support[candidate_id], candidate_id),
            )[:cheap_universe_cap]
            for candidate_id in ranked_ids:
                right = by_id[candidate_id]
                pair_key = (
                    min(left.package_id, right.package_id),
                    max(left.package_id, right.package_id),
                )
                if pair_key in seen_pairs:
                    continue
                routes, similarity = _pair_signals(left, right)
                weak = {
                    RecallRoute.CORE_ENTITY,
                    RecallRoute.TIME_WINDOW,
                    RecallRoute.PROPOSITION_EMBEDDING,
                    RecallRoute.SAME_SOURCE_MEMBER,
                }
                if not routes or (len(routes) == 1 and routes[0] in weak):
                    continue
                seen_pairs.add(pair_key)
                chunk_pairs.append(
                    N13CandidatePair(
                        left_package_id=pair_key[0],
                        right_package_id=pair_key[1],
                        routes=tuple(routes),
                        similarity=similarity,
                    )
                )
        chunk_pairs.sort(key=lambda item: (item.left_package_id, item.right_package_id))
        chunks.append(
            N13PlanChunk(
                chunk_index=chunk_index,
                touched_package_ids=tuple(card.package_id for card in chunk_cards),
                pairs=tuple(chunk_pairs),
            )
        )
    return N13Plan(
        planner_version=N13_PLANNER_VERSION,
        chunks=tuple(chunks),
        candidate_pair_count=len(seen_pairs),
        strong_bucket_overflow_count=overflow_count,
        vector_query_count=len(vector_neighbors),
    )


def _strong_keys(card: N13PackageCard) -> tuple[str, ...]:
    keys = [f"event:{value}" for value in card.member_event_ids]
    keys.extend(f"artifact:{value}" for value in card.artifact_ids)
    keys.extend(f"anchor:{value}" for value in card.anchor_ids)
    keys.extend(f"identity:{value}" for value in card.member_identity_hashes)
    return tuple(sorted(keys))


def _structured_keys(card: N13PackageCard) -> tuple[str, ...]:
    period = card.anchor_period_id or "-"
    keys = [
        f"entity-family-period:{entity}:{card.package_family}:{period}"
        for entity in card.entity_ids
    ]
    keys.extend(f"entity-family:{entity}:{card.package_family}" for entity in card.entity_ids)
    keys.extend(
        f"issuer-family-period:{issuer}:{card.package_family}:{period}"
        for issuer in card.issuer_ids
    )
    keys.extend(f"issuer-family:{issuer}:{card.package_family}" for issuer in card.issuer_ids)
    if card.time_start is not None:
        bucket = card.time_start.toordinal() // 45
        keys.extend(
            f"entity-time:{entity}:{card.package_family}:{nearby}"
            for entity in card.entity_ids
            for nearby in (bucket - 1, bucket, bucket + 1)
        )
    keys.extend(f"institution:{value}" for value in card.institution_ids)
    keys.extend(
        f"market:{instrument}:{session}:{measure}"
        for instrument in card.instrument_ids or {"-"}
        for session in card.market_sessions or {"-"}
        for measure in card.market_measures or {"-"}
    )
    keys.extend(f"source:{value}" for value in card.source_ids)
    return tuple(sorted(keys))


def _has_second_route(left: N13PackageCard, right: N13PackageCard) -> bool:
    return bool(
        left.entity_ids.intersection(right.entity_ids)
        or left.source_ids.intersection(right.source_ids)
        or left.member_identity_hashes.intersection(right.member_identity_hashes)
        or (
            left.anchor_period_id
            and left.anchor_period_id == right.anchor_period_id
            and left.package_family == right.package_family
        )
    )


def _pair_signals(
    left: N13PackageCard, right: N13PackageCard
) -> tuple[list[RecallRoute], float | None]:
    routes: set[RecallRoute] = set()
    if left.member_event_ids.intersection(right.member_event_ids):
        routes.add(RecallRoute.SHARED_ATOMIC_EVENT)
    if left.artifact_ids.intersection(right.artifact_ids):
        routes.add(RecallRoute.CANONICAL_ARTIFACT)
    if left.anchor_ids.intersection(right.anchor_ids):
        routes.add(RecallRoute.PACKAGE_ANCHOR)
    if left.entity_ids.intersection(right.entity_ids):
        routes.add(RecallRoute.CORE_ENTITY)
    if left.member_identity_hashes.intersection(right.member_identity_hashes):
        routes.add(RecallRoute.MEMBER_IDENTITY)
    if left.source_ids.intersection(right.source_ids):
        routes.add(RecallRoute.SAME_SOURCE_MEMBER)
    if _parent_context_overlap(left.parent_blocks, right.parent_blocks):
        routes.add(RecallRoute.PARENT_CONTEXT)
    if _ranges_near(left, right):
        routes.add(RecallRoute.TIME_WINDOW)
    if left.lifecycle_state and left.lifecycle_state == right.lifecycle_state:
        routes.add(RecallRoute.LIFECYCLE_COMPATIBILITY)
    similarity: float | None = None
    if (
        left.vector is not None
        and right.vector is not None
        and len(left.vector) == len(right.vector)
    ):
        left_vector = np.asarray(left.vector, dtype=np.float32)
        right_vector = np.asarray(right.vector, dtype=np.float32)
        denominator = float(np.linalg.norm(left_vector) * np.linalg.norm(right_vector))
        similarity = (
            float(np.dot(left_vector, right_vector) / denominator) if denominator > 0 else -1.0
        )
        if similarity >= 0.65:
            routes.add(RecallRoute.PROPOSITION_EMBEDDING)
    return sorted(routes, key=str), similarity


def package_card_signals(
    left: N13PackageCard, right: N13PackageCard
) -> tuple[list[RecallRoute], float | None]:
    """Shared pure-card signal compiler used by N13 and Package Wave C."""

    return _pair_signals(left, right)


def _parent_context_overlap(
    left: tuple[N13ParentBlock, ...], right: tuple[N13ParentBlock, ...]
) -> bool:
    for left_block in left:
        for right_block in right:
            if (
                left_block.message_id != right_block.message_id
                or left_block.field != right_block.field
                or not left_block.originators.intersection(right_block.originators)
            ):
                continue
            gap = max(
                0,
                max(left_block.start_char, right_block.start_char)
                - min(left_block.end_char, right_block.end_char),
            )
            if gap <= 150:
                return True
    return False


def _ranges_near(left: N13PackageCard, right: N13PackageCard) -> bool:
    if not left.time_start or not right.time_start:
        return False
    left_end = left.time_end or left.time_start
    right_end = right.time_end or right.time_start
    return not (
        left_end.toordinal() + 45 < right.time_start.toordinal()
        or right_end.toordinal() + 45 < left.time_start.toordinal()
    )


def _vector_neighbors(
    cards: list[N13PackageCard],
    touched: list[N13PackageCard],
    *,
    top_k: int,
) -> dict[str, tuple[str, ...]]:
    vector_cards = [card for card in cards if card.vector]
    if not vector_cards or top_k <= 0:
        return {}
    dimensions = {len(card.vector or ()) for card in vector_cards}
    if len(dimensions) != 1:
        return {}
    matrix = np.asarray([card.vector for card in vector_cards], dtype=np.float32)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    matrix = matrix / np.maximum(norms, 1e-12)
    row_by_id = {card.package_id: index for index, card in enumerate(vector_cards)}
    result: dict[str, tuple[str, ...]] = {}
    for offset in range(0, len(touched), N13_PLAN_CHUNK_SIZE):
        selected = [
            card
            for card in touched[offset : offset + N13_PLAN_CHUNK_SIZE]
            if card.package_id in row_by_id
        ]
        if not selected:
            continue
        query = matrix[[row_by_id[card.package_id] for card in selected]]
        scores = query @ matrix.T
        for row, card in enumerate(selected):
            count = min(top_k + 1, len(vector_cards))
            indices = np.argpartition(-scores[row], count - 1)[:count]
            ordered = sorted(
                indices,
                key=lambda index: (-float(scores[row, index]), vector_cards[index].package_id),
            )
            result[card.package_id] = tuple(
                vector_cards[index].package_id
                for index in ordered
                if vector_cards[index].package_id != card.package_id
            )[:top_k]
    return result


def as_date(value: date | datetime | None) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    return value
