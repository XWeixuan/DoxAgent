from types import SimpleNamespace

import pytest

from doxagent.message_bus_v2.reuters_sources import capture_reuters_search


@pytest.mark.asyncio
async def test_normal_article_and_true_empty_search_recipe():
    async def goto(*args, **kwargs):
        return SimpleNamespace(status=200)

    async def wait(*args, **kwargs):
        return None

    rows = [
        {
            "title": "Micron publishes earnings and memory sales update",
            "url": "/technology/micron-earnings-2026-10-04/",
            "date": "2026-10-04",
        }
    ]

    async def evaluate(*args):
        return rows

    page = SimpleNamespace(goto=goto, wait_for_function=wait, evaluate=evaluate)
    assert await capture_reuters_search(page, "Micron", 0) == rows
    rows.clear()
    assert await capture_reuters_search(page, "No matching article", 0) == []


@pytest.mark.asyncio
async def test_denial_retains_untrusted_page_but_no_cookie():
    async def headers():
        return {"set-cookie": "secret", "content-type": "text/html"}

    async def goto(*args, **kwargs):
        return SimpleNamespace(status=403, all_headers=headers)

    async def content():
        return "<html>Access is temporarily restricted</html>"

    page = SimpleNamespace(goto=goto, content=content, url="https://www.reuters.com/site-search/")
    with pytest.raises(RuntimeError) as error:
        await capture_reuters_search(page, "Micron", 0)
    assert "restricted" in error.value.response_body
    assert "set-cookie" not in error.value.response_headers
