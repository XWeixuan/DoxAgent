"""Desired configuration is activated only by the existing order writer."""

from __future__ import annotations

import json

from doxagent.persistent_runtime_v2.journal import digest

from .capital import CapitalLedger, get, put
from .planning import TradingConfig
from .schema import historical_profile, legacy_entry_profile

SCHEMA_VERSION = 2


def account_pin(db, environment):
    revision = get(db, "execution_account_profiles", environment)
    if revision:
        row = db.execute("SELECT payload FROM te_profiles WHERE revision=?", (revision,)).fetchone()
        if row:
            return {"revision": revision, "profile": json.loads(row[0])}
    return None


def active_in(db, ticker):
    return get(db, "execution_config_active", ticker)


def banned_in(db, ticker, now):
    from doxagent.semantic_clock import semantic_day

    active = active_in(db, ticker)
    day = semantic_day(now).isoformat()
    if active and day in active["configuration"].get("banned_semantic_dates", []):
        return dict(
            suppressed_semantic_date=day, suppression_policy_revision=active["applied_revision"]
        )
    return None


class ExecutionConfiguration:
    def __init__(self, journal):
        self.journal = journal

    def read(self, ticker):
        with self.journal.transaction(write=False) as db:
            desired = get(db, "execution_config_desired", ticker)
            active = active_in(db, ticker)
            accounts = {}
            for environment in ("LIVE", "PAPER"):
                pin = account_pin(db, environment)
                if pin:
                    profile = historical_profile(pin["profile"])
                    accounts[environment] = {
                        "account_type": profile.account_type,
                        "revision": pin["revision"],
                    }
            return {
                "ticker": ticker,
                "revision": (desired or {}).get("revision", 0),
                "configuration": (desired or {}).get(
                    "configuration", TradingConfig().model_dump(mode="json")
                ),
                "active": {k: v for k, v in active.items() if k != "execution_pin"}
                if active
                else None,
                "accounts": accounts,
                "error": get(db, "execution_config_errors", ticker),
            }

    def save(self, ticker, configuration, expected_revision, actor):
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError("invalid configuration revision")
        config = TradingConfig.model_validate(configuration)
        with self.journal.transaction() as db:
            desired = get(db, "execution_config_desired", ticker)
            if (desired or {}).get("revision", 0) != expected_revision:
                raise ValueError("CONFIGURATION_REVISION_CHANGED")
            pin = account_pin(db, config.environment)
            if pin:
                config.validate_account(historical_profile(pin["profile"]).account_type)
            put(
                db,
                "execution_config_desired",
                ticker,
                dict(
                    revision=expected_revision + 1,
                    configuration=config.model_dump(mode="json"),
                    actor=actor,
                    saved_at=self.journal.clock().isoformat(),
                ),
            )
        return self.read(ticker)

    def apply(self, repo):
        """One short local transaction; connectivity does not masquerade as config ACK."""
        for environment in ("LIVE", "PAPER"):
            desired = self.journal.get("execution_capital_desired", environment)
            applied = self.journal.get("execution_capital_applied", environment)
            if desired and (not applied or applied["identity"] != desired["identity"]):
                with repo.transaction(write=False) as db:
                    pin = account_pin(db, environment)
                if (
                    pin
                    and desired.get("account", pin["profile"]["expected_account_id"])
                    == pin["profile"]["expected_account_id"]
                ):
                    CapitalLedger(repo).allocate(
                        pin["profile"]["expected_account_id"],
                        desired["amount"],
                        "allocation:" + desired["identity"],
                        desired["reason"],
                        actor=desired["actor"],
                    )
                    self.journal.set(
                        "execution_capital_applied",
                        environment,
                        dict(
                            identity=desired["identity"],
                            amount=desired["amount"],
                            applied_at=self.journal.clock().isoformat(),
                        ),
                    )
        with repo.transaction() as db:
            # Bootstrap only registered binding profiles, never guess an account/port.
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='v2_mode_binding'").fetchone():
                for (payload,) in db.execute(
                    "SELECT payload FROM v2_mode_binding WHERE ticker='*'"
                ).fetchall():
                    pin = json.loads(payload)
                    profile = pin.get("profile", {})
                    env = profile.get("environment")
                    if env and account_pin(db, env) is None:
                        put(db, "execution_account_profiles", env, pin["revision"])
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='v2_ticker_control'").fetchone():
                for (ticker,) in db.execute("SELECT ticker FROM v2_ticker_control").fetchall():
                    if get(db, "execution_config_desired", ticker) is None:
                        put(
                            db,
                            "execution_config_desired",
                            ticker,
                            dict(
                                revision=1,
                                configuration=TradingConfig().model_dump(mode="json"),
                                actor="default",
                                saved_at=self.journal.clock().isoformat(),
                            ),
                        )
            for ticker, payload in db.execute(
                "SELECT key,payload FROM runtime_values WHERE namespace='execution_config_desired'"
            ).fetchall():
                desired = json.loads(payload)
                try:
                    config = TradingConfig.model_validate(desired["configuration"])
                    pin = account_pin(db, config.environment)
                    if pin is None:
                        raise ValueError("ACCOUNT_NOT_CONFIGURED")
                    profile = historical_profile(pin["profile"])
                    # New imports use the current schema. Archived pins remain EXIT-only.
                    if legacy_entry_profile(pin["profile"]):
                        raise ValueError("LEGACY_PROFILE_REQUIRES_REIMPORT")
                    config.validate_account(profile.account_type)
                    if not db.execute(
                        "SELECT 1 FROM te_capital_events WHERE account=? AND kind='ALLOCATION' "
                        "LIMIT 1",
                        (profile.expected_account_id,),
                    ).fetchone():
                        raise ValueError("STRATEGY_CAPITAL_UNCONFIGURED")
                    revision = digest(
                        dict(
                            account_revision=pin["revision"],
                            ticker_revision=desired["revision"],
                            plan_schema_version=SCHEMA_VERSION,
                        )
                    )
                    current = active_in(db, ticker)
                    if not current or current["applied_revision"] != revision:
                        put(
                            db,
                            "execution_config_active",
                            ticker,
                            dict(
                                configuration=config.model_dump(mode="json"),
                                ticker_revision=desired["revision"],
                                account_revision=pin["revision"],
                                applied_revision=revision,
                                plan_schema_version=SCHEMA_VERSION,
                                execution_pin=pin,
                                applied_at=self.journal.clock().isoformat(),
                            ),
                        )
                        from doxagent.v2_control.repository import ControlRepository, control_in

                        state = control_in(db, ticker)
                        if state and state["mode"] != "MESSAGE_MONITORING":
                            state.update(
                                mode=config.environment + "_TRADING",
                                requested_mode=config.environment + "_TRADING",
                                revision=state["revision"] + 1,
                            )
                            ControlRepository(self.journal)._save(
                                db, state, "trading.configuration.applied"
                            )
                    db.execute(
                        "DELETE FROM runtime_values WHERE namespace='execution_config_errors' "
                        "AND key=?",
                        (ticker,),
                    )
                except ValueError as exc:
                    error = dict(revision=desired["revision"], error=str(exc))
                    if get(db, "execution_config_errors", ticker) != error:
                        put(db, "execution_config_errors", ticker, error)
