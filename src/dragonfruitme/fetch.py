"""Fetch ladder: inline HTML -> plain HTTP (stdlib) -> optional rendering.

The cheapest stage that yields usable content wins. Rendering is optional
(``pip install 'dragonfruitme[browser]'``) and only used when the extraction
graph asks for it.
"""
from __future__ import annotations

import gzip
import re
import threading
import time
import urllib.error
import urllib.request
import urllib.robotparser
import zlib
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.parse import urlsplit, urlunsplit

from .errors import DragonFruitMeError
from .policy import FetchPolicy, detect_challenge

_META_CHARSET = re.compile(rb"<meta[^>]+charset=[\"']?([A-Za-z0-9_\-]+)", re.I)


@dataclass
class FetchResult:
    url: str
    final_url: str
    status: int
    html: str
    stage: str  # inline | http | render
    elapsed_ms: int = 0
    bytes: int = 0
    truncated: bool = False
    content_type: str = ""
    notes: list[str] = field(default_factory=list)

    def info(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "url": self.url,
            "final_url": self.final_url,
            "status": self.status,
            "bytes": self.bytes,
            "elapsed_ms": self.elapsed_ms,
            "truncated": self.truncated,
            "content_type": self.content_type,
            "notes": list(self.notes),
        }


def _decode(body: bytes, content_type: str) -> str:
    charset = ""
    match = re.search(r"charset=([A-Za-z0-9_\-]+)", content_type or "", re.I)
    if match:
        charset = match.group(1)
    else:
        meta = _META_CHARSET.search(body[:4096])
        if meta:
            charset = meta.group(1).decode("ascii", "ignore")
    for candidate in (charset, "utf-8", "cp1252"):
        if not candidate:
            continue
        try:
            return body.decode(candidate)
        except (LookupError, UnicodeDecodeError):
            continue
    return body.decode("utf-8", "replace")


def _decompress(body: bytes, encoding: str) -> bytes:
    encoding = (encoding or "").lower()
    if encoding == "gzip":
        return gzip.decompress(body)
    if encoding == "deflate":
        try:
            return zlib.decompress(body)
        except zlib.error:
            return zlib.decompress(body, -zlib.MAX_WBITS)
    return body


def normalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path or "/", parts.query, ""))


def render_available() -> bool:
    try:
        import playwright.sync_api  # noqa: F401
    except ImportError:
        return False
    return True


def _render_with_playwright(url: str, policy: FetchPolicy) -> tuple[str, str, int]:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page(user_agent=policy.user_agent)
            response = page.goto(url, wait_until="networkidle", timeout=int(policy.timeout_seconds * 1000))
            html = page.content()
            status = response.status if response is not None else 0
            return html, page.url, status
        finally:
            browser.close()


class Fetcher:
    """Polite fetcher with robots.txt, per-host rate limiting and a page cache."""

    def __init__(
        self,
        policy: FetchPolicy | None = None,
        *,
        opener: Callable[[urllib.request.Request, float], Any] | None = None,
        renderer: Callable[[str, FetchPolicy], tuple[str, str, int]] | None = None,
    ):
        self.policy = policy or FetchPolicy()
        self._opener = opener or (lambda req, timeout: urllib.request.urlopen(req, timeout=timeout))
        self._renderer = renderer
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self._last_hit: dict[str, float] = {}
        self._cache: dict[tuple[str, str], tuple[float, FetchResult]] = {}
        self._lock = threading.Lock()

    # -- policy ------------------------------------------------------------
    def _check_url(self, url: str) -> str:
        parts = urlsplit(url)
        if parts.scheme.lower() not in self.policy.allowed_schemes:
            raise DragonFruitMeError("URL_SCHEME_DENIED", f"Scheme not allowed: {parts.scheme or '(none)'}", recoverable=False)
        host = (parts.hostname or "").lower()
        if not host:
            raise DragonFruitMeError("URL_INVALID", f"URL has no host: {url}", recoverable=False)
        if any(host == d or host.endswith("." + d) for d in self.policy.deny_hosts):
            raise DragonFruitMeError("HOST_DENIED", f"Host is denied by policy: {host}", recoverable=False)
        return host

    def _robots_allowed(self, url: str) -> bool:
        if not self.policy.respect_robots:
            return True
        parts = urlsplit(url)
        root = f"{parts.scheme}://{parts.netloc}"
        if root not in self._robots:
            parser: urllib.robotparser.RobotFileParser | None = urllib.robotparser.RobotFileParser()
            request = urllib.request.Request(root + "/robots.txt", headers={"User-Agent": self.policy.user_agent})
            try:
                with self._opener(request, self.policy.timeout_seconds) as response:
                    raw = response.read(500_000)
                    body = _decompress(raw, response.headers.get("Content-Encoding", ""))
                    parser.parse(body.decode("utf-8", "replace").splitlines())
            except urllib.error.HTTPError as exc:
                if exc.code in {401, 403}:
                    parser.disallow_all = True
                else:
                    parser.allow_all = True  # 404 and other 4xx/5xx: no usable rules
            except (urllib.error.URLError, OSError, ValueError):
                parser = None  # unreachable: decide below
            self._robots[root] = parser
        parser = self._robots[root]
        if parser is None:
            return True
        return parser.can_fetch(self.policy.user_agent, url)

    def _throttle(self, host: str) -> None:
        with self._lock:
            last = self._last_hit.get(host)
            now = time.monotonic()
            if last is not None:
                wait = self.policy.min_interval_seconds - (now - last)
                if wait > 0:
                    time.sleep(wait)
            self._last_hit[host] = time.monotonic()

    # -- stages ------------------------------------------------------------
    def inline(self, html: str, base_url: str = "") -> FetchResult:
        return FetchResult(url=base_url, final_url=base_url, status=200, html=html, stage="inline", bytes=len(html.encode("utf-8", "ignore")))

    def http(self, url: str) -> FetchResult:
        url = normalize_url(url)
        host = self._check_url(url)
        cached = self._cache.get((url, "http"))
        if cached and time.monotonic() - cached[0] < self.policy.cache_ttl_seconds:
            result = cached[1]
            return FetchResult(**{**result.__dict__, "notes": [*result.notes, "cache_hit"]})
        if not self._robots_allowed(url):
            raise DragonFruitMeError(
                "ROBOTS_DISALLOWED",
                f"robots.txt disallows fetching {url} for this user agent.",
                recoverable=False,
                details={"url": url, "next": "use an official API/feed or ask the site owner"},
            )
        self._throttle(host)
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": self.policy.user_agent,
                "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5",
                "Accept-Language": "de-DE,de;q=0.9,en;q=0.7",
                "Accept-Encoding": "gzip, deflate",
            },
        )
        started = time.monotonic()
        status = 0
        headers: Any = {}
        final_url = url
        try:
            response = self._opener(request, self.policy.timeout_seconds)
        except urllib.error.HTTPError as exc:
            response = exc
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise DragonFruitMeError("FETCH_FAILED", f"Could not fetch {url}: {exc}", details={"url": url}) from exc
        try:
            status = getattr(response, "status", None) or getattr(response, "code", 0) or 0
            headers = response.headers or {}
            final_url = response.geturl() if hasattr(response, "geturl") else url
            try:
                raw = response.read(self.policy.max_bytes + 1) or b""
            except (OSError, ValueError, AttributeError):
                raw = b""
        finally:
            close = getattr(response, "close", None)
            if close is not None:
                close()
        truncated = len(raw) > self.policy.max_bytes
        raw = raw[: self.policy.max_bytes]
        try:
            body = _decompress(raw, headers.get("Content-Encoding", ""))
        except (OSError, zlib.error, EOFError):
            body = raw
        content_type = headers.get("Content-Type", "") or ""
        html = _decode(body, content_type)
        result = FetchResult(
            url=url,
            final_url=final_url,
            status=int(status),
            html=html,
            stage="http",
            elapsed_ms=int((time.monotonic() - started) * 1000),
            bytes=len(body),
            truncated=truncated,
            content_type=content_type,
        )
        self._raise_if_blocked(result)
        if result.status >= 400:
            raise DragonFruitMeError(
                "HTTP_ERROR",
                f"HTTP {result.status} for {url}",
                recoverable=result.status >= 500,
                details={"url": url, "status": result.status},
            )
        self._cache[(url, "http")] = (time.monotonic(), result)
        return result

    def render(self, url: str) -> FetchResult:
        url = normalize_url(url)
        host = self._check_url(url)
        renderer = self._renderer
        if renderer is None:
            if not render_available():
                raise DragonFruitMeError(
                    "RENDER_NOT_AVAILABLE",
                    "Rendering needs the optional browser extra: pip install 'dragonfruitme[browser]' && playwright install chromium",
                )
            renderer = _render_with_playwright
        if not self._robots_allowed(url):
            raise DragonFruitMeError("ROBOTS_DISALLOWED", f"robots.txt disallows fetching {url}.", recoverable=False)
        cached = self._cache.get((url, "render"))
        if cached and time.monotonic() - cached[0] < self.policy.cache_ttl_seconds:
            return cached[1]
        self._throttle(host)
        started = time.monotonic()
        html, final_url, status = renderer(url, self.policy)
        result = FetchResult(
            url=url,
            final_url=final_url,
            status=int(status or 200),
            html=html,
            stage="render",
            elapsed_ms=int((time.monotonic() - started) * 1000),
            bytes=len(html.encode("utf-8", "ignore")),
        )
        self._raise_if_blocked(result)
        self._cache[(url, "render")] = (time.monotonic(), result)
        return result

    def can_render(self) -> bool:
        return self._renderer is not None or render_available()

    @staticmethod
    def _raise_if_blocked(result: FetchResult) -> None:
        reason = detect_challenge(result.status, result.html)
        if reason:
            raise DragonFruitMeError(
                "BLOCKED",
                f"{result.final_url} answered with a bot challenge or block ({reason}). DragonFruitMe does not bypass this.",
                recoverable=False,
                details={
                    "url": result.final_url,
                    "status": result.status,
                    "reason": reason,
                    "next": "human-in-the-loop, an official API/feed, or skip this source",
                },
            )
