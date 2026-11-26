"""Narrow per-role overlays using the existing locked release owner."""
import dynamic_ownership_release as release

if __name__ == "__main__":
    release.ROLES = ["v2-api", "v2-message-collector", "v2-message-bus", "v2-content-enrichment", "v2-initialization", "v2-o4"]
    release.FILES = ["src/doxagent/message_bus_v2/" + name for name in (
        "technology_sources.py", "public_listing.py", "google_news_rules.py", "live.py",
        "manifests.py", "service.py", "polling_cadence.py", "energy_catalog.py", "source_catalog.json",
    )]
    release.COPY_ROOTS = ("src/doxagent/message_bus_v2",)
    release.main()
