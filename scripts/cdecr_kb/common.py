"""Shared helpers for building the CDECR v2 JSON catalogs.

The builders deliberately use only the Python standard library so they can be
rerun without changing the application's dependency set.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import string
import time
import unicodedata
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Iterable


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_WORK_DIR = Path(r"D:\cdecr-kb-work")
DEFAULT_OUTPUT_DIR = REPO_ROOT / "src" / "cdecr" / "catalogs" / "v2"
DEFAULT_USER_AGENT = "CDECR-KB-Builder/2.0 contact@example.com"

CORPORATE_SUFFIX_RE = re.compile(
    r"(?:[\s,.-]+(?:incorporated|inc|corporation|corp|company|co|limited|ltd|"
    r"plc|llc|l\.l\.c|lp|l\.p|holdings?|group|s\.a\.|sa|n\.v\.|nv|ag|se))+$",
    re.IGNORECASE,
)
SPACE_RE = re.compile(r"\s+")
NON_ID_RE = re.compile(r"[^A-Z0-9]+")
DISPLAY_ACRONYMS = {
    "ADR", "ADS", "AI", "AG", "ASA", "BANC", "ETF", "FSB", "IBM", "LP", "LLC", "NA", "NV",
    "PLC", "REIT", "SA", "SEC", "USA", "US", "UK", "II", "III", "IV",
}


def configured_sec_user_agent() -> str:
    """Read SEC_USER_AGENT without printing or persisting its value."""

    value = os.environ.get("SEC_USER_AGENT", "").strip()
    if value:
        return value
    env_path = REPO_ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.startswith("SEC_USER_AGENT="):
                value = line.split("=", 1)[1].strip().strip('"').strip("'")
                if value:
                    return value
    return DEFAULT_USER_AGENT


def runtime_normalize(value: str) -> str:
    """Mirror the catalog runtime's case/punctuation/underscore normalization."""

    value = unicodedata.normalize("NFKC", value or "").casefold().replace("_", " ")
    value = "".join(" " if ch in string.punctuation else ch for ch in value)
    return SPACE_RE.sub(" ", value).strip()


def id_slug(value: str, fallback: str = "UNKNOWN") -> str:
    ascii_value = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode()
    slug = NON_ID_RE.sub("_", ascii_value.upper()).strip("_")
    return slug or fallback


def stable_suffix(value: str, length: int = 8) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:length].upper()


def strip_corporate_suffix(name: str) -> str:
    value = SPACE_RE.sub(" ", (name or "").strip()).strip(" ,.-")
    previous = None
    while value and value != previous:
        previous = value
        value = CORPORATE_SUFFIX_RE.sub("", value).strip(" ,.-")
    return value or SPACE_RE.sub(" ", (name or "").strip())


def display_name(name: str) -> str:
    """Make all-uppercase source labels readable without over-normalizing them."""

    value = SPACE_RE.sub(" ", str(name or "").strip())
    if not value or value.upper() != value or not any(ch.isalpha() for ch in value):
        return value
    result: list[str] = []
    for token in value.split():
        bare = re.sub(r"[^A-Z0-9]", "", token)
        if bare in DISPLAY_ACRONYMS or (bare.isalpha() and len(bare) <= 2):
            result.append(token)
        else:
            result.append(token.title())
    return " ".join(result)


def clean_aliases(name: str, aliases: Iterable[str], limit: int = 30) -> list[str]:
    """Deduplicate aliases using runtime equivalence while preserving useful form."""

    seen = {runtime_normalize(name)}
    result: list[str] = []
    for raw in aliases:
        alias = SPACE_RE.sub(" ", str(raw or "").strip())
        normalized = runtime_normalize(alias)
        if not alias or not normalized or normalized in seen:
            continue
        if len(alias) > 180:
            continue
        seen.add(normalized)
        result.append(alias)
        if len(result) >= limit:
            break
    return result


def unique_id(prefix: str, token: str, uniqueness_key: str, used: dict[str, str]) -> str:
    candidate = f"{prefix}_{id_slug(token)}"
    if candidate not in used or used[candidate] == uniqueness_key:
        used[candidate] = uniqueness_key
        return candidate
    candidate = f"{candidate}_{stable_suffix(uniqueness_key)}"
    used[candidate] = uniqueness_key
    return candidate


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, separators=(",", ":"))
        handle.write("\n")
    temp.replace(path)


def download(
    url: str,
    destination: Path,
    *,
    sec: bool = False,
    timeout: int = 180,
    force: bool = False,
) -> Path:
    """Download atomically and reuse non-empty cached files."""

    if destination.exists() and destination.stat().st_size > 0 and not force:
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": configured_sec_user_agent() if sec else DEFAULT_USER_AGENT,
            "Accept-Encoding": "identity",
        },
    )
    temp = destination.with_suffix(destination.suffix + ".part")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response, temp.open("wb") as out:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)
        temp.replace(destination)
    except Exception:
        temp.unlink(missing_ok=True)
        raise
    return destination


def request_json(url: str, *, sec: bool = False, timeout: int = 90) -> Any:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": configured_sec_user_agent() if sec else DEFAULT_USER_AGENT,
            "Accept": "application/json",
            "Accept-Encoding": "identity",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def polite_pause(seconds: float = 0.15) -> None:
    time.sleep(seconds)
