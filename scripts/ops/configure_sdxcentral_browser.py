"""Provision only SDxCentral's source-owned Chrome pair; preserve existing revisions."""

import argparse
import json

from doxagent.site_strategy.governance_models import SourceAccessScope
from doxagent.site_strategy.governance_repository import GovernanceRepository
from doxagent.site_strategy.repository import SiteStrategyRepository
from doxagent.site_strategy.schema import (
    AccessCombination, AccessPolicy, AuthPolicy, BodyStrategyPointer,
    BrowserEnvironment, BrowserIdentityLifecycle, BrowserIdentitySpec, BrowserProfile,
    DomainRule, SiteStrategySpec,
)

SCOPE = "source:sdxcentral_news"
BODY_CANARY = "https://www.sdxcentral.com/news/broadcom-kills-vmware-telco-cloud-platform-standalone-license-option/"


def configure(repository, *, primary_egress="de-standard-1", fallback_egress="nl-standard-2"):
    governance = GovernanceRepository(repository)
    members = []
    for role, egress_id in (("main", primary_egress), ("backup", fallback_egress)):
        if repository.get_egress(egress_id) is None:
            raise ValueError("missing egress:" + egress_id)
        identity_id = "sdxcentral-news-" + role
        members.append(identity_id)
        if repository.get_identity(identity_id):
            continue
        environment = BrowserEnvironment(timezone_id="Europe/Berlin")
        repository.save_profile(BrowserProfile(profile_id=identity_id, site_id="sdxcentral",
            bound_egress_id=egress_id, directory_key=identity_id, environment=environment))
        repository.apply_identity(BrowserIdentitySpec(identity_id=identity_id,
            owner_scope_id=SCOPE, runtime_kind="external_chrome", profile_id=identity_id,
            egress_id=egress_id, browser_release="google-chrome-153.0.8010.52",
            environment=environment, lifecycle=BrowserIdentityLifecycle(
                idle_seconds=43200 if role == "main" else 120)),
            expected_revision=None, actor="ops:sdxcentral-browser")
    if repository.get_strategy("sdxcentral") is None:
        repository.apply_strategy(SiteStrategySpec(site_id="sdxcentral", display_name="SDxCentral",
            domains=[DomainRule(host="sdxcentral.com", match="suffix")],
            access=AccessPolicy(combinations=[AccessCombination(id=member, identity_id=member,
                priority=index) for index, member in enumerate(members)],
                max_concurrency=1, min_interval_ms=3000, crawler_queue_timeout_ms=30000),
            auth=AuthPolicy(maintenance_url="https://www.sdxcentral.com/news/",
                verification_url=BODY_CANARY, verification_kind="public_access"),
            body=BodyStrategyPointer(ref="builtin:generic@1", access_order=["browser"])),
            expected_revision=None, actor="ops:sdxcentral-browser")
    # One-time upgrade of this rollout's original empty verification policy.
    # Never overwrite an operator's existing article choice.
    current = repository.get_strategy("sdxcentral")
    if not current.auth.verification_url:
        repository.apply_strategy(current.model_copy(update={"auth":current.auth.model_copy(
            update={"verification_url":BODY_CANARY, "verification_kind":"public_access"})}),
            expected_revision=current.revision, actor="ops:sdxcentral-public-verification")
    if governance.get_scope(SCOPE) is None:
        governance.apply_scope(SourceAccessScope(scope_id=SCOPE, source_id="sdxcentral_news",
            owner_site_id="sdxcentral", primary_identity_id=members[0],
            fallback_identity_id=members[1], verification_plan={"default_publisher":True,
                "crawler":{"url":"https://www.sdxcentral.com/news/"},
                "body_url":BODY_CANARY, "fallback_state":"UNVERIFIED"}), expected_revision=None)
    return {"scope_id":SCOPE, "identities":members}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sqlite", required=True)
    args = parser.parse_args()
    repository = SiteStrategyRepository(args.sqlite)
    try:
        print(json.dumps(configure(repository)))
    finally:
        repository.close()
