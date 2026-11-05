"""Ticker/day W1 index publication and post-freeze Library fact visibility."""
from datetime import date
from hashlib import sha256
import json

from doxagent.event_library.provider import KnownEventIndexSnapshot, PublishedEventLibraryReader
from .providers import PublishedEventLibraryRuntimeProvider
from .schema import RuntimeFactCandidate


def event_provider(default, root):
    return PublishedEventLibraryRuntimeProvider(PublishedEventLibraryReader(root)) if root else default


VERSION = "w1-daily-known-index.v1"


def freeze_index(repository, provider, index, ticker, day, root, at, instructions):
    frozen = repository.daily_known_index(ticker, day)
    if frozen is None:
        source = event_provider(provider, root)
        build = getattr(source, "filtered_index", None)
        selected = build(ticker, index.version, day) if build else index
        if selected is None:
            raise ValueError("Daily Known Event Index source version unavailable")
        frozen = repository.daily_known_index(ticker, day, {
            "index": selected.model_dump(mode="json"), "event_library_root": root,
            "frozen_at": at.isoformat(), "instructions": instructions, "selection": "reference-or-birth14-or-fact7.v1",
        })
    return KnownEventIndexSnapshot.model_validate(frozen["index"]), frozen["event_library_root"], frozen["instructions"]


def retain_new_library_facts(repository, default_provider, ticker, day, *,
                             frozen_index, frozen_root, current_index, current_root, completed=None):
    """Already-published supplemental facts use the existing provisional wire path.

    Persist them as PROCESSED atomically so O2 never reimports them. Runtime-born
    provisional facts remain visible after their ordinary O2 settlement as before.
    """
    if frozen_root == current_root and current_index.version <= frozen_index.version:
        return
    head = (ticker.upper(), day, frozen_root, frozen_index.version, current_root, current_index.version)
    if completed is not None and head in completed:
        return
    source = event_provider(default_provider, current_root)
    changed = getattr(source, "new_library_facts", None)
    if changed is None:
        return
    maximum = event_provider(default_provider, frozen_root).max_event_numeric_id(
        ticker, frozen_index.version) or 0
    after = frozen_index.version if frozen_root == current_root else 0
    values = repository.list_provisional(ticker, day)
    def content(candidate):
        return json.dumps(candidate.model_dump(mode="json", exclude={"entities"}), sort_keys=True)
    seen = {content(value.candidate) for value in values}
    for version, event, fact in changed(ticker, after, current_index.version):
        occurred = event.occurred_at if fact.fact_occurred_at == "SAME" else fact.fact_occurred_at
        try:
            occurred = date.fromisoformat(occurred) if occurred else None
        except ValueError:
            occurred = None
        candidate = RuntimeFactCandidate(proposition=fact.proposition,
            assertion_state=fact.assertion_state, subject_time=fact.subject_time,
            occurrence_date=occurred, entities=[])
        if content(candidate) in seen:
            continue
        root_hash = sha256(str(current_root).encode()).hexdigest()[:16]
        repository.allocate_provisional(ticker=ticker, trading_date=day,
            source_message_id=f"library-bridge:{ticker}:{day}:{root_hash}:v{version}:{fact.fact_id}",
            candidate_index=0, candidate=candidate, published_max_event_numeric_id=maximum,
            already_published=True)
        seen.add(content(candidate))
    if completed is not None:
        if len(completed) >= 128:
            completed.clear()
        completed.add(head)
