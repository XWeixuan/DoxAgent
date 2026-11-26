"""Persistent per-ticker workflow structure, independent of trading permissions."""

from enum import StrEnum


class RunningMode(StrEnum):
    FULL = "FULL"
    LIGHTWEIGHT = "LIGHTWEIGHT"


def running_mode(value):
    return RunningMode(value.get("running_mode", "FULL"))


def required_artifact_roles(mode):
    return (
        ("document1", "event_library", "monitoring_configuration")
        if RunningMode(mode) is RunningMode.LIGHTWEIGHT
        else ("document1", "document2", "event_library", "document3", "monitoring_configuration")
    )
