"""Observe Bloomberg's owned Press & Hold page and apply only PX cookies."""
import re
from urllib.parse import urlsplit

from .datadome_browser import NativeChallenge


def application_id(html):
    match = re.search(r'''(?:_pxAppId|pxAppId)["']?\s*[:=]\s*["'](PX[a-zA-Z0-9]{4,64})["']''', html[:2_000_000])
    if not match:
        match = re.search(r'https://(?:captcha\.px-(?:cdn|cloud)\.net|client\.perimeterx\.net)/(PX[a-zA-Z0-9]{4,64})/', html[:2_000_000])
    return match[1] if match else None


def cookie_parameters(solution, website, expected_user_agent):
    if not expected_user_agent or solution.get('useragent') != expected_user_agent:
        raise ValueError('SOLVERIFY_USER_AGENT_MISMATCH')
    if urlsplit(website).hostname not in {'www.bloomberg.com', 'bloomberg.com'}:
        raise ValueError('SOLVERIFY_PUBLISHER_NOT_ENABLED')
    cookies = solution.get('cookies')
    if not isinstance(cookies, dict) or not 1 <= len(cookies) <= 16:
        raise ValueError('INVALID_PROVIDER_COOKIE')
    params = []
    for name, value in cookies.items():
        # Never replace login, subscription, consent, or unrelated cookies.
        if not re.fullmatch(r'_px[a-zA-Z0-9_]{0,32}|pxcts', name):
            continue
        if not isinstance(value, str) or not re.fullmatch(r'[\x21\x23-\x2B\x2D-\x3A\x3C-\x5B\x5D-\x7E]{1,16384}', value):
            raise ValueError('INVALID_PROVIDER_COOKIE')
        params.append({'name': name, 'value': value, 'domain': '.bloomberg.com',
                       'path': '/', 'secure': True, 'httpOnly': False, 'sameSite': 'Lax'})
    if not any(c['name'] in {'_px3', '_px2', '_pxhd'} for c in params):
        raise ValueError('INVALID_PROVIDER_COOKIE')
    return params


class PerimeterXChallenge(NativeChallenge):
    async def inspect(self):
        from .challenge_detection import assess
        async with self.connection() as call:
            root = (await call('DOM.getDocument', {'depth': 0}))['root']
            html = (await call('DOM.getOuterHTML', {'nodeId': root['nodeId']}))['outerHTML']
            assessment = assess(html, blocked=True)
            observed = (await call('Runtime.evaluate', {'returnByValue': True, 'expression': r'''(() => {
              const nodes=[...document.querySelectorAll('#px-captcha,.px-captcha-visible')];
              const visible=nodes.some(n=>{const r=n.getBoundingClientRect();
                for(let p=n;p;p=p.parentElement){const s=getComputedStyle(p);
                  if(p.hidden||p.getAttribute('aria-hidden')==='true'||s.display==='none'||s.visibility==='hidden'||s.opacity==='0')return false;}
                return r.width>0&&r.height>0&&r.right>0&&r.bottom>0&&r.left<innerWidth&&r.top<innerHeight;});
              return {visible, appId:typeof window._pxAppId==='string'?window._pxAppId:null};
            })()'''})).get('result', {}).get('value') or {}
            key = observed.get('appId') or application_id(html)
            key = key if isinstance(key, str) and re.fullmatch(r'PX[a-zA-Z0-9]{4,64}', key) else None
            website = self.recovery.original_url or root.get('documentURL', '')
            valid_host = urlsplit(website).hostname in {'www.bloomberg.com', 'bloomberg.com'}
            ready = (valid_host and key and observed.get('visible') is True
                     and assessment.provider == 'HUMAN_PERIMETERX' and assessment.kind != 'ACCESS_BLOCK')
            self.check()
            return {'url': root.get('documentURL'), 'website': website, 'user_agent': self.user_agent,
                    'website_key': key, 'state': 'SOLVABLE' if ready else 'UNAVAILABLE',
                    'scene_state': 'PRESS_AND_HOLD_READY' if ready else 'UNREADABLE',
                    'visible': observed.get('visible'), 'family': 'PERIMETERX'}

    async def apply(self, cookies, website):
        return await self.apply_cookies(cookies, website, return_to_article=True)
