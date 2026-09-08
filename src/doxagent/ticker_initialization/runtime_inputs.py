"""Resolve a whole Case's upstream inputs from one local activation pointer read."""

from doxagent.event_library.provider import PublishedEventLibraryReader
from doxagent.persistent_runtime_v2.providers import RuntimeInputSnapshot
from doxagent.workflows.codex_document3.repository import Document3PolicyRepository
from doxagent.workflows.codex_document3.runtime_projection import Document3RuntimeProjectionConsumer

from .repository import InitializationRepository
from .schema import InitializationError


class ActivatedRuntimeInputs:
    def __init__(
        self,
        control: InitializationRepository,
        events: PublishedEventLibraryReader,
        policies: Document3PolicyRepository,
        runtime_control=None,
    ) -> None:
        self.control = control
        self.events = events
        self.policies = Document3RuntimeProjectionConsumer(policies)
        self.runtime_control = runtime_control

    def __call__(self, ticker: str) -> RuntimeInputSnapshot | None:
        revision = self.control.active_revision(ticker)
        state = self.runtime_control.get(ticker) if self.runtime_control else None
        if state and state.get("initialization_incomplete"):
            if not state.get("activation_id"):
                return None
            revision = self.control.revision(state["activation_id"])
        if revision is None:
            return None
        return self.from_revision(ticker, revision)

    def for_admission(self, ticker: str, revision_id: str) -> RuntimeInputSnapshot | None:
        """Validate a candidate without making it the source for ordinary new Cases."""
        revision = self.control.active_revision(ticker)
        if not revision or revision["revision_id"] != revision_id:
            return None
        return self.from_revision(ticker, revision)

    def from_revision(self, ticker: str, revision: dict) -> RuntimeInputSnapshot:
        try:
            refs = revision["artifacts"]
            event_version = int(refs["event_library"]["version"])
            policy_version = int(refs["document3"]["version"])
            d1_run = refs["document1"]["run_id"]
            d2_run = refs["document2"]["run_id"]
        except (KeyError, TypeError, ValueError) as exc:
            raise InitializationError(
                "active revision is missing required runtime references"
            ) from exc
        if (
            not isinstance(d1_run, str)
            or not d1_run
            or not isinstance(d2_run, str)
            or not d2_run
            or event_version < 1
            or policy_version < 1
        ):
            raise InitializationError("active revision has invalid runtime references")
        # Do not read current heads again or fall back when a pinned artifact is absent.
        root = refs["event_library"].get("root")
        events = PublishedEventLibraryReader(root) if root else self.events
        return RuntimeInputSnapshot(
            index=events.known_index(ticker, version=event_version),
            projection=self.policies.version(ticker, policy_version),
            activation_revision_id=revision["revision_id"],
            document1_run_id=d1_run,
            document2_run_id=d2_run,
            event_library_root=root,
            visibility_day=revision.get("runtime_metadata", {}).get("visibility_day"),
        )
