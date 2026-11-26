"""Narrow per-role overlays using the existing locked release owner."""
import dynamic_ownership_release as release

if __name__ == "__main__":
    # Initialization is executing long research jobs; live Bus owns candidate installation.
    release.ROLES = ["v2-api", "v2-message-collector", "v2-message-bus", "v2-content-enrichment", "v2-o4"]
    release.FILES = ["src/doxagent/message_bus_v2/" + name for name in (
        "technology_sources.py", "public_listing.py", "google_news_rules.py", "live.py",
        "manifests.py", "service.py", "polling_cadence.py", "energy_catalog.py", "source_catalog.json",
    )]
    release.FILES += ["src/doxagent/ticker_initialization/configuration.py"]
    release.COPY_ROOTS = ("src/doxagent/message_bus_v2", "src/doxagent/ticker_initialization")
    release.main()
