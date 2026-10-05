"""Traversal and coverage inference from one page graph.

This module does not crawl. It tells an agent what traversal structure is
visible on the current page: pagination bounds, next/last controls, and
repeated internal link scopes that may represent a lower hierarchy level.

The central invariant is deliberately conservative: one observed page can
prove that coverage is open, but it cannot by itself prove that a source is
complete.
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .recipes import scope_of
from .values import fold, normalize_text

_LAST_TEXT = {
    "last", "lastpage", "letzte", "letzter", "letzte seite", "ende",
    ">>", ">>>", "»»", "››", "≫", "⏭",
}
_NEXT_TEXT = {
    "next", "nextpage", "weiter", "nächste", "naechste", "nächste seite",
    ">", "›", "→", "»",
}
_PREV_TEXT = {"prev", "previous", "zurück", "zurueck", "<", "‹", "←", "«"}
_FIRST_TEXT = {"first", "erste", "erste seite", "<<", "<<<", "««", "‹‹", "≪"}

_PATH_PAGE_RE = re.compile(r"(?P<prefix>/(?:page|seite|p)/)(?P<value>\d+)(?P<suffix>(?:/|$))", re.I)
_PATH_PAGE_DASH_RE = re.compile(r"(?P<prefix>/(?:page|seite|p)[-_])(?P<value>\d+)(?P<suffix>(?:/|$))", re.I)


def _key_kind(key: str) -> str | None:
    compact = re.sub(r"[^a-z0-9]+", "", key.lower())
    if compact in {"p", "pg"} or any(token in compact for token in ("page", "seite", "pageno", "pagenum")):
        return "page"
    if compact in {"offset", "start", "skip"} or any(token in compact for token in ("offset", "startindex")):
        return "offset"
    return None


def _marker(url: str) -> dict[str, Any] | None:
    """Return the first pagination-like numeric marker encoded in url."""
    parts = urlsplit(url)
    for index, (key, value) in enumerate(parse_qsl(parts.query, keep_blank_values=True)):
        kind = _key_kind(key)
        if kind and value.isdigit():
            return {"kind": kind, "key": key, "value": int(value), "query_index": index}

    for pattern in (_PATH_PAGE_RE, _PATH_PAGE_DASH_RE):
        match = pattern.search(parts.path)
        if match:
            return {
                "kind": "page",
                "key": "path",
                "value": int(match.group("value")),
                "span": match.span("value"),
            }
    return None


def _template(url: str, marker: dict[str, Any] | None) -> str | None:
    if marker is None:
        return None
    parts = urlsplit(url)
    placeholder = "{page}" if marker["kind"] == "page" else "{offset}"
    if "query_index" in marker:
        pairs = parse_qsl(parts.query, keep_blank_values=True)
        index = int(marker["query_index"])
        if not (0 <= index < len(pairs)):
            return None
        key, _ = pairs[index]
        pairs[index] = (key, placeholder)
        query = urlencode(pairs, doseq=True, safe="{}[]")
        return urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))
    span = marker.get("span")
    if not span:
        return None
    start, end = span
    path = parts.path[:start] + placeholder + parts.path[end:]
    return urlunsplit((parts.scheme, parts.netloc, path, parts.query, ""))


def _semantic(link) -> str | None:
    rel = {part.lower() for part in getattr(link, "rel", ())}
    if "last" in rel:
        return "last"
    if "next" in rel:
        return "next"
    if "prev" in rel or "previous" in rel:
        return "prev"
    if "first" in rel:
        return "first"

    text = fold(normalize_text(link.text))
    compact = re.sub(r"\s+", " ", text).strip()
    compact_no_space = compact.replace(" ", "")
    if compact in _LAST_TEXT or compact_no_space in _LAST_TEXT:
        return "last"
    if compact in _FIRST_TEXT or compact_no_space in _FIRST_TEXT:
        return "first"
    if compact in _NEXT_TEXT or compact_no_space in _NEXT_TEXT:
        return "next"
    if compact in _PREV_TEXT or compact_no_space in _PREV_TEXT:
        return "prev"
    return None


def _same_host(a: str, b: str) -> bool:
    return (urlsplit(a).hostname or "").lower() == (urlsplit(b).hostname or "").lower()


def _clean_url(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path or "/", parts.query, ""))


def _pagination(graph) -> tuple[dict[str, Any], set[str]]:
    numbered: dict[int, str] = {}
    offsets: dict[int, str] = {}
    pagination_urls: set[str] = set()
    next_url = last_url = first_url = prev_url = None
    last_source = None

    for link in graph.links:
        if not link.internal:
            continue
        href = _clean_url(link.href)
        marker = _marker(href)
        semantic = _semantic(link)

        if semantic in {"next", "last", "first", "prev"}:
            pagination_urls.add(href)
            if semantic == "next" and next_url is None:
                next_url = href
            elif semantic == "last" and last_url is None:
                last_url = href
                last_source = "rel=last" if "last" in {r.lower() for r in getattr(link, "rel", ())} else "last-control"
            elif semantic == "first" and first_url is None:
                first_url = href
            elif semantic == "prev" and prev_url is None:
                prev_url = href

        if marker is None:
            continue

        text = normalize_text(link.text).strip()
        numeric_anchor = text.isdigit()
        same_path = urlsplit(href).path == urlsplit(graph.url).path
        if not (numeric_anchor or semantic or same_path):
            continue

        pagination_urls.add(href)
        if marker["kind"] == "page":
            numbered.setdefault(int(marker["value"]), href)
        else:
            offsets.setdefault(int(marker["value"]), href)

    current_marker = _marker(graph.url)
    current_page = None
    current_offset = None
    if current_marker:
        if current_marker["kind"] == "page":
            current_page = int(current_marker["value"])
        else:
            current_offset = int(current_marker["value"])

    if current_page is None and (numbered or next_url or last_url):
        current_page = 1

    last_page = None
    last_offset = None
    if last_url:
        marker = _marker(last_url)
        if marker:
            if marker["kind"] == "page":
                last_page = int(marker["value"])
            else:
                last_offset = int(marker["value"])

    if last_page is None and len(numbered) >= 3:
        nums = sorted(numbered)
        if nums[-1] > nums[-2] + 1 and nums[0] <= 2:
            last_page = nums[-1]
            last_url = numbered[last_page]
            last_source = "terminal-page-gap"

    if next_url is None and current_page is not None and last_page is not None and current_page < last_page:
        candidate = numbered.get(current_page + 1)
        if candidate:
            next_url = candidate

    highest_visible_page = max(numbered) if numbered else None
    highest_visible_offset = max(offsets) if offsets else None
    has_next = bool(next_url)
    if current_page is not None and last_page is not None:
        has_next = current_page < last_page
    elif current_offset is not None and last_offset is not None:
        has_next = current_offset < last_offset

    exemplar_url = last_url or next_url
    exemplar_marker = _marker(exemplar_url) if exemplar_url else None
    if exemplar_url is None and numbered:
        exemplar_url = numbered[max(numbered)]
        exemplar_marker = _marker(exemplar_url)
    if exemplar_url is None and offsets:
        exemplar_url = offsets[max(offsets)]
        exemplar_marker = _marker(exemplar_url)

    remaining_pages = None
    if current_page is not None and last_page is not None:
        remaining_pages = max(0, last_page - current_page)

    result = {
        "detected": bool(pagination_urls or numbered or offsets),
        "kind": (
            "page" if numbered or (exemplar_marker and exemplar_marker["kind"] == "page")
            else "offset" if offsets or (exemplar_marker and exemplar_marker["kind"] == "offset")
            else None
        ),
        "current_page": current_page,
        "current_offset": current_offset,
        "last_page": last_page,
        "last_offset": last_offset,
        "highest_visible_page": highest_visible_page,
        "highest_visible_offset": highest_visible_offset,
        "next_url": next_url,
        "prev_url": prev_url,
        "first_url": first_url,
        "last_url": last_url,
        "last_evidence": last_source,
        "bounded": bool(last_url or last_page is not None or last_offset is not None),
        "has_next": has_next,
        "remaining_pages": remaining_pages,
        "template": _template(exemplar_url, exemplar_marker) if exemplar_url else None,
        "visible_page_numbers": sorted(numbered)[:50],
    }
    return result, pagination_urls


def _child_groups(graph, pagination_urls: set[str]) -> list[dict[str, Any]]:
    """Return complete repeated internal-link groups for host-side traversal.

    Agent-facing traversal stays bounded to samples, but a host that owns the
    crawl budget needs the actual URLs so it can exhaust a productive source
    without sending thousands of links through the agent context.
    """
    current_url = _clean_url(graph.url)
    current_scope = scope_of(current_url)
    groups: dict[str, dict[str, str]] = defaultdict(dict)

    for link in graph.links:
        if not link.internal:
            continue
        href = _clean_url(link.href)
        if href == current_url or href in pagination_urls:
            continue
        if graph.canonical and href == _clean_url(graph.canonical):
            continue
        if not _same_host(href, current_url):
            continue
        scope = scope_of(href)
        if scope == current_scope:
            continue
        groups[scope].setdefault(href, normalize_text(link.text))

    out: list[dict[str, Any]] = []
    for scope, items in groups.items():
        if len(items) < 2:
            continue
        urls = list(items)
        paths = [urlsplit(url).path for url in urls]
        same_path = len(set(paths)) == 1
        dynamic = "*" in scope or same_path
        out.append(
            {
                "scope": scope,
                "kind": "detail_candidates" if dynamic else "child_candidates",
                "count": len(urls),
                "urls": urls,
                "texts": items,
            }
        )

    out.sort(key=lambda item: (-int(item["count"]), str(item["scope"])))
    return out


def _child_collections(graph, pagination_urls: set[str], max_groups: int = 8) -> list[dict[str, Any]]:
    """Bounded agent-facing view of repeated internal-link groups."""
    out: list[dict[str, Any]] = []
    for group in _child_groups(graph, pagination_urls)[:max_groups]:
        texts = group["texts"]
        urls = group["urls"]
        out.append(
            {
                "scope": group["scope"],
                "kind": group["kind"],
                "count": group["count"],
                "sample": [
                    {"url": url, "text": texts[url]}
                    for url in urls[:5]
                ],
            }
        )
    return out


def source_frontier(graph) -> dict[str, Any]:
    """Complete host-side frontier for one already-fetched source page.

    Unlike :func:`analyze_traversal`, this function is not intended for an
    LLM context window. It returns every repeated child/detail URL visible on
    the page plus pagination metadata, so a deterministic host runner can
    exhaust high-fanout sources before returning to external discovery.
    """
    pagination, pagination_urls = _pagination(graph)
    children = []
    for group in _child_groups(graph, pagination_urls):
        children.append(
            {
                "scope": group["scope"],
                "kind": group["kind"],
                "count": group["count"],
                "urls": list(group["urls"]),
            }
        )
    return {"pagination": pagination, "child_collections": children}

def analyze_traversal(graph) -> dict[str, Any]:
    """Infer traversal structure visible from the current page.

    coverage_status is only INCOMPLETE or UNKNOWN. A single page never proves
    a whole source complete.
    """
    pagination, pagination_urls = _pagination(graph)
    children = _child_collections(graph, pagination_urls)

    signals: list[str] = []
    if pagination["has_next"]:
        signals.append("PAGINATION_OPEN")
    elif pagination["detected"] and not pagination["bounded"]:
        signals.append("PAGINATION_UNBOUNDED")
    if children:
        signals.append("CHILD_LEVEL_CANDIDATES")

    status = "INCOMPLETE" if signals else "UNKNOWN"
    known_child_urls = sum(int(group["count"]) for group in children)
    known_open = known_child_urls
    if pagination.get("remaining_pages") is not None:
        known_open += int(pagination["remaining_pages"])
    elif pagination["has_next"]:
        known_open += 1

    return {
        "coverage_status": status,
        "claim_complete": False,
        "signals": signals,
        "known_open_urls": known_open,
        "pagination": pagination,
        "child_collections": children,
        "next": (
            "Traverse pagination and candidate child levels before claiming source completeness."
            if status == "INCOMPLETE"
            else "No open traversal edge was proven on this page; source completeness is still unknown."
        ),
    }
