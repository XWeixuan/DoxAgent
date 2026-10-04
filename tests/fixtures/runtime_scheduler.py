"""Minimal scheduler fixture without retired documents, buses, or execution services."""

from types import SimpleNamespace
from unittest.mock import Mock

from doxagent.runtime_scheduler.repository import InMemoryRuntimeSchedulerRepository
from doxagent.runtime_scheduler.service import UnifiedRuntimeSchedulerService


def scheduler_fixture():
    scheduler = UnifiedRuntimeSchedulerService(InMemoryRuntimeSchedulerRepository())
    provider = SimpleNamespace(latest_calls=0, initialize_calls=0)
    return scheduler, provider, None, Mock()
