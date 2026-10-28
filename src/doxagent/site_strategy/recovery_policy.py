"""Demand-only publishers do not create permanent browser recovery work."""


def demand_only(scope_id: str) -> bool:
    return scope_id.startswith("publisher:")


def public_identity(identity) -> bool:
    return getattr(identity, "execution_mode", "identity") in {"public_context", "public_identity"}


def background_recovery(recovery) -> bool:
    return recovery is not None and recovery.phase != "RESOLVED" and recovery.automation_active
