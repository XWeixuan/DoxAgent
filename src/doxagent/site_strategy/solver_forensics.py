"""Small, redacted evidence from existing requests; never sends website requests."""
import hashlib
import hmac
import json
import re
from http.cookies import SimpleCookie
from urllib.parse import urlsplit

from .challenge_evidence import safe_url, safe_text

NAMES = {"datadome", "ArcP", "ArcId.USER_PROFILE"}


def relevant_cookie(name):
    return name in NAMES or bool(re.fullmatch(r'_px[a-zA-Z0-9_]{0,32}|pxcts', name))


def fingerprint(value, incident):
    return hmac.new(incident.encode(), value.encode(), hashlib.sha256).hexdigest()[:24]


def cookie_summary(cookies, incident):
    return [{"name": c["name"], "domain": c.get("domain"), "path": c.get("path"),
             "secure": c.get("secure"), "http_only": c.get("httpOnly"),
             "partitioned": bool(c.get("partitionKey")),
             "fingerprint": fingerprint(c.get("value", ""), incident)}
            for c in cookies if relevant_cookie(c.get('name', ''))][:12]


def header_cookies(headers, incident):
    raw = next((str(v) for k, v in headers.items() if k.lower() == "cookie"), "")
    # Do not persist the header or unrelated tracking/account values.
    return [{"name": name.strip(), "fingerprint": fingerprint(value.strip(), incident)}
            for item in raw.split(";") if "=" in item
            for name, value in [item.split("=", 1)] if relevant_cookie(name.strip())][:12]


def request_headers(headers):
    allowed = {"user-agent", "accept-language", "accept", "sec-fetch-site",
               "sec-fetch-mode", "sec-fetch-dest", "sec-ch-ua", "sec-ch-ua-platform"}
    return {k.lower(): safe_text(v, 160) for k, v in headers.items() if k.lower() in allowed}


def set_cookie_summary(headers, incident):
    raw = next((str(v) for k, v in headers.items() if k.lower() == "set-cookie"), "")
    result = []
    for line in raw[:16384].splitlines()[:20]:
        parsed = SimpleCookie()
        try:
            parsed.load(line)
        except Exception:
            continue
        for name, cookie in parsed.items():
            if relevant_cookie(name):
                result.append({"name": name, "domain": cookie["domain"], "path": cookie["path"],
                    "fingerprint": fingerprint(cookie.value, incident), "http_only": bool(cookie["httponly"])})
    return result[:8]


def response_summary(url, status, headers, body=None, *, wire_body=False):
    headers = {k.lower(): str(v) for k, v in headers.items()}
    allowed = {k: safe_text(headers[k], 100) for k in (
        "content-type", "server", "via", "x-cache", "cf-mitigated", "cache-control"
    ) if k in headers}
    for k in ("x-datadome", "x-dd-b"):
        if k in headers:
            value = headers[k]
            allowed[k] = value if re.fullmatch(r"[a-zA-Z_-]{1,32}", value) else "PRESENT"
    if "www-authenticate" in headers:
        allowed["www-authenticate"] = headers["www-authenticate"].split(" ", 1)[0][:24]
    if "location" in headers:
        allowed["location"] = safe_url(headers["location"])
    if 'x-squid-error' in headers:
        allowed['x-squid-error'] = headers['x-squid-error'].split(' ', 1)[0][:80]
    text = (body or "")[:16384]
    signals = {"datadome_url": bool(re.search(r"(?:captcha-delivery\.com|dd\.reuters\.com)", text)),
               "datadome_bootstrap": bool(re.search(r"(?:var|let|const)\s+dd\s*=", text)),
               "auth_message": bool(re.search(r"login_required|invalid_token|unauthorized|sign in to", text, re.I))}
    signals['perimeterx_page'] = bool(re.search(r'px-captcha|_pxAppId', text))
    layer = ('PROXY_NETWORK' if status == 407 or 'x-squid-error' in headers or
                ('squid' in headers.get('server', '').lower() and status >= 400) else
             "DATADOME" if "x-datadome" in headers or "x-dd-b" in headers
             or (wire_body and signals["datadome_url"]) else
             'PERIMETERX' if any(k.startswith('x-px-') for k in headers) or
                (signals['perimeterx_page'] and re.search(r'press\s*(?:&|and)\s*hold|are you a robot', text, re.I)) else
             "CLOUDFLARE" if headers.get("cf-mitigated") == "challenge" else
             "AUTHENTICATION" if "www-authenticate" in headers else "UNCONFIRMED")
    out = {"url": safe_url(url), "status": status, "headers": allowed,
           "layer": layer, "body_source": ("NONE" if body is None else
               "WIRE" if wire_body else "RENDERED_DOCUMENT"),
           "body_signals": signals}
    if body is not None:
        out["body_bytes"] = len(body.encode())
        out["body_sha256"] = hashlib.sha256(body.encode()).hexdigest()
        try:
            obj = json.loads(text)
            if isinstance(obj, dict):
                out["json_keys"] = [k for k in obj if re.fullmatch(r"[a-zA-Z_]{1,30}", k)][:10]
                if isinstance(obj.get("url"), str):
                    out["challenge_url"] = safe_url(obj["url"])
        except (ValueError, TypeError):
            pass
    return out


class WireEvidence:
    """At most six navigation/search responses and eight unmatched cookie facts."""
    def __init__(self, incident, *, website=None):
        self.incident = incident
        self.website = website
        self.requests = {}
        self.extras = {}

    @staticmethod
    def interested(url):
        p = urlsplit(url)
        return p.hostname == "www.reuters.com" and (p.path == "/site-search/"
            or p.path.startswith("/pf/api/v3/content/fetch/articles-by-search"))

    def event(self, message):
        method, p = message.get("method"), message.get("params", {})
        key = p.get("requestId")
        if method == "Network.requestWillBeSent":
            request = p.get("request", {})
            is_bloomberg = (self.website and urlsplit(self.website).hostname in {'www.bloomberg.com', 'bloomberg.com'}
                            and urlsplit(request.get('url', '')).hostname == urlsplit(self.website).hostname
                            and (p.get('type') == 'Document' or urlsplit(request.get('url', '')).path.startswith('/lineup-next/api/')))
            if key not in self.requests and (not (is_bloomberg or self.interested(request.get("url", ""))) or len(self.requests) >= 6):
                return
            row = self.requests.setdefault(key, {"url": safe_url(request["url"]), "redirects": []})
            row.update(request_at=p.get("wallTime"), resource_type=p.get("type"),
                method=request.get("method"), request_headers=request_headers(request.get("headers", {})),
                request_cookies=header_cookies(request.get("headers", {}), self.incident))
            if key in self.extras:
                extra = self.extras.pop(key)
                extra["request_headers"] = {**row["request_headers"], **extra["request_headers"]}
                row.update(extra)
            if p.get("redirectResponse") and len(row["redirects"]) < 4:
                r = p["redirectResponse"]
                row["redirects"].append(response_summary(r.get("url", ""), r.get("status"), r.get("headers", {})))
        elif method == "Network.requestWillBeSentExtraInfo":
            challenge_cookies = [dict(cookie_summary([c['cookie']], self.incident)[0],
                blocked_reasons=c.get('blockedReasons', [])) for c in p.get('associatedCookies', [])
                if c.get('cookie', {}).get('name') == 'datadome' or
                   re.fullmatch(r'_px[a-zA-Z0-9_]{0,32}|pxcts', c.get('cookie', {}).get('name', ''))][:12]
            extra = {"request_cookies": header_cookies(p.get("headers", {}), self.incident),
                     "request_headers": request_headers(p.get("headers", {})),
                     'associated_challenge_cookies': challenge_cookies,
                     "associated_datadome": [dict(cookie_summary([c["cookie"]], self.incident)[0],
                        blocked_reasons=c.get("blockedReasons", [])) for c in p.get("associatedCookies", [])
                        if c.get("cookie", {}).get("name") == "datadome"][:4]}
            if key in self.requests:
                extra["request_headers"] = {**self.requests[key].get("request_headers", {}), **extra["request_headers"]}
                self.requests[key].update(extra)
            elif challenge_cookies and len(self.extras) < 8:
                self.extras[key] = extra
        elif key in self.requests and method == "Network.responseReceived":
            r = p.get("response", {})
            row = self.requests[key]
            row["response"] = response_summary(r.get("url", ""), r.get("status"), r.get("headers", {}))
            row["transport"] = {"protocol": r.get("protocol"), "remote_ip": r.get("remoteIPAddress"),
                "tls_issuer": r.get("securityDetails", {}).get("issuer"),
                "from_cache": bool(r.get("fromDiskCache")), "from_service_worker": bool(r.get("fromServiceWorker"))}
        elif key in self.requests and method == "Network.responseReceivedExtraInfo":
            self.requests[key]["response_extra"] = {
                "status": p.get("statusCode"), "set_cookies": set_cookie_summary(p.get("headers", {}), self.incident),
                "blocked_set_cookie_count": len(p.get("blockedCookies", []))}
        elif key in self.requests and method == "Network.loadingFinished":
            self.requests[key]["finished"] = True
        elif key in self.requests and method == 'Network.loadingFailed':
            error = p.get('errorText', '')
            self.requests[key]['network_error'] = error if re.fullmatch(r'net::ERR_[A-Z_]{1,80}', error) else 'REQUEST_FAILED'

    async def bodies(self, call):
        for key, row in list(self.requests.items()):
            if not row.get("finished") or not row.get("response"):
                continue
            if row["response"]["status"] < 400:
                continue  # Healthy article/search payloads are not collected.
            try:
                value = await call("Network.getResponseBody", {"requestId": key})
                body = value.get("body", "")
                if value.get("base64Encoded"):
                    import base64
                    body = base64.b64decode(body).decode("utf-8", errors="replace")
                if len(body) <= 65536:
                    row["wire_body"] = response_summary(row["url"], row["response"]["status"],
                        row["response"]["headers"], body, wire_body=True)
            except (RuntimeError, TimeoutError, ValueError):
                row["body_state"] = "NOT_AVAILABLE"

    def result(self):
        return list(self.requests.values())
