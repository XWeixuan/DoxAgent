"""Promote only governed collection observations into StateValue projections."""

from __future__ import annotations

from doxagent.horizontal_collection.registry import CollectionTargetRegistry, MetricRegistry
from doxagent.horizontal_collection.schema import (
    CollectionObservation,
    HorizontalCollectionBundle,
    HorizontalCollectionManifest,
    OutputPolicy,
    PromotedStateValue,
    StateParameterIdentity,
)


class HorizontalStateCompiler:
    def __init__(self, *, metrics: MetricRegistry, targets: CollectionTargetRegistry) -> None:
        self._metrics = metrics
        self._targets = targets

    def compile(
        self,
        *,
        ticker: str,
        manifest: HorizontalCollectionManifest,
        observations: tuple[CollectionObservation, ...],
    ) -> HorizontalCollectionBundle:
        promoted: list[PromotedStateValue] = []
        for observation in observations:
            target = self._targets.get(observation.collection_target_id)
            if target.output_policy is not OutputPolicy.STATE_VALUE:
                continue
            metric_id = (
                observation.item_key.split(":", 1)[0] if observation.item_key else target.metric_id
            )
            if not metric_id or metric_id not in {item.metric_id for item in self._metrics.all()}:
                continue
            if observation.unit is None or not observation.source_refs or observation.as_of is None:
                continue
            identity = StateParameterIdentity(entity_id=ticker.upper(), metric_id=metric_id)
            promoted.append(
                PromotedStateValue(
                    parameter_id=identity.parameter_id,
                    entity_id=identity.entity_id,
                    metric_id=metric_id,
                    source_role=target.source_role,
                    time_scope=target.time_scope,
                    value=observation.value,
                    unit=observation.unit,
                    as_of=observation.as_of,
                    source_refs=observation.source_refs,
                    collection_target_id=target.collection_target_id,
                    quality_flags=observation.quality_flags,
                )
            )
        return HorizontalCollectionBundle(
            manifest=manifest,
            observations=observations,
            state_values=tuple(promoted),
        )
