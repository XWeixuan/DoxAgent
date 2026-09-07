from doxagent.event_library.provider import PublishedEventLibraryReader
from doxagent.event_library.repository import EventLibraryRepository


def test_noop_readable_empty_publication_but_unknown_version_is_not_success(tmp_path):
    repository = EventLibraryRepository(tmp_path / "US" / "MU" / "event_library.sqlite3")
    reader = PublishedEventLibraryReader(tmp_path)
    assert reader.known_index("MU") is None
    assert repository.ensure_empty_publication("MU") == 1
    assert repository.ensure_empty_publication("MU") == 1
    assert reader.known_index("MU", version=1).published_at is not None
    assert reader.reference_view("MU", version=1).published_at is not None
    assert reader.event_details("MU", [], version=1).events == []
    assert reader.known_index("MU", version=99) is None
    assert reader.reference_view("MU", version=99) is None
