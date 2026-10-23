import asyncio

from doxagent.site_strategy.budget import JointBudget
from doxagent.site_strategy.schema import SitePurpose


async def test_body_load_reserves_crawler_capacity_and_one_body_per_identity():
    budget = JointBudget(max_concurrency=4)
    release = asyncio.Event()
    entered = []

    async def work(name, purpose, identity):
        async with budget.permit(
            purpose,
            site_key=name,
            identity_id=identity,
            site_max_concurrency=2,
            site_min_interval_ms=0,
            identity_max_concurrency=2,
            identity_min_interval_ms=0,
            timeout_seconds=2,
        ):
            entered.append(name)
            await release.wait()

    bodies = [asyncio.create_task(work(f"body{i}", SitePurpose.BODY, str(i))) for i in range(4)]
    await asyncio.sleep(0.03)
    assert len(entered) == 3
    crawler = asyncio.create_task(work("crawler", SitePurpose.CRAWLER, "0"))
    await asyncio.sleep(0.03)
    assert "crawler" in entered and "body3" not in entered
    release.set()
    await asyncio.gather(*bodies, crawler)
    assert budget._active == 0
