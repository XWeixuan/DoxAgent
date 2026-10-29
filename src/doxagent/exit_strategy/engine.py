"""Causal stop state transition: explicit clocks/input, no I/O or broker calls."""

from dataclasses import dataclass, field
from math import isfinite
from statistics import median

from .schema import ExitPolicy


@dataclass
class StopState:
    mfe: float = 0.0
    stop_net: float | None = None
    activated_at: float | None = None
    requested: bool = False
    recent: list[tuple[float, float]] = field(default_factory=list)
    confirmation: list[tuple[float, bool, bool]] = field(default_factory=list)
    last_mono: float | None = None
    last_utc: float | None = None
    active_since: float | None = None
    generation: object = None

    def reset_window(self):
        self.recent.clear()
        self.confirmation.clear()
        self.active_since = None

    def durable(self):
        return dict(
            mfe=self.mfe,
            stop_net=self.stop_net,
            activated_at=self.activated_at,
            requested=self.requested,
        )

    @classmethod
    def restore(cls, value):
        return cls(
            **{k: value[k] for k in ("mfe", "stop_net", "activated_at", "requested") if k in value}
        )


def observe(
    state: StopState,
    policy: ExitPolicy,
    *,
    utc: float,
    mono: float,
    pnl: float | None,
    notional: float,
    atr: float | None,
    valid: bool,
    generation=None,
) -> bool:
    """Return true exactly when this observation first confirms an exit.

    Runtime windows deliberately are not restored across process restarts.
    A missed slot never becomes six rapid catch-up observations.
    """
    if state.requested:
        return False
    if state.last_mono is not None:
        delta = mono - state.last_mono
        if delta <= 0 or delta < 4:
            return False
        if delta > 6.5 or abs((utc - state.last_utc) - delta) > 2:
            state.reset_window()
    if state.generation != generation:
        state.reset_window()
        state.generation = generation
    state.last_mono, state.last_utc = mono, utc
    valid = valid and pnl is not None and isfinite(pnl) and isfinite(notional)
    if valid:
        state.recent.append((mono, pnl))
        state.recent = state.recent[-3:]
        if len(state.recent) == 3 and mono - state.recent[0][0] <= 15:
            state.mfe = max(state.mfe, median(v for _, v in state.recent))
    else:
        state.recent.clear()
    if (
        notional > 0
        and state.mfe >= float(policy.activation_fraction) * notional
        and atr is not None
        and isfinite(atr)
        and atr >= 0
    ):
        candidate = state.mfe - max(
            float(policy.giveback_fraction) * state.mfe, float(policy.atr_multiplier) * atr
        )
        if candidate > 0:
            state.stop_net = max(state.stop_net or 0.0, candidate)
            if state.activated_at is None:
                state.activated_at = utc
    if state.stop_net is None:
        return False
    if state.active_since is None:
        state.active_since = mono
        state.confirmation.clear()
        return False
    breach = bool(valid and pnl <= state.stop_net)
    state.confirmation.append((mono, bool(valid), breach))
    state.confirmation = [x for x in state.confirmation[-6:] if x[0] > mono - 30]
    if (
        mono - state.active_since >= 30
        and len(state.confirmation) == 6
        and all(x[1] for x in state.confirmation)
        and sum(x[2] for x in state.confirmation) >= 5
        and breach
    ):
        state.requested = True
        return True
    return False
