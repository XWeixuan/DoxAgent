"""Exercise real Owner revision cascades, without production writes or API secrets."""

from doxagent.message_bus_v2.schema import UpdateActor
from doxagent.site_strategy.repository import SiteStrategyRepository
from doxagent.site_strategy.seeds import bootstrap_seed
from doxagent.site_strategy.service import SiteStrategyService
from scripts.ops.configure_news_latency import plan
from tests.test_content_enrichment_hub import _setup


def test_explicit_binding_migration_survives_source_revision_cascade(tmp_path):
    repo, bus, _, _ = _setup(tmp_path)
    source = repo.get_source("yahoo_finance_news")
    repo.save_source(
        source.model_copy(
            update={
                "default_polling_config": source.default_polling_config.model_copy(
                    update={"target_interval_seconds": 60}
                ),
                "scheduler_constraints": source.scheduler_constraints.model_copy(
                    update={"max_concurrency": 1}
                ),
            }
        )
    )
    for ticker in ("MU", "INTC", "BE", "COHR", "NBIS", "CEG", "RKLB", "SNOW", "ONDS"):
        if ticker != "MU":
            bus.start_ticker(ticker)
        binding = repo.get_binding(ticker + ":yahoo_finance_news")
        repo.save_binding(
            binding.model_copy(
                update={
                    "polling": binding.polling.model_copy(update={"target_interval_seconds": 60})
                }
            )
        )
    site = SiteStrategyRepository(tmp_path / "site.db")
    bootstrap_seed(site, SiteStrategyService(site, profile_root=tmp_path / "profiles"))
    changes = plan(repo.path, site.path)
    applied_bindings = []
    for change in changes:
        if change["kind"] not in {"source", "binding"}:
            continue
        current = (
            bus.require_source(change["id"])
            if change["kind"] == "source"
            else repo.get_binding(change["id"])
        )
        assert current.version == change["expected_revision"]
        apply = bus.update_source if change["kind"] == "source" else bus.update_binding
        apply(
            change["id"], change["patch"], actor=UpdateActor.SYSTEM, reason="latency-migration-test"
        )
        if change["kind"] == "binding":
            applied_bindings.append(change["id"])
    assert len(applied_bindings) == 9
    assert all(
        repo.get_binding(identity).polling.target_interval_seconds == 30
        for identity in applied_bindings
    )
    assert repo.get_source("yahoo_finance_news").scheduler_constraints.max_concurrency == 2
