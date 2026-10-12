"""Run focused tests against the exact three files destined for the overlay."""
import importlib
import importlib.util
import sys
from pathlib import Path

import pytest

root = Path(__file__).parent / "staging"
for module, name in [
    ("doxagent.message_bus_v2.reuters_sources", "reuters_sources.py"),
    ("doxagent.site_strategy.runtime", "runtime.py"),
    ("doxagent.site_strategy.service", "service.py"),
]:
    spec = importlib.util.spec_from_file_location(module, root / name)
    loaded = importlib.util.module_from_spec(spec)
    sys.modules[module] = loaded
    parent, attribute = module.rsplit(".", 1)
    setattr(importlib.import_module(parent), attribute, loaded)
    spec.loader.exec_module(loaded)

raise SystemExit(pytest.main([
    "tests/test_source_recovery_20261006.py",
    "tests/test_site_strategy.py",
    "tests/test_message_source_recurring_repair.py", "-q",
]))
