from __future__ import annotations

from collections import deque
from typing import Any, Callable

from .errors import DragonFruitMeError
from .graph import build_graph
from .traversal import source_frontier


def _format_page(template: str, page: int) -> str:
    return template.replace("{page}", str(page))


def expand_source(
    fetch: Callable[[str], Any],
    seed_urls: list[str],
    *,
    max_pages: int = 5000,
    max_urls: int = 100000,
    max_depth: int = 3,
) -> dict[str, Any]:
    """Deterministically exhaust productive source structure.

    Pagination pages stay at the same hierarchy depth. Repeated
    child_candidates are followed as lower listing levels up to max_depth.
    Repeated detail_candidates are materialized as final detail URLs for
    later extraction and are not fetched here.

    Limits are host-owned safety budgets. Reaching one never implies that the
    source is complete.
    """
    queue: deque[tuple[str, int]] = deque()
    queued: set[str] = set()
    limited_by: str | None = None
    for raw in seed_urls:
        url = str(raw).strip()
        if not url or url in queued:
            continue
        if len(queue) >= max_pages:
            limited_by = "max_pages"
            break
        queued.add(url)
        queue.append((url, 0))

    fetched: set[str] = set()
    detail_urls: list[str] = []
    detail_seen: set[str] = set()
    errors: list[dict[str, Any]] = []
    source_pages: list[dict[str, Any]] = []

    def enqueue(url: str | None, depth: int) -> bool:
        nonlocal limited_by
        if not url or url in fetched or url in queued:
            return False
        if len(fetched) + len(queue) >= max_pages:
            limited_by = limited_by or "max_pages"
            return False
        queued.add(url)
        queue.append((url, depth))
        return True

    def add_detail(url: str) -> bool:
        nonlocal limited_by
        if url in detail_seen:
            return True
        if len(detail_urls) >= max_urls:
            limited_by = limited_by or "max_urls"
            return False
        detail_seen.add(url)
        detail_urls.append(url)
        return True

    while queue:
        if len(fetched) >= max_pages:
            limited_by = limited_by or "max_pages"
            break

        url, depth = queue.popleft()
        queued.discard(url)
        if url in fetched:
            continue
        fetched.add(url)

        try:
            result = fetch(url)
        except DragonFruitMeError as exc:
            errors.append({"url": url, "error": exc.as_dict()})
            continue
        except Exception as exc:
            errors.append({
                "url": url,
                "error": {
                    "code": "UNEXPECTED_ERROR",
                    "message": str(exc),
                    "recoverable": False,
                    "details": {"type": type(exc).__name__},
                },
            })
            continue

        final_url = result.final_url or result.url or url
        graph = build_graph(result.html, final_url)
        frontier = source_frontier(graph)
        pagination = frontier["pagination"]

        page_details = 0
        page_children = 0
        for group in frontier["child_collections"]:
            urls = group["urls"]
            if group["kind"] == "detail_candidates":
                for child_url in urls:
                    before = len(detail_seen)
                    if not add_detail(child_url):
                        break
                    if len(detail_seen) > before:
                        page_details += 1
            elif depth < max_depth:
                for child_url in urls:
                    if enqueue(child_url, depth + 1):
                        page_children += 1

        template = pagination.get("template")
        last_page = pagination.get("last_page")
        if template and last_page is not None and "{page}" in template:
            terminal = int(last_page)
            if terminal > max_pages:
                limited_by = limited_by or "max_pages"
            for page in range(1, min(terminal, max_pages) + 1):
                enqueue(_format_page(template, page), depth)
        else:
            enqueue(pagination.get("next_url"), depth)

        source_pages.append({
            "url": final_url,
            "depth": depth,
            "detail_urls": page_details,
            "child_pages": page_children,
            "pagination_bounded": bool(pagination.get("bounded")),
            "last_page": pagination.get("last_page"),
        })

        if limited_by == "max_urls":
            break

    exhausted = not queue and limited_by is None
    return {
        "ok": True,
        "seed_urls": len(seed_urls),
        "source_pages_fetched": len(source_pages),
        "detail_urls": detail_urls,
        "detail_count": len(detail_urls),
        "errors": errors,
        "error_count": len(errors),
        "frontier_exhausted": exhausted,
        "limited_by": limited_by,
        "remaining_source_pages": len(queue),
        "fanout_per_source_page": round(len(detail_urls) / len(source_pages), 3) if source_pages else 0.0,
        "source_pages": source_pages,
    }
