"""Read-only, memory-bounded runtime access to the CDECR v2 object catalogs."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import date
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

CATALOG_NAMES = (
    "companies",
    "institutions",
    "persons",
    "instruments",
    "places",
    "named_objects",
    "concepts",
    "metrics",
    "fiscal_periods",
    "units",
    "artifacts",
    "attributes",
)
RESOLUTION_POLICY_FILE = "resolution_policy.json"
_HASH_CACHE: dict[tuple[tuple[str, int, int], ...], str] = {}
_GLOBAL_QUERY_CACHE: dict[tuple[object, ...], tuple[KBMatch, ...]] = {}


@dataclass(frozen=True)
class KBMatch:
    catalog: str
    external_id: str
    name: str
    aliases: tuple[str, ...]
    kind: str | None = None
    owner_id: str | None = None
    company_id: str | None = None
    start: str | None = None
    end: str | None = None
    date: str | None = None
    period_id: str | None = None
    fiscal_year: int | None = None
    quarter: int | None = None


@dataclass(frozen=True)
class KBCandidate:
    match: KBMatch
    score: float


@dataclass(frozen=True)
class AttributeRoute:
    key: str
    aliases: tuple[str, ...]
    target: str
    use: str


@dataclass(frozen=True)
class ParticipantRouteOverride:
    catalog: str
    external_id: str


@dataclass(frozen=True)
class ResolutionPolicy:
    metric_redirects: dict[str, str]
    blocked_exact_aliases: dict[str, tuple[str, ...]]
    participant_route_overrides: dict[str, ParticipantRouteOverride]


class V2KnowledgeBase:
    """Exact v2 KB recall without materializing the large catalogs in memory."""

    def __init__(self, catalog_dir: Path | None = None) -> None:
        self.catalog_dir = catalog_dir or Path(__file__).parent / "catalogs" / "v2"
        missing = [name for name in CATALOG_NAMES if not self._path(name).is_file()]
        if missing:
            raise FileNotFoundError(f"missing v2 catalogs: {', '.join(missing)}")
        self._catalog_hash: str | None = None
        self._attributes: dict[str, AttributeRoute] | None = None
        self._resolution_policy: ResolutionPolicy | None = None
        self._query_cache: dict[tuple[object, ...], tuple[KBMatch, ...]] = {}
        self._cache_namespace = tuple(
            (
                str(self._path(name).resolve()),
                self._path(name).stat().st_size,
                self._path(name).stat().st_mtime_ns,
            )
            for name in CATALOG_NAMES
        ) + (
            (
                str((self.catalog_dir / RESOLUTION_POLICY_FILE).resolve()),
                (
                    (self.catalog_dir / RESOLUTION_POLICY_FILE).stat().st_size
                    if (self.catalog_dir / RESOLUTION_POLICY_FILE).is_file()
                    else 0
                ),
                (
                    (self.catalog_dir / RESOLUTION_POLICY_FILE).stat().st_mtime_ns
                    if (self.catalog_dir / RESOLUTION_POLICY_FILE).is_file()
                    else 0
                ),
            ),
        )

    @property
    def resolution_policy(self) -> ResolutionPolicy:
        if self._resolution_policy is not None:
            return self._resolution_policy
        path = self.catalog_dir / RESOLUTION_POLICY_FILE
        if not path.is_file():
            self._resolution_policy = ResolutionPolicy({}, {}, {})
            return self._resolution_policy
        payload = json.loads(path.read_text(encoding="utf-8"))
        if set(payload) != {
            "metric_redirects",
            "blocked_exact_aliases",
            "participant_route_overrides",
        }:
            raise ValueError("resolution_policy.json has unexpected fields")
        overrides = {
            _normalize(raw): ParticipantRouteOverride(
                catalog=str(value["catalog"]),
                external_id=str(value["id"]),
            )
            for raw, value in payload["participant_route_overrides"].items()
        }
        self._resolution_policy = ResolutionPolicy(
            metric_redirects={
                str(source): str(target)
                for source, target in payload["metric_redirects"].items()
            },
            blocked_exact_aliases={
                _normalize(raw): tuple(str(item) for item in blocked)
                for raw, blocked in payload["blocked_exact_aliases"].items()
            },
            participant_route_overrides=overrides,
        )
        return self._resolution_policy

    def metric_redirect(self, external_id: str) -> str:
        redirects = self.resolution_policy.metric_redirects
        visited: set[str] = set()
        current = external_id
        while current in redirects:
            if current in visited:
                raise ValueError("metric redirect cycle detected")
            visited.add(current)
            current = redirects[current]
        return current

    def blocked_exact_ids(self, raw_value: str) -> set[str]:
        return set(
            self.resolution_policy.blocked_exact_aliases.get(
                _normalize(raw_value), ()
            )
        )

    def participant_route_override(
        self, raw_value: str
    ) -> ParticipantRouteOverride | None:
        return self.resolution_policy.participant_route_overrides.get(
            _normalize(raw_value)
        )

    @property
    def catalog_hash(self) -> str:
        if self._catalog_hash is None:
            signature = tuple(
                (
                    name,
                    self._path(name).stat().st_size,
                    self._path(name).stat().st_mtime_ns,
                )
                for name in sorted(CATALOG_NAMES)
            )
            policy_path = self.catalog_dir / RESOLUTION_POLICY_FILE
            if policy_path.is_file():
                signature += (
                    (
                        RESOLUTION_POLICY_FILE,
                        policy_path.stat().st_size,
                        policy_path.stat().st_mtime_ns,
                    ),
                )
            cached = _HASH_CACHE.get(signature)
            if cached is not None:
                self._catalog_hash = cached
                return cached
            digest = hashlib.sha256()
            paths = [self._path(name) for name in sorted(CATALOG_NAMES)]
            if policy_path.is_file():
                paths.append(policy_path)
            for path in paths:
                digest.update(path.name.encode())
                digest.update(b"\0")
                with path.open("rb") as handle:
                    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                        digest.update(chunk)
                digest.update(b"\0")
            self._catalog_hash = digest.hexdigest()
            _HASH_CACHE[signature] = self._catalog_hash
        return self._catalog_hash

    def attribute_route(self, key: str) -> AttributeRoute | None:
        if self._attributes is None:
            routes: dict[str, AttributeRoute] = {}
            for item in _iter_json_array(self._path("attributes")):
                route = AttributeRoute(
                    key=str(item["key"]),
                    aliases=tuple(str(value) for value in item.get("aliases", [])),
                    target=str(item["target"]),
                    use=str(item["use"]),
                )
                for value in (route.key, *route.aliases):
                    routes[_normalize(value)] = route
            self._attributes = routes
        return self._attributes.get(_normalize(key))

    def lookup(
        self,
        catalog: str,
        raw_value: str,
        *,
        kind: str | None = None,
        company_id: str | None = None,
        owner_id: str | None = None,
        limit: int = 8,
    ) -> list[KBMatch]:
        if catalog not in CATALOG_NAMES:
            raise ValueError(f"unknown v2 catalog {catalog!r}")
        if limit < 1 or limit > 50:
            raise ValueError("KB lookup limit must be between 1 and 50")
        query = _query_for_catalog(catalog, raw_value, kind=kind)
        key = (catalog, query, kind, company_id, owner_id, limit)
        cached = self._query_cache.get(key)
        global_key = (self._cache_namespace, *key)
        if cached is None:
            cached = _GLOBAL_QUERY_CACHE.get(global_key)
        if cached is not None:
            self._query_cache[key] = cached
            return list(cached)
        matches: list[KBMatch] = []
        for item in _iter_json_array(self._path(catalog)):
            if kind is not None and str(item.get("kind", "")) != kind:
                continue
            if company_id is not None and str(item.get("company_id", "")) != company_id:
                continue
            if owner_id is not None and str(item.get("owner_id", "")) != owner_id:
                continue
            if query not in {
                _query_for_catalog(catalog, value, kind=kind) for value in _surfaces(catalog, item)
            }:
                continue
            matches.append(_match(catalog, item))
            if len(matches) >= limit:
                break
        result = tuple(matches)
        if len(self._query_cache) >= 4096:
            self._query_cache.pop(next(iter(self._query_cache)))
        self._query_cache[key] = result
        _remember_global_query(global_key, result)
        return list(result)

    def string_candidates(
        self,
        catalog: str,
        raw_value: str,
        *,
        kind: str | None = None,
        company_id: str | None = None,
        owner_id: str | None = None,
        limit: int = 8,
        minimum_score: float = 0.52,
    ) -> list[KBCandidate]:
        """Recall deterministic string candidates without embeddings."""

        if catalog not in CATALOG_NAMES:
            raise ValueError(f"unknown v2 catalog {catalog!r}")
        query = _query_for_catalog(catalog, raw_value, kind=kind)
        query_tokens = set(query.split())
        ranked: list[KBCandidate] = []
        for item in _iter_json_array(self._path(catalog)):
            if kind is not None and str(item.get("kind", "")) != kind:
                continue
            if company_id is not None and str(item.get("company_id", "")) != company_id:
                continue
            if owner_id is not None and str(item.get("owner_id", "")) != owner_id:
                continue
            match = _match(catalog, item)
            score = 0.0
            for surface in _surfaces(catalog, item):
                normalized = _query_for_catalog(catalog, surface, kind=kind)
                if not normalized:
                    continue
                if query == normalized:
                    score = 1.0
                    break
                tokens = set(normalized.split())
                overlap = len(query_tokens & tokens) / max(len(query_tokens | tokens), 1)
                sequence = SequenceMatcher(None, query, normalized).ratio()
                score = max(score, 0.62 * sequence + 0.38 * overlap)
            if score >= minimum_score:
                ranked.append(KBCandidate(match=match, score=score))
        ranked.sort(key=lambda item: (-item.score, item.match.external_id))
        return ranked[:limit]

    def lookup_many(
        self,
        catalogs: Sequence[str],
        raw_values: Sequence[str],
        *,
        limit_per_query: int = 8,
    ) -> dict[tuple[str, str], list[KBMatch]]:
        """Resolve many exact participant surfaces with one scan per catalog."""

        if limit_per_query < 1 or limit_per_query > 50:
            raise ValueError("KB lookup limit must be between 1 and 50")
        unique_values = list(dict.fromkeys(raw_values))
        results: dict[tuple[str, str], list[KBMatch]] = {
            (catalog, raw): [] for catalog in catalogs for raw in unique_values
        }
        for catalog in catalogs:
            if catalog not in CATALOG_NAMES:
                raise ValueError(f"unknown v2 catalog {catalog!r}")
            uncached: list[str] = []
            for raw in unique_values:
                key = (
                    catalog,
                    _query_for_catalog(catalog, raw),
                    None,
                    None,
                    None,
                    limit_per_query,
                )
                cached = self._query_cache.get(key)
                global_key = (self._cache_namespace, *key)
                if cached is None:
                    cached = _GLOBAL_QUERY_CACHE.get(global_key)
                if cached is None:
                    uncached.append(raw)
                else:
                    results[(catalog, raw)] = list(cached)
                    self._query_cache[key] = cached
            if not uncached:
                continue
            queries: dict[str, list[str]] = {}
            for raw in uncached:
                normalized = _query_for_catalog(catalog, raw)
                queries.setdefault(normalized, []).append(raw)
            for item in _iter_json_array(self._path(catalog)):
                matched_queries = {
                    normalized
                    for surface in _surfaces(catalog, item)
                    if (normalized := _query_for_catalog(catalog, surface)) in queries
                }
                if not matched_queries:
                    continue
                match = _match(catalog, item)
                for normalized in matched_queries:
                    for raw in queries[normalized]:
                        target = results[(catalog, raw)]
                        if len(target) < limit_per_query:
                            target.append(match)
            for raw in uncached:
                key = (
                    catalog,
                    _query_for_catalog(catalog, raw),
                    None,
                    None,
                    None,
                    limit_per_query,
                )
                matches = tuple(results[(catalog, raw)])
                if len(self._query_cache) >= 4096:
                    self._query_cache.pop(next(iter(self._query_cache)))
                self._query_cache[key] = matches
                _remember_global_query((self._cache_namespace, *key), matches)
        return results

    def string_candidates_many(
        self,
        catalogs: Sequence[str],
        raw_values: Sequence[str],
        *,
        limit_per_query: int = 5,
        minimum_score: float = 0.67,
    ) -> dict[tuple[str, str], list[KBCandidate]]:
        """Recall many string candidates with one token-gated scan per catalog."""

        unique_values = list(dict.fromkeys(raw_values))
        results: dict[tuple[str, str], list[KBCandidate]] = {
            (catalog, raw): [] for catalog in catalogs for raw in unique_values
        }
        for catalog in catalogs:
            queries = {raw: _query_for_catalog(catalog, raw) for raw in unique_values}
            token_to_queries: dict[str, set[str]] = {}
            for raw, query in queries.items():
                for token in set(query.split()):
                    if len(token) >= 2:
                        token_to_queries.setdefault(token, set()).add(raw)
            ranked: dict[str, dict[str, tuple[float, KBMatch]]] = {raw: {} for raw in unique_values}
            for item in _iter_json_array(self._path(catalog)):
                match = _match(catalog, item)
                scores: dict[str, float] = {}
                for surface in _surfaces(catalog, item):
                    normalized = _query_for_catalog(catalog, surface)
                    surface_tokens = set(normalized.split())
                    relevant = {
                        raw for token in surface_tokens for raw in token_to_queries.get(token, ())
                    }
                    for raw in relevant:
                        query = queries[raw]
                        query_tokens = set(query.split())
                        overlap = len(query_tokens & surface_tokens) / max(
                            len(query_tokens | surface_tokens), 1
                        )
                        sequence = SequenceMatcher(None, query, normalized).ratio()
                        score = 0.62 * sequence + 0.38 * overlap
                        scores[raw] = max(scores.get(raw, 0.0), score)
                for raw, score in scores.items():
                    if score >= minimum_score:
                        ranked[raw][match.external_id] = (score, match)
            for raw in unique_values:
                ordered = sorted(
                    ranked[raw].values(),
                    key=lambda item: (-item[0], item[1].external_id),
                )[:limit_per_query]
                results[(catalog, raw)] = [
                    KBCandidate(match=match, score=score) for score, match in ordered
                ]
        return results

    def fiscal_candidates(
        self,
        raw_value: str,
        *,
        company_id: str | None,
        published_date: date,
        limit: int = 8,
    ) -> tuple[KBMatch | None, list[KBMatch], bool]:
        """Resolve or recall an issuer-scoped fiscal period deterministically.

        Returns ``(unique_match, candidates, identifiable_period)``.
        """

        parsed = _parse_fiscal_surface(raw_value, published_date=published_date)
        if company_id is None or parsed is None:
            return None, [], False
        fiscal_year, quarter = parsed
        candidates: list[KBMatch] = []
        for item in _iter_json_array(self._path("fiscal_periods")):
            if str(item.get("company_id", "")) != company_id:
                continue
            match = _match("fiscal_periods", item)
            if fiscal_year is not None and match.fiscal_year != fiscal_year:
                continue
            if quarter is not None and match.quarter != quarter:
                continue
            if quarter is None and match.quarter is not None:
                continue
            candidates.append(match)
        if fiscal_year is None:

            def temporal_rank(item: KBMatch) -> tuple[int, int, str]:
                if item.start is None or item.end is None:
                    return (3, 10**9, item.external_id)
                start = date.fromisoformat(item.start)
                end = date.fromisoformat(item.end)
                if start <= published_date <= end:
                    return (0, (end - published_date).days, item.external_id)
                if end < published_date:
                    return (1, (published_date - end).days, item.external_id)
                return (2, (start - published_date).days, item.external_id)

            candidates.sort(key=temporal_rank)
            if candidates:
                best_rank = temporal_rank(candidates[0])
                if best_rank[0] == 0 or best_rank[1] <= 180:
                    candidates = [candidates[0]]
                else:
                    candidates = []
        candidates = candidates[:limit]
        return unique_match(candidates), candidates, True

    def _path(self, name: str) -> Path:
        return self.catalog_dir / f"{name}.json"


def _match(catalog: str, item: dict[str, Any]) -> KBMatch:
    identifier = item.get("id", item.get("key"))
    period_match = re.search(r"_FY(20\d{2})(?:_Q([1-4]))?$", str(identifier).upper())
    return KBMatch(
        catalog=catalog,
        external_id=str(identifier),
        name=str(item.get("name", identifier)),
        aliases=tuple(str(value) for value in item.get("aliases", [])),
        kind=str(item["kind"]) if item.get("kind") is not None else None,
        owner_id=str(item["owner_id"]) if item.get("owner_id") is not None else None,
        company_id=(str(item["company_id"]) if item.get("company_id") is not None else None),
        start=str(item["start"]) if item.get("start") is not None else None,
        end=str(item["end"]) if item.get("end") is not None else None,
        date=str(item["date"]) if item.get("date") is not None else None,
        period_id=str(item["period_id"]) if item.get("period_id") is not None else None,
        fiscal_year=int(period_match.group(1)) if period_match else None,
        quarter=(
            int(period_match.group(2))
            if period_match is not None and period_match.group(2)
            else None
        ),
    )


def hard_dimensions_for_match(
    match: KBMatch,
) -> dict[str, str | int | float | bool | None]:
    if match.catalog == "fiscal_periods":
        return {
            "issuer_id": match.company_id,
            "fiscal_year": match.fiscal_year,
            "quarter": match.quarter,
            "start": match.start,
            "end": match.end,
        }
    normalized = _normalize(match.name)
    if match.catalog == "metrics":
        transformation = next(
            (value for value in ("growth", "margin", "yield") if value in normalized),
            "value",
        )
        scope = (
            "segment"
            if any(value in normalized for value in ("segment", "business unit", "division"))
            else "company"
        )
        base = normalized
        for value in (
            "adjusted",
            "non gaap",
            "gaap",
            "guidance",
            "actual",
            "consensus",
            "growth",
            "margin",
            "yield",
            "total",
            "segment",
        ):
            base = re.sub(rf"\b{re.escape(value)}\b", " ", base)
        return {
            "base_measure": " ".join(base.split()),
            "scope": scope,
            "transformation": transformation,
        }
    if match.catalog == "concepts" and match.kind == "PREDICATE":
        words = _normalize(match.name).split()
        return {
            "action": words[0] if words else "",
            "object_class": " ".join(words[1:]) or "unspecified",
            "direction": next(
                (
                    word
                    for word in words
                    if word
                    in {
                        "raise",
                        "lower",
                        "increase",
                        "decrease",
                        "upgrade",
                        "downgrade",
                        "withdraw",
                    }
                ),
                "neutral",
            ),
            "lifecycle": next(
                (
                    word
                    for word in words
                    if word
                    in {
                        "plan",
                        "expect",
                        "announce",
                        "approve",
                        "start",
                        "complete",
                        "cancel",
                        "sign",
                    }
                ),
                "unspecified",
            ),
        }
    if match.catalog == "instruments":
        instrument_type = next(
            (
                value
                for value in ("index", "etf", "fund", "bond", "note", "stock", "share")
                if value in normalized
            ),
            "instrument",
        )
        return {"instrument_type": instrument_type}
    return {}


def _surfaces(catalog: str, item: dict[str, Any]) -> list[str]:
    values = [str(item.get("id", item.get("key", ""))), str(item.get("name", ""))]
    values.extend(str(value) for value in item.get("aliases", []))
    if item.get("ticker"):
        values.append(str(item["ticker"]))
    if catalog in {"metrics", "concepts"}:
        values.extend(value.replace(" ", "_") for value in list(values))
    return [value for value in values if value.strip()]


def _query_for_catalog(catalog: str, value: str, *, kind: str | None = None) -> str:
    normalized = _normalize(value, company_suffixes=catalog == "companies")
    if catalog == "metrics":
        words = [
            word
            for word in normalized.split()
            if word
            not in {
                "actual",
                "consensus",
                "guidance",
                "guided",
                "reported",
                "lower",
                "upper",
                "midpoint",
                "gaap",
                "non",
                "adjusted",
            }
        ]
        normalized = " ".join(words)
        aliases = {
            "eps": "earnings per share",
            "capex": "capital expenditures",
            "fcf": "free cash flow",
            "target price": "price target",
        }
        normalized = aliases.get(normalized, normalized)
    elif catalog == "concepts" and kind == "PREDICATE":
        words = normalized.split()
        if words and words[0] in {"report", "reported", "post", "posted", "disclose"}:
            if any(
                word
                in {
                    "revenue",
                    "eps",
                    "earnings",
                    "capex",
                    "margin",
                    "income",
                    "metric",
                }
                for word in words[1:]
            ):
                normalized = "report metric"
        elif words and words[0] in {"guide", "guided", "forecast"}:
            normalized = "guide metric"
    return normalized


def _parse_fiscal_surface(
    value: str, *, published_date: date
) -> tuple[int | None, int | None] | None:
    normalized = _normalize(value)
    if normalized in {
        "today",
        "last month",
        "latest quarterly report",
        "latest quarter",
        "current period",
    }:
        return None
    quarter_words = {"first": 1, "second": 2, "third": 3, "fourth": 4}
    quarter: int | None = None
    match = re.search(r"\b(?:q|fq)\s*([1-4])\b|\b([1-4])\s*q\b", normalized)
    if match:
        quarter = int(match.group(1) or match.group(2))
    else:
        quarter = next(
            (number for word, number in quarter_words.items() if word in normalized),
            None,
        )
    year_match = re.search(r"\b(?:fy\s*)?(20\d{2})\b", normalized)
    short_year = re.search(r"\bfy\s*(\d{2})\b", normalized)
    if year_match:
        fiscal_year = int(year_match.group(1))
    elif short_year:
        fiscal_year = 2000 + int(short_year.group(1))
    elif "current fiscal year" in normalized:
        fiscal_year = None
    elif quarter is not None and "fiscal" in normalized:
        fiscal_year = None
    else:
        return None
    return fiscal_year, quarter


def _normalize(value: str, *, company_suffixes: bool = False) -> str:
    text = unicodedata.normalize("NFKC", value).casefold().replace("_", " ")
    words = "".join(character if character.isalnum() else " " for character in text).split()
    if company_suffixes:
        suffixes = {
            "co",
            "company",
            "corp",
            "corporation",
            "inc",
            "incorporated",
            "ltd",
            "limited",
            "llc",
            "plc",
        }
        while words and words[-1] in suffixes:
            words.pop()
    return " ".join(words)


def _iter_json_array(path: Path, *, chunk_size: int = 256 * 1024) -> Iterator[dict[str, Any]]:
    """Incrementally decode a compact JSON array, retaining at most one chunk plus one row."""

    decoder = json.JSONDecoder()
    with path.open("r", encoding="utf-8") as handle:
        buffer = ""
        position = 0
        started = False
        ended = False
        while not ended:
            if position >= len(buffer) - 1:
                tail = buffer[position:]
                chunk = handle.read(chunk_size)
                buffer = tail + chunk
                position = 0
                if not chunk and not buffer.strip():
                    break
            while position < len(buffer) and buffer[position].isspace():
                position += 1
            if not started:
                if position >= len(buffer):
                    continue
                if buffer[position] != "[":
                    raise ValueError(f"catalog {path.name} must contain a JSON array")
                position += 1
                started = True
                continue
            while position < len(buffer) and (
                buffer[position].isspace() or buffer[position] == ","
            ):
                position += 1
            if position < len(buffer) and buffer[position] == "]":
                ended = True
                continue
            if position >= len(buffer):
                continue
            try:
                value, end = decoder.raw_decode(buffer, position)
            except json.JSONDecodeError:
                chunk = handle.read(chunk_size)
                if not chunk:
                    raise ValueError(f"catalog {path.name} contains invalid JSON") from None
                buffer = buffer[position:] + chunk
                position = 0
                continue
            if not isinstance(value, dict):
                raise ValueError(f"catalog {path.name} entries must be objects")
            yield value
            position = end
        if not started or not ended:
            raise ValueError(f"catalog {path.name} is not a complete JSON array")


def unique_match(matches: Sequence[KBMatch]) -> KBMatch | None:
    by_id = {item.external_id: item for item in matches}
    return next(iter(by_id.values())) if len(by_id) == 1 else None


def _remember_global_query(key: tuple[object, ...], matches: tuple[KBMatch, ...]) -> None:
    if len(_GLOBAL_QUERY_CACHE) >= 32_768:
        _GLOBAL_QUERY_CACHE.pop(next(iter(_GLOBAL_QUERY_CACHE)))
    _GLOBAL_QUERY_CACHE[key] = matches


def deterministic_match(raw_value: str, matches: Sequence[KBMatch]) -> KBMatch | None:
    """Prefer an explicit KB ID, then an exact canonical name, else require uniqueness."""

    query = _normalize(raw_value)
    id_matches = [item for item in matches if _normalize(item.external_id) == query]
    if len(id_matches) == 1:
        return id_matches[0]
    name_matches = [item for item in matches if _normalize(item.name) == query]
    if len(name_matches) == 1:
        return name_matches[0]
    return unique_match(matches)
