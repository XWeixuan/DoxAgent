import os

import pytest


@pytest.fixture(autouse=True)
def default_unit_tests_to_memory_storage(monkeypatch: pytest.MonkeyPatch) -> None:
    if (
        os.getenv("DOXAGENT_RUN_REAL_API_TESTS") == "1"
        or os.getenv("DOXAGENT_RUN_REAL_DB_TESTS") == "1"
    ):
        return
    monkeypatch.setenv("DOXAGENT_STORAGE_MODE", "memory")
    monkeypatch.delenv("DOXAGENT_DATABASE_URL", raising=False)
