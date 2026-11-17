"""Ticker/day W1 index publication and post-freeze Library fact visibility."""
from datetime import date
from hashlib import sha256
from functools import lru_cache
from threading import RLock
import json

from doxagent.event_library.provider import KnownEventIndexSnapshot, PublishedEventLibraryReader
from .providers import PublishedEventLibraryRuntimeProvider
from .schema import RuntimeFactCandidate
from doxagent.w3_timing import observed


def event_provider(default, root):
    return PublishedEventLibraryRuntimeProvider(PublishedEventLibraryReader(root)) if root else default


VERSION = "w1-daily-known-index.v1"


def fact_candidate(event, fact):
    occurred = event.occurred_at if fact.fact_occurred_at == "SAME" else fact.fact_occurred_at
    try:
        occurred = date.fromisoformat(occurred) if occurred else None
    except ValueError:
        occurred = None
    return RuntimeFactCandidate(proposition=fact.proposition,
        assertion_state=fact.assertion_state, subject_time=fact.subject_time,
        occurrence_date=occurred, entities=[])


def fact_content(candidate):
    value = candidate.model_dump(mode="json", exclude={"entities"})
    for field in ("proposition", "subject_time"):
        if isinstance(value.get(field), str):
            value[field] = " ".join(value[field].split())
    return json.dumps(value, sort_keys=True)


@observed('case.frozen_fact_contents')
def frozen_fact_contents(default_provider, ticker, root, version, cache=None):
    """All frozen Library facts, including Events omitted from the compact index."""
    key = (ticker.upper(), root, version)
    if cache is not None and key in cache:
        return cache[key]
    source = event_provider(default_provider, root)
    read = getattr(source, "published_facts", None)
    contents = frozenset(fact_content(fact_candidate(event, fact))
                         for event, fact in read(ticker, version)) if read else frozenset()
    if cache is not None:
        if len(cache) >= 128:
            cache.clear()
        cache[key] = contents
    return contents


def visible_library_bridges(values, frozen_contents):
    # Preserve raw records and historical frozen Case inputs. Only future Case
    # snapshots drop bridge content that was already in the frozen Library.
    return [value for value in values
            if not value.source_message_id.startswith("library-bridge:")
            or fact_content(value.candidate) not in frozen_contents]


@observed('case.daily_index')
def freeze_index(repository, provider, index, ticker, day, root, at, instructions):
    frozen = repository.daily_known_index(ticker, day)
    if frozen is None:
        source = event_provider(provider, root)
        build = getattr(source, "filtered_index", None)
        prepared = getattr(repository, 'prepared_known_index', None)
        identity = sha256(repr((ticker.upper(), str(day), root, index.version)).encode()).hexdigest()
        raw = prepared(identity) if prepared else None
        selected = KnownEventIndexSnapshot.model_validate(raw) if raw else (build(ticker, index.version, day) if build else index)
        if selected is None:
            raise ValueError("Daily Known Event Index source version unavailable")
        frozen = repository.daily_known_index(ticker, day, {
            "index": selected.model_dump(mode="json"), "event_library_root": root,
            "frozen_at": at.isoformat(), "instructions": instructions, "selection": "reference-or-birth14-or-fact7.v1",
        })
    return KnownEventIndexSnapshot.model_validate(frozen["index"]), frozen["event_library_root"], frozen["instructions"]


@lru_cache(maxsize=128)
def _bridge_lock(head):
    return RLock()


def _retain_new_library_facts(repository, default_provider, ticker, day, *,
                             frozen_index, frozen_root, current_index, current_root, completed=None,
                             frozen_contents=None):
    """Already-published supplemental facts use the existing provisional wire path.

    Persist them as PROCESSED atomically so O2 never reimports them. Runtime-born
    provisional facts remain visible after their ordinary O2 settlement as before.
    """
    if frozen_root == current_root and current_index.version == frozen_index.version:
        return
    head = (ticker.upper(), str(day), frozen_root, frozen_index.version, current_root, current_index.version)
    if completed is not None and head in completed:
        return
    source = event_provider(default_provider, current_root)
    changed = getattr(source, "published_facts", None)
    if changed is None:
        return
    maximum = event_provider(default_provider, frozen_root).max_event_numeric_id(
        ticker, frozen_index.version) or 0
    values = repository.list_provisional(ticker, day)
    if frozen_contents is None:
        frozen_contents = frozen_fact_contents(default_provider, ticker, frozen_root, frozen_index.version)
    seen = {fact_content(value.candidate) for value in values} | frozen_contents
    batch = []
    for event, fact in changed(ticker, current_index.version):
        candidate = fact_candidate(event, fact)
        if fact_content(candidate) in seen:
            continue
        content_hash = sha256(fact_content(candidate).encode()).hexdigest()
        batch.append(dict(ticker=ticker, trading_date=day,
            source_message_id=f"library-bridge:{ticker}:{day}:{content_hash}",
            candidate_index=0, candidate=candidate, published_max_event_numeric_id=maximum,
            already_published=True))
        seen.add(fact_content(candidate))
    apply = getattr(repository, "apply_library_bridge", None)
    if apply:
        apply(sha256(repr(head).encode()).hexdigest(), batch)
    else:
        for item in batch:
            repository.allocate_provisional(**item)
    if completed is not None:
        if len(completed) >= 128:
            completed.clear()
        completed.add(head)

@observed('case.library_fact_bridge')
def retain_new_library_facts(repository, default_provider, ticker, day, **kwargs):
    head = (ticker.upper(), str(day), kwargs['frozen_root'], kwargs['frozen_index'].version,
            kwargs['current_root'], kwargs['current_index'].version)
    identity = sha256(repr(head).encode()).hexdigest()
    with _bridge_lock(head):
        check = getattr(repository, 'library_bridge_applied', None)
        if check and check(identity):
            return
        return _retain_new_library_facts(repository, default_provider, ticker, day, **kwargs)
