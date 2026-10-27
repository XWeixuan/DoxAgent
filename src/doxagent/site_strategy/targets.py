"""Exact target ownership across controller connections; no age/blank-page sweeps."""

from .governance_models import ChallengeTask, OwnedBrowserTarget
from .governance_repository import GovernanceRepository
from .schema import utc_now
from .storage import registry_io


class TargetOwner:
    def __init__(self, repository):
        self.store = GovernanceRepository(repository)

    async def event(self,action,payload):
        return await registry_io(self._event,action,payload)

    def _event(self, action, payload):
        if action == "allocate":
            return self.allocate(**payload).model_dump(mode="json")
        if action == "bind":
            return self.bind(**payload).model_dump(mode="json")
        if action == "list":
            return [t.model_dump(mode="json") for t in self.list_for_instance(**payload)]
        if action == "close_claim":
            with self.store.repository.transaction() as connection:
                target = self.store.get_target(payload["allocation_id"])
                expected_ref = payload.get("owner_ref")
                if not target or target.state in {"LOST", "CLOSED"} or (
                    target.owner_kind != "BUSINESS" and target.owner_ref != expected_ref
                ):
                    return {"claimed": False}
                self.store._write_target(connection, target.model_copy(update={"state": "CLOSE_PENDING"}))
                return {"claimed": True}
        if action == "state":
            target = self.store.get_target(payload["allocation_id"])
            if target is None:
                raise KeyError(payload["allocation_id"])
            if payload["state"] not in {"LIVE", "CLOSE_PENDING", "CLOSED", "LOST"}:
                raise ValueError("invalid target transition")
            if payload["state"] == "LOST":
                task = self.store.current_challenge(target.identity_id)
                if task and task.allocation_id == target.allocation_id:
                    self.store.save_challenge(task.model_copy(update={"state": "TARGET_LOST"}))
            return self.store.save_target(
                target.model_copy(
                    update={
                        "state": payload["state"],
                        "updated_at": utc_now(),
                    }
                )
            ).model_dump(mode="json")
        if action == "login":
            return self.store.handoff_login(
                payload["allocation_id"], payload["session_id"], payload["site_id"]
            ).model_dump(mode="json")
        if action == "handoff":
            return self.store.handoff_challenge(ChallengeTask.model_validate(payload)).model_dump(
                mode="json",
            )
        if action == "recovery":
            previous = self.store.get_recovery(payload["identity_id"])
            result = self.store.begin_recovery(payload)
            if previous is None or previous.recovery_id != result.recovery_id:
                from .schema import AccessEvent

                self.store.repository.append_event(AccessEvent(
                    operation_id=result.recovery_id, site_id=result.site_id, category="RECOVERY_STARTED",
                    payload={"recovery_id": result.recovery_id, "identity_id": result.identity_id,
                             "handling_mode": result.handling_mode, "kind": result.assessment.get("kind")},
                ))
            return result.model_dump(mode="json")
        raise ValueError("unsupported ownership action")

    def allocate(self, identity_id, instance_id, epoch):
        return self.store.save_target(
            OwnedBrowserTarget(
                identity_id=identity_id,
                browser_instance_id=instance_id,
                worker_epoch=epoch,
            )
        )

    def bind(self, allocation_id, target_id):
        target = self.store.get_target(allocation_id)
        if target is None:
            raise KeyError(allocation_id)
        return self.store.save_target(
            target.model_copy(
                update={
                    "target_id": target_id,
                    "state": "LIVE",
                    "updated_at": utc_now(),
                }
            )
        )

    def list_for_instance(self, identity_id, instance_id):
        repo = self.store.repository
        with repo._lock:
            rows = repo._connection.execute(
                "select data_json from owned_browser_targets "
                "where identity_id=? and browser_instance_id=? and state not in ('CLOSED','LOST')",
                (identity_id, instance_id),
            ).fetchall()
        values = [OwnedBrowserTarget.model_validate_json(row[0]) for row in rows]
        recovery = self.store.get_recovery(identity_id)
        task = self.store.current_challenge(identity_id)
        sessions = {s.allocation_id for s in self.store.list_sessions() if s.state == "OPEN"}
        for index, value in enumerate(values):
            if (value.owner_kind == "RECOVERY" and recovery and value.owner_ref == recovery.recovery_id
                    and value.allocation_id != recovery.original_allocation_id
                    and (not task or task.allocation_id != value.allocation_id)
                    and value.allocation_id not in sessions):
                values[index] = self.store.save_target(value.model_copy(update={
                    "state": "CLOSE_PENDING"}))
        return values

    async def reconcile(self, cdp, identity_id, instance_id):
        """Find lost create replies, retry exact business closes, preserve humans."""
        result = await cdp.send("Target.getTargets")
        live = {str(t["targetId"]): t for t in result["targetInfos"]}
        outcomes = []
        for target in (await registry_io(self.list_for_instance, identity_id, instance_id)):
            if not target.target_id:
                matched = [key for key, info in live.items() if info.get("url") == target.marker]
                if len(matched) == 1:
                    target = (await registry_io(self.bind, target.allocation_id, matched[0]))
            if target.target_id not in live:
                target = target.model_copy(update={"state": "LOST", "updated_at": utc_now()})
                (await registry_io(self.store.save_target, target))
                task = (await registry_io(self.store.current_challenge, identity_id))
                if task and task.allocation_id == target.allocation_id:
                    (await registry_io(self.store.save_challenge, task.model_copy(update={"state": "TARGET_LOST"})))
            elif target.owner_kind == "BUSINESS" or target.state == "CLOSE_PENDING":
                claimed = await self.event("close_claim", {"allocation_id": target.allocation_id,
                    "owner_ref": target.owner_ref if target.state == "CLOSE_PENDING" else None})
                if not claimed["claimed"]:
                    continue
                pending = (await registry_io(self.store.get_target, target.allocation_id))
                try:
                    await cdp.send("Target.closeTarget", {"targetId": target.target_id})
                except Exception:
                    pass  # Durable pending record survives this connection.
                else:
                    target = (await registry_io(self.store.save_target, pending.model_copy(update={"state": "CLOSED"})))
            outcomes.append(target)
        return outcomes

    def handoff(self, target: OwnedBrowserTarget, **details):
        return self.store.handoff_challenge(
            ChallengeTask(
                allocation_id=target.allocation_id,
                identity_id=target.identity_id,
                browser_instance_id=target.browser_instance_id,
                original_target_id=target.target_id,
                **details,
            )
        )
