"""Immutable base snapshots used by the one-shot stage graph."""

from __future__ import annotations

from dataclasses import dataclass

from cdecr.bulk_epoch.artifacts import canonical_hash
from cdecr.contracts import AtomicEvent, EventPackage
from cdecr.ports import CDECRRegistry


@dataclass(frozen=True)
class AtomicSnapshot:
    events: tuple[AtomicEvent, ...]
    snapshot_hash: str


@dataclass(frozen=True)
class PackageSnapshot:
    packages: tuple[EventPackage, ...]
    snapshot_hash: str


def atomic_snapshot(registry: CDECRRegistry) -> AtomicSnapshot:
    events = tuple(
        sorted(registry.list_current_atomic_events(limit=10000), key=lambda x: x.event_id)
    )
    return AtomicSnapshot(
        events=events,
        snapshot_hash=canonical_hash(
            [
                (
                    event.event_id,
                    event.version,
                    canonical_hash(event.identity_profile.model_dump(mode="json")),
                )
                for event in events
            ]
        ),
    )


def package_snapshot(registry: CDECRRegistry) -> PackageSnapshot:
    packages = tuple(
        sorted(registry.list_current_packages(limit=10000), key=lambda x: x.package_id)
    )
    return PackageSnapshot(
        packages=packages,
        snapshot_hash=canonical_hash(
            [
                (
                    package.package_id,
                    package.version,
                    canonical_hash(package.model_dump(mode="json")),
                )
                for package in packages
            ]
        ),
    )
