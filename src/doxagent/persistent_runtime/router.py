"""Deterministic Route Engine for Persistent Runtime Execution."""

from __future__ import annotations

from doxagent.monitoring.schema import SourceType
from doxagent.persistent_runtime.schema import (
    A2Result,
    A2VerificationStatus,
    RouteDecision,
    RuntimeRoute,
    RuntimeSourceMessage,
    W1Confidence,
    W1Result,
    W2Result,
    W2Type,
)


class RouteEngine:
    """Apply PRD routing rules without making LLM judgments."""

    def plan_initial(
        self,
        message: RuntimeSourceMessage,
        *,
        w1: W1Result,
        w2: W2Result,
    ) -> RouteDecision:
        if message.source_type is SourceType.SOCIAL:
            return self._plan_social_initial(message, w1=w1, w2=w2)
        return self._plan_media_initial(message, w1=w1, w2=w2)

    def plan_after_a2(
        self,
        message: RuntimeSourceMessage,
        *,
        w1: W1Result,
        w2: W2Result,
        a2: A2Result,
    ) -> RouteDecision:
        if message.source_type is SourceType.SOCIAL:
            return self._plan_social_after_a2(message, w1=w1, w2=w2, a2=a2)
        return self._plan_media_after_a2(message, w1=w1, w2=w2, a2=a2)

    def plan_w1_failure(self, message: RuntimeSourceMessage, *, reason: str) -> RouteDecision:
        if message.source_type is SourceType.SOCIAL:
            return self._decision(
                message,
                RuntimeRoute.INGEST_QUEUE,
                f"W1 failed for social; preserve for later review: {reason}",
            )
        return self._decision(
            message,
            RuntimeRoute.O3,
            f"W1 failed for media; escalate to O3 fallback: {reason}",
        )

    def plan_duplicate_archive(
        self,
        message: RuntimeSourceMessage,
        *,
        duplicate_of_source_message_id: str,
        duplicate_key: str,
    ) -> RouteDecision:
        return RouteDecision(
            source_message_id=message.source_message_id,
            ticker=message.ticker,
            route=RuntimeRoute.ARCHIVE,
            reason=(
                "duplicate URL/content_hash matched prior runtime execution; archive current "
                "message with duplicate audit."
            ),
            duplicate_of_source_message_id=duplicate_of_source_message_id,
            duplicate_key=duplicate_key,
        )

    def plan_w2_failure(
        self,
        message: RuntimeSourceMessage,
        *,
        w1: W1Result,
        reason: str,
    ) -> RouteDecision:
        if w1.is_new:
            return self._decision(
                message,
                RuntimeRoute.O3,
                f"W2 failed after W1 marked new; escalate to O3 fallback: {reason}",
            )
        return self._decision(
            message,
            RuntimeRoute.ARCHIVE,
            f"W2 failed but W1 marked old; archive with exception audit: {reason}",
        )

    def plan_a2_failure(
        self,
        message: RuntimeSourceMessage,
        *,
        w1: W1Result,
        w2: W2Result,
        reason: str,
    ) -> RouteDecision:
        if message.source_type is SourceType.SOCIAL:
            route = (
                RuntimeRoute.INGEST_QUEUE
                if w2.type is not W2Type.IRRELEVANT
                else RuntimeRoute.ARCHIVE
            )
            return self._decision(
                message,
                route,
                f"A2 failed for social; route by W2 type/source quality: {reason}",
            )
        if w2.type is W2Type.DIRECT_TRADE_CANDIDATE and w1.confidence is W1Confidence.LOW:
            return self._decision(
                message,
                RuntimeRoute.INGEST_QUEUE,
                f"A2 failed while rechecking low-confidence media DTC: {reason}",
            )
        return self._decision(
            message,
            RuntimeRoute.INGEST_QUEUE,
            f"A2 failed; preserve message for later review: {reason}",
        )

    def plan_o3_failure(
        self,
        message: RuntimeSourceMessage,
        *,
        upstream_trade_path: bool,
        reason: str,
        timeout: bool = False,
    ) -> RouteDecision:
        if timeout:
            return self._decision(
                message,
                RuntimeRoute.TRADING_RECORD,
                f"O3 timeout; record with exception per runtime timeout policy: {reason}",
                upstream_trade_path=True,
            )
        if upstream_trade_path:
            return self._decision(
                message,
                RuntimeRoute.TRADING_RECORD,
                f"O3 {'timeout' if timeout else 'failure'} on trade path; record with exception: "
                f"{reason}",
                upstream_trade_path=True,
            )
        return self._decision(
            message,
            RuntimeRoute.INGEST_QUEUE,
            f"O3 {'timeout' if timeout else 'failure'} outside trade path; preserve for review: "
            f"{reason}",
        )

    def _plan_media_initial(
        self,
        message: RuntimeSourceMessage,
        *,
        w1: W1Result,
        w2: W2Result,
    ) -> RouteDecision:
        if w1.is_new and w2.type is W2Type.DIRECT_TRADE_CANDIDATE:
            if w1.confidence in {W1Confidence.HIGH, W1Confidence.MEDIUM}:
                return self._decision(
                    message,
                    RuntimeRoute.TRADING_RECORD,
                    "media new + DTC with high/medium W1 confidence goes directly to "
                    "Trading Records.",
                    upstream_trade_path=True,
                    requires_o3_known_events_update=True,
                )
            return self._decision(
                message,
                RuntimeRoute.A2,
                "media new + DTC with low W1 confidence requires A2 novelty review.",
                upstream_trade_path=True,
            )
        if w1.is_new and w2.type is W2Type.ESCALATE_TO_BACKGROUND_AGENT:
            if w1.confidence in {W1Confidence.HIGH, W1Confidence.MEDIUM}:
                return self._decision(message, RuntimeRoute.O3, "media new + EBA goes to O3.")
            return self._decision(
                message,
                RuntimeRoute.A2,
                "media new + EBA with low W1 confidence requires A2 novelty review.",
            )
        if w1.is_new and w2.type is W2Type.NULL:
            return self._decision(
                message,
                RuntimeRoute.O3,
                "media new + NULL goes to O3 for uncovered policy judgment.",
                o3_must_check_novelty_first=w1.confidence is W1Confidence.LOW,
            )
        if w1.is_new and w2.type is W2Type.IRRELEVANT:
            return self._decision(message, RuntimeRoute.ARCHIVE, "media new + Irrelevant archives.")
        if not w1.is_new and w2.type in {
            W2Type.DIRECT_TRADE_CANDIDATE,
            W2Type.ESCALATE_TO_BACKGROUND_AGENT,
        }:
            if w1.confidence is W1Confidence.HIGH:
                return self._decision(
                    message,
                    RuntimeRoute.ARCHIVE,
                    "media old + DTC/EBA with high W1 confidence archives.",
                )
            return self._decision(
                message,
                RuntimeRoute.A2,
                "media old + DTC/EBA with medium/low W1 confidence requires A2 review.",
                upstream_trade_path=w2.type is W2Type.DIRECT_TRADE_CANDIDATE,
            )
        return self._decision(
            message,
            RuntimeRoute.ARCHIVE,
            "media old + NULL/Irrelevant archives.",
        )

    def _plan_media_after_a2(
        self,
        message: RuntimeSourceMessage,
        *,
        w1: W1Result,
        w2: W2Result,
        a2: A2Result,
    ) -> RouteDecision:
        if not a2.is_new:
            return self._decision(message, RuntimeRoute.ARCHIVE, "A2 judged media message old.")
        if w2.type is W2Type.DIRECT_TRADE_CANDIDATE:
            return self._decision(
                message,
                RuntimeRoute.TRADING_RECORD,
                "A2 confirmed media DTC is new; route to Trading Records.",
                upstream_trade_path=True,
                requires_o3_known_events_update=True,
            )
        if w2.type is W2Type.ESCALATE_TO_BACKGROUND_AGENT:
            return self._decision(message, RuntimeRoute.O3, "A2 confirmed media EBA is new.")
        if w2.type is W2Type.NULL:
            return self._decision(
                message,
                RuntimeRoute.O3,
                "A2 confirmed media NULL is new.",
                o3_must_check_novelty_first=w1.confidence is W1Confidence.LOW,
            )
        return self._decision(message, RuntimeRoute.ARCHIVE, "A2 confirmed Irrelevant route.")

    def _plan_social_initial(
        self,
        message: RuntimeSourceMessage,
        *,
        w1: W1Result,
        w2: W2Result,
    ) -> RouteDecision:
        if not w1.is_new:
            return self._decision(message, RuntimeRoute.ARCHIVE, "social old messages archive.")
        if w2.type is W2Type.IRRELEVANT:
            return self._decision(
                message,
                RuntimeRoute.ARCHIVE,
                "social new + Irrelevant archives.",
            )
        return self._decision(
            message,
            RuntimeRoute.A2,
            "social new + non-Irrelevant must pass A2 before O3.",
            upstream_trade_path=w2.type is W2Type.DIRECT_TRADE_CANDIDATE,
        )

    def _plan_social_after_a2(
        self,
        message: RuntimeSourceMessage,
        *,
        w1: W1Result,
        w2: W2Result,
        a2: A2Result,
    ) -> RouteDecision:
        if not a2.passed_for_runtime:
            return self._decision(
                message,
                RuntimeRoute.ARCHIVE,
                "A2 rejected social novelty/truthfulness.",
            )
        return self._decision(
            message,
            RuntimeRoute.O3,
            "A2 passed social message; O3 decides final action.",
            upstream_trade_path=w2.type is W2Type.DIRECT_TRADE_CANDIDATE,
        )

    def _decision(
        self,
        message: RuntimeSourceMessage,
        route: RuntimeRoute,
        reason: str,
        *,
        upstream_trade_path: bool = False,
        requires_o3_known_events_update: bool = False,
        o3_must_check_novelty_first: bool = False,
    ) -> RouteDecision:
        return RouteDecision(
            source_message_id=message.source_message_id,
            ticker=message.ticker,
            route=route,
            reason=reason,
            upstream_trade_path=upstream_trade_path,
            requires_o3_known_events_update=requires_o3_known_events_update,
            o3_must_check_novelty_first=o3_must_check_novelty_first,
        )


def verification_failed(status: A2VerificationStatus) -> bool:
    return status in {A2VerificationStatus.LIKELY_FALSE, A2VerificationStatus.DENIED}
