"""Bounded overlays on accepted images; preserve the running initializer and browser services."""

import dynamic_ownership_release as release

if __name__ == "__main__":
    release.ROLES = [
        "v2-api",
        "v2-message-collector",
        "v2-message-bus",
        "v2-content-enrichment",
        "v2-o4",
    ]
    release.FILES = [
        "src/doxagent/message_bus_v2/" + p
        for p in (
            "securities_sources.py",
            "securities_parsers.py",
            "securities_http.py",
            "securities_body.py",
            "technology_sources.py",
            "technology_body.py",
            "public_listing.py",
            "manifests.py",
            "polling_cadence.py",
            "service.py",
            "source_catalog.json",
            "corporate_ir/catalog.py",
        )
    ]
    # Docker merges only the bounded staged files into this existing directory.
    release.COPY_ROOTS = ("src/doxagent/message_bus_v2",)
    release.main()
