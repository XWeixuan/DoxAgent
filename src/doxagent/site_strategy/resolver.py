"""Deterministic URL-to-site resolution with a bounded generic fallback."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from urllib.parse import urlsplit

from .repository import SiteStrategyRepository
from .schema import (
    DomainRule,
    MatchType,
    ResolvedSite,
    SiteStrategyHead,
    SiteStrategySpec,
    normalize_host,
)


@dataclass(frozen=True)
class _Match:
    head: SiteStrategyHead
    spec: SiteStrategySpec
    rule: DomainRule
    exact: bool
    length: int


class SiteResolver:
    def __init__(self, repository: SiteStrategyRepository) -> None:
        self.repository = repository

    def validate_no_conflicts(self, candidate: SiteStrategySpec) -> None:
        strategies = [
            spec
            for _, spec in self.repository.list_active_strategies()
            if spec.site_id not in {candidate.site_id, "generic"}
        ]
        all_specs = [*strategies, candidate]
        rules: list[tuple[str, DomainRule]] = []
        for spec in all_specs:
            rules.extend((spec.site_id, rule) for rule in spec.domains if not rule.exclude)
        for index, (site_a, rule_a) in enumerate(rules):
            for site_b, rule_b in rules[index + 1 :]:
                if site_a == site_b:
                    continue
                if rule_a.match == rule_b.match and rule_a.host == rule_b.host:
                    raise ValueError(
                        f"domain ownership conflict: {rule_a.host} belongs to {site_a} and {site_b}"
                    )

    def resolve(self, url: str, *, revision: int | None = None) -> ResolvedSite:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("url must be absolute HTTP(S)")
        host = normalize_host(parsed.hostname)
        matches: list[_Match] = []
        for head, spec in self.repository.list_active_strategies():
            if spec.site_id == "generic":
                continue
            excluded = any(rule.exclude and rule.matches(host) for rule in spec.domains)
            if excluded:
                continue
            for rule in spec.domains:
                if not rule.exclude and rule.matches(host):
                    matches.append(
                        _Match(
                            head=head,
                            spec=spec,
                            rule=rule,
                            exact=rule.match is MatchType.EXACT,
                            length=len(rule.host),
                        )
                    )
        if matches:
            matches.sort(key=lambda item: (item.exact, item.length), reverse=True)
            top = matches[0]
            equally_specific = [
                item for item in matches if item.exact == top.exact and item.length == top.length
            ]
            if len({item.spec.site_id for item in equally_specific}) > 1:
                raise RuntimeError(f"ambiguous site ownership for {host}")
            selected: SiteStrategySpec = top.spec
            if revision is not None:
                revision_spec = self.repository.get_strategy(top.spec.site_id, revision)
                if revision_spec is None:
                    raise ValueError(
                        f"site strategy revision not found: {top.spec.site_id}@{revision}"
                    )
                selected = revision_spec
            return self._resolved(
                selected,
                top.head,
                runtime_key=top.spec.site_id,
                evidence={
                    "host": host,
                    "match": top.rule.match.value,
                    "rule": top.rule.host,
                    "role": top.rule.role.value,
                },
            )
        generic_head = self.repository.get_head("generic")
        generic = self.repository.get_strategy("generic")
        if generic_head is None or generic is None:
            raise RuntimeError("generic site strategy is not configured")
        if revision is not None:
            generic_revision = self.repository.get_strategy("generic", revision)
            if generic_revision is None:
                raise ValueError(f"site strategy revision not found: generic@{revision}")
            generic = generic_revision
        key = hashlib.sha256(host.encode("ascii")).hexdigest()[:16]
        return self._resolved(
            generic,
            generic_head,
            runtime_key=f"generic:{host}:{key}",
            evidence={"host": host, "match": "generic"},
        )

    @staticmethod
    def _resolved(
        spec: SiteStrategySpec,
        head: SiteStrategyHead,
        *,
        runtime_key: str,
        evidence: dict[str, object],
    ) -> ResolvedSite:
        return ResolvedSite(
            site_id=spec.site_id,
            runtime_key=runtime_key,
            strategy_revision=spec.revision,
            enabled=head.enabled,
            match_evidence=evidence,
            body=spec.body,
            crawler=spec.crawler,
            auth=spec.auth,
            access=spec.access,
        )

    def supports_host(self, site_id: str, host: str) -> bool:
        spec = self.repository.get_strategy(site_id)
        if spec is None:
            return False
        normalized = normalize_host(host)
        if any(rule.exclude and rule.matches(normalized) for rule in spec.domains):
            return False
        return any(rule.matches(normalized) for rule in [*spec.domains, *spec.support_hosts])


__all__ = ["SiteResolver"]
