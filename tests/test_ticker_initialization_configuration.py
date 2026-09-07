from pathlib import Path

import pytest

from doxagent.message_bus_v2.service import MessageBusV2Service
from doxagent.ticker_initialization.configuration import CandidateConfiguration, candidate_bus_path


def test_candidate_is_not_pollable_and_install_preserves_existing_ticker(tmp_path: Path) -> None:
    config = CandidateConfiguration(tmp_path / "bus.db", "init-a", "MU")
    live = MessageBusV2Service(config.live)
    live.bootstrap()
    live.start_ticker("MU")
    live.start_ticker("NVDA")
    original = config.live.list_bindings(ticker="MU")
    other = config.live.list_bindings(ticker="NVDA")
    state = config.live.get_ticker_state("MU")
    config.prepare()
    assert config.candidate.get_ticker_state("MU") is None
    changed = original[0].model_copy(update={"enabled": False})
    config.candidate.save_binding(changed)
    config.prepare()  # recovery must not refresh the frozen snapshot
    assert config.candidate.get_binding(changed.binding_id).enabled is False
    assert config.live.list_bindings(ticker="MU") == original
    config.install()
    installed = config.live.list_bindings(ticker="MU")
    assert config.live.get_binding(changed.binding_id).enabled is False
    config.install()
    assert config.live.list_bindings(ticker="MU") == installed
    assert config.live.list_bindings(ticker="NVDA") == other
    assert config.live.get_ticker_state("MU") == state
    assert config.rollback()
    assert config.live.list_bindings(ticker="MU") == original
    assert not config.rollback()


def test_first_start_does_not_overwrite_o4_configuration(tmp_path: Path) -> None:
    config = CandidateConfiguration(tmp_path / "bus.db", "init-a", "MU")
    config.prepare()
    assert not config.candidate.list_ticker_states()
    binding = config.candidate.list_bindings(ticker="MU")[0]
    config.candidate.save_binding(binding.model_copy(update={"enabled": False}))
    config.install()
    MessageBusV2Service(config.live).bootstrap()
    MessageBusV2Service(config.live).start_ticker("MU")
    assert config.live.get_binding(binding.binding_id).enabled is False


def test_old_rollback_cannot_overwrite_new_configuration(tmp_path: Path) -> None:
    first = CandidateConfiguration(tmp_path / "bus.db", "init-a", "MU")
    first.prepare()
    first.install()
    second = CandidateConfiguration(tmp_path / "bus.db", "init-b", "MU")
    second.prepare()
    second.install()
    assert not first.rollback()
    assert second.rollback()


@pytest.mark.parametrize("identity", ["..", "../live", "a/b", "a\\b", "", "C:"])
def test_candidate_rejects_path_escape(identity: str, tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        candidate_bus_path(tmp_path / "bus.db", identity)
