from __future__ import annotations

from pathlib import Path

from cdecr.provider_resilience import (
    ModelFailureClass,
    ProviderKeyHealthRegistry,
    classify_provider_error,
    is_provider_failure,
    is_provider_pressure,
)


def test_only_throttling_is_global_provider_pressure() -> None:
    class RateLimited(RuntimeError):
        status_code = 429

    class DataInspection(RuntimeError):
        status_code = 400
        code = "provider_datainspectionfailed"

    assert is_provider_pressure(RateLimited()) is True
    assert is_provider_pressure(DataInspection()) is False


class ProviderError(RuntimeError):
    def __init__(self, code: str, status_code: int | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code


def test_failure_classifier_separates_local_output_from_provider_failure() -> None:
    assert classify_provider_error(ValueError("local validator")) is (
        ModelFailureClass.OUTPUT_LOCAL_INVALID
    )
    assert not is_provider_failure(ValueError("local validator"))
    assert classify_provider_error(ProviderError("provider_arrearage", 400)) is (
        ModelFailureClass.KEY_ARREARAGE
    )
    assert is_provider_failure(ProviderError("provider_arrearage", 400))
    assert classify_provider_error(ProviderError("invalid_parameter", 400)) is (
        ModelFailureClass.REQUEST_CONTRACT_INVALID
    )


def test_arrearage_quarantine_persists_only_fingerprint(tmp_path: Path) -> None:
    state_path = tmp_path / "provider-health.json"
    registry = ProviderKeyHealthRegistry(state_path=state_path)
    key = "secret-provider-key"
    for _ in range(3):
        registry.record_failure(key, ModelFailureClass.KEY_ARREARAGE)

    assert not registry.healthy(key)
    assert key not in state_path.read_text(encoding="utf-8")
    reloaded = ProviderKeyHealthRegistry(state_path=state_path)
    assert not reloaded.healthy(key)
    assert reloaded.ordered([key, "healthy-key"]) == ("healthy-key",)
