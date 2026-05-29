"""Stateless validation helpers for Phase 1 contract objects."""

from collections.abc import Iterable

from doxagent.models.blackboard import BlackboardTarget, Delegation, Objection


def same_target(left: BlackboardTarget, right: BlackboardTarget) -> bool:
    return left.model_dump() == right.model_dump()


def has_unresolved_objections(
    objections: Iterable[Objection],
    target: BlackboardTarget | None = None,
) -> bool:
    return any(
        objection.is_unresolved and (target is None or same_target(objection.target, target))
        for objection in objections
    )


def has_blocking_delegations(
    delegations: Iterable[Delegation],
    target: BlackboardTarget | None = None,
) -> bool:
    return any(
        delegation.is_blocking
        and (target is None or same_target(delegation.blocking_scope, target))
        for delegation in delegations
    )


def can_promote_target(
    target: BlackboardTarget,
    objections: Iterable[Objection],
    delegations: Iterable[Delegation],
) -> bool:
    return not has_unresolved_objections(objections, target) and not has_blocking_delegations(
        delegations,
        target,
    )
