import json
import subprocess

import pytest

from doxagent.site_strategy.reuters_storage import STORAGE_GUARD, prepare_reuters_storage


def run_guard(data, origin='https://www.reuters.com'):
    # Run the shipped script, including browser-origin/quota semantics, without
    # a website request or mirroring its trimming algorithm in Python.
    source = "const data=" + json.dumps(data) + ";const location={origin:"+json.dumps(origin)+"};"
    source += "const localStorage={getItem:k=>data[k]??null,setItem:(k,v)=>{if(v.length>5242880)throw Error('quota');data[k]=v}};"
    source += "const result=" + STORAGE_GUARD + ";console.log(JSON.stringify({result,data}));"
    return json.loads(subprocess.check_output(['node'],input=source,text=True))


def test_oversized_visit_history_releases_quota_and_preserves_auth_and_tracking():
    visits=['https://www.reuters.com/site-search/?query=Micron&offset=0']*2500
    original={'ABTastyData':json.dumps({'VisitedPages':visits,'ActionTracking':{'keep':'assignment'}}),
              'ArcP':'opaque-auth','ArcId.USER_PROFILE':'opaque-profile','other':'unchanged'}
    result=run_guard(original)
    assert result['result']['state']=='TRIMMED'
    data=json.loads(result['data']['ABTastyData'])
    assert len(data['VisitedPages'])==256 and data['ActionTracking']=={'keep':'assignment'}
    assert all(result['data'][k]==v for k,v in original.items() if k!='ABTastyData')


@pytest.mark.parametrize('origin',['https://www.barrons.com','http://www.reuters.com','https://evil-reuters.com'])
def test_guard_does_not_change_other_origins(origin):
    data={'ABTastyData':'x'*140000,'ArcP':'opaque'}
    out=run_guard(data,origin)
    assert out['data']==data and out['result']['state']=='OTHER_ORIGIN'


def test_unknown_or_broken_cache_is_not_blindly_deleted():
    for raw in ['x'*140000,json.dumps({'unknown':'x'*140000})]:
        out=run_guard({'ABTastyData':raw})
        assert out['data']['ABTastyData']==raw
        assert out['result']['state'] in {'UNAVAILABLE','UNKNOWN_SCHEMA'}


class Context:
    def __init__(self):self.scripts=[]
    async def add_init_script(self,script):self.scripts.append(script)


class Page:
    def __init__(self,context):self.context=context;self.calls=0
    async def evaluate(self,script):self.calls+=1;return {'state':'SMALL'}


@pytest.mark.asyncio
async def test_context_install_is_once_and_warm_checks_are_bounded(monkeypatch):
    import doxagent.site_strategy.reuters_storage as module
    now=[100];monkeypatch.setattr(module.time,'monotonic',lambda:now[0])
    c=Context();a,b=Page(c),Page(c)
    await prepare_reuters_storage(a);await prepare_reuters_storage(b)
    assert len(c.scripts)==1 and a.calls+b.calls==1
    now[0]+=61;await prepare_reuters_storage(b)
    assert len(c.scripts)==1 and b.calls==1
    await prepare_reuters_storage(b,force=True)
    assert len(c.scripts)==1 and b.calls==2


@pytest.mark.asyncio
async def test_disconnected_browser_hygiene_does_not_fail_business():
    class Broken(Page):
        async def evaluate(self,script):raise RuntimeError('driver disconnected')
    await prepare_reuters_storage(Broken(Context()))
