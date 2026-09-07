from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import pytest

from doxagent.event_library.provider import ReferenceEventViewSnapshot
from doxagent.workflows.codex_document2.inputs import PublishedEventLibraryProvider


@pytest.mark.asyncio
async def test_explicit_o2_publication_does_not_expand_or_invalidate_evidence_cutoff() -> None:
    cutoff = datetime(2026, 9, 5, tzinfo=UTC)
    reader = Mock()
    reader.reference_view.return_value = ReferenceEventViewSnapshot(
        ticker="MU",
        version=7,
        published_at=cutoff + timedelta(hours=1),
        reference_view="Reference view compiled from cutoff-frozen evidence",
        sha256="a" * 64,
    )
    pinned = PublishedEventLibraryProvider(reader, pinned_version=7, pinned_sha256="a" * 64)
    result = await pinned.load(ticker="MU", as_of=cutoff)
    assert result.status.value == "AVAILABLE"
    reader.reference_view.assert_called_with("MU", version=7)
    unpinned = await PublishedEventLibraryProvider(reader).load(ticker="MU", as_of=cutoff)
    assert unpinned.status.value == "ABSENT"
