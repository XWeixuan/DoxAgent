"""Small, idempotent article-boundary fixes, shared by extraction and reuse."""

import re
from dataclasses import replace
from urllib.parse import urlsplit

QZ = re.compile(
    r"(?m)^[ \t]*See today[’']s quantum computing news on Quantum Zeitgeist "
    r"for the latest breakthroughs in qubits, hardware, algorithms, and industry deals\.[ \t]*$"
)
DCD = re.compile(r"(?m)^\s*More in [^\n]+\s*$")
HOSTS = {"quantumzeitgeist.com", "datacenterdynamics.com"}


def clean_text(text, url):
    if not text:
        return text
    host = (urlsplit(url).hostname or "").removeprefix("www.")
    if host == "quantumzeitgeist.com":
        return QZ.sub("", text).strip()
    if host == "datacenterdynamics.com":
        boundary = DCD.search(text)
        return text[: boundary.start()].rstrip() if boundary else text
    return text


def clean_document(root, url):
    if (urlsplit(url).hostname or "").removeprefix("www.") != "datacenterdynamics.com":
        return False
    nodes = root.xpath(
        "//*[contains(concat(' ',normalize-space(@class),' '),"
        "' w-block-auto_featured_content ')]"
        "[.//h2[starts-with(normalize-space(.),'More in ')]]"
    )
    for node in nodes:
        node.drop_tree()
    return bool(nodes)


def clean_candidates(info, url):
    if (urlsplit(url).hostname or "").removeprefix("www.") in HOSTS:
        info.candidates = [replace(c, text=clean_text(c.text, url)) for c in info.candidates]
    return info


def clean_message(message):
    body, summary = clean_text(message.body, message.url), clean_text(message.summary, message.url)
    if body == message.body and summary == message.summary:
        return message
    # A changed cached body must not use the 304 unchanged-digest shortcut.
    md = dict(message.metadata)
    media = dict(md.get("media_enrichment", {}))
    media["http_not_modified"] = False
    md["media_enrichment"] = media
    md["publisher_cleanup_version"] = 1
    if "v2_body_completion" in md:
        md["v2_body_completion"] = {**md["v2_body_completion"], "body_fingerprint": None}
    return message.model_copy(update={"body": body, "summary": summary, "metadata": md})
