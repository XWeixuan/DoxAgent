"""Observe existing native responses without website/provider traffic."""
from types import SimpleNamespace
from unittest.mock import AsyncMock
import json

import pytest

from doxagent.message_bus_v2 import reuters_sources as recipe


@pytest.mark.parametrize("dom_timeout", [False, True])
async def test_api_denial_survives_dom_timeout_without_replacing_original_page(monkeypatch, dom_timeout):
    api = ('https://www.reuters.com/pf/api/v3/content/fetch/articles-by-search-v2'
           '?query=%7B%22keyword%22%3A%22Micron%22%7D&token=private')
    response = SimpleNamespace(
        url=api, status=401,
        all_headers=AsyncMock(return_value={
            'content-type': 'text/html', 'content-length': '120',
            'x-datadome': 'protected', 'x-cache': 'LambdaGeneratedResponse from cloudfront',
            'set-cookie': 'datadome=private-cookie', 'authorization': 'private-auth',
        }),
        text=AsyncMock(return_value='<script>var dd={};</script>https://geo.captcha-delivery.com/captcha/?secret=private'),
    )
    callbacks={}
    page=SimpleNamespace(
        url='https://www.reuters.com/site-search/?query=Micron',
        on=lambda event, callback: callbacks.update({event: callback}),
        remove_listener=lambda event, callback: callbacks.pop(event),
        content=AsyncMock(return_value='<main>Search shell</main>'),
    )
    timeout=TimeoutError('native result selector timed out')
    async def capture(*args, **kwargs):
        callbacks['response'](response)
        if dom_timeout:
            raise timeout
        return []
    monkeypatch.setattr(recipe, '_capture_reuters_search_dom', capture)
    with pytest.raises((TimeoutError, RuntimeError)) as raised:
        await recipe.capture_reuters_search(page, 'Micron', 0)
    error=raised.value
    if dom_timeout:
        assert error is timeout
    else:
        assert error.response_body=='<main>Search shell</main>'
        assert error.response_url==page.url
    evidence=error.native_search_failure
    assert evidence['status']==401 and evidence['layer']=='DATADOME'
    assert evidence['body_signals']['datadome_bootstrap']
    assert evidence['url']==api.split('?')[0]
    assert 'private' not in json.dumps(evidence)
    assert 'Micron' not in json.dumps(evidence)
    assert not callbacks


@pytest.mark.parametrize('length', [None, '999999', 'invalid'])
async def test_unknown_or_large_response_does_not_allocate_body(length):
    headers={'x-datadome':'protected'}
    if length is not None:
        headers['content-length']=length
    response=SimpleNamespace(url='https://www.reuters.com/api',status=401,
                             all_headers=AsyncMock(return_value=headers),text=AsyncMock())
    error=RuntimeError()
    await recipe._attach_native_search_failure(error,response)
    assert error.native_search_failure['layer']=='DATADOME'
    assert error.native_search_failure['body_state']=='SKIPPED_SIZE_UNKNOWN_OR_LARGE'
    response.text.assert_not_awaited()


async def test_unavailable_diagnostic_does_not_mask_known_denial():
    response=SimpleNamespace(url='https://www.reuters.com/api',status=401,
                             all_headers=AsyncMock(side_effect=RuntimeError('detached')))
    error=TimeoutError()
    await recipe._attach_native_search_failure(error,response)
    assert error.native_search_failure['status']==401
    assert error.native_search_failure['body_state']=='UNAVAILABLE'


async def test_successful_response_does_not_read_diagnostics(monkeypatch):
    response=SimpleNamespace(url='https://www.reuters.com/pf/api/v3/content/fetch/articles-by-search-v2?query=%7B%22keyword%22%3A%22Micron%22%7D',
        status=200,request=object(),json=AsyncMock(return_value={'result':{'articles':[]}}),
        all_headers=AsyncMock(),text=AsyncMock())
    callbacks={}
    page=SimpleNamespace(on=lambda event, callback: callbacks.update({event:callback}),
                         remove_listener=lambda event, callback:callbacks.pop(event))
    async def capture(*args,**kwargs):
        callbacks['response'](response)
        return []
    monkeypatch.setattr(recipe,'_capture_reuters_search_dom',capture)
    assert await recipe.capture_reuters_search(page,'Micron',0)==[]
    response.all_headers.assert_not_awaited()
    response.text.assert_not_awaited()
