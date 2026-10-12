"""Test only the files destined for the production overlays."""
import importlib
import importlib.util
import sys
from pathlib import Path

import pytest

root = Path(__file__).parent / "staging"
for package, name in [
    ("site_strategy", "storage"), ("site_strategy", "repository"),
    ("site_strategy", "client"), ("site_strategy", "service"), ("site_strategy", "api"),
    ("message_bus_v2", "news_adapters"), ("message_bus_v2", "market_sources"),
    ("content_enrichment", "service"),
]:
    module = f"doxagent.{package}.{name}"
    parent = importlib.import_module(f"doxagent.{package}")
    spec = importlib.util.spec_from_file_location(module, root / package / (name + ".py"))
    loaded = importlib.util.module_from_spec(spec)
    sys.modules[module] = loaded
    setattr(parent, name, loaded)
    spec.loader.exec_module(loaded)

raise SystemExit(pytest.main([
    "tests/test_source_storage_recovery_20261009.py", "tests/test_site_strategy.py",
    "tests/test_site_strategy_client.py", "tests/test_message_bus_market_sources.py",
    "tests/test_source_recovery_20261006.py", "tests/test_content_enrichment_hub.py", "-q",
]))
