from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from .binding import state_dir as resolve_state_dir
from .errors import DragonFruitMeError
from .extract import Page, extract as extract_core, page_from
from .fetch import Fetcher, FetchResult
from .graph import build_graph, summarize
from .locate import locate as locate_core
from .policy import FetchPolicy
from .recipes import RecipeStore, scope_of


def safe_call(fn: Callable[..., dict[str, Any]], /, *args: Any, **kwargs: Any) -> dict[str, Any]:
    try:
        return fn(*args, **kwargs)
    except DragonFruitMeError as exc:
        return {"ok": False, "error": exc.as_dict()}
    except Exception as exc:  # keep MCP/CLI machine-readable without hiding the failure class
        return {
            "ok": False,
            "error": {
                "code": "UNEXPECTED_ERROR",
                "message": str(exc),
                "recoverable": False,
                "details": {"type": type(exc).__name__},
            },
        }


class Engine:
    """Raw operations; they raise ``DragonFruitMeError``. Use ``DragonFruitMe`` for safe calls."""

    def __init__(
        self,
        state_dir: str | Path | None = None,
        policy: FetchPolicy | None = None,
        fetcher: Fetcher | None = None,
    ):
        self.state_dir = Path(state_dir) if state_dir else resolve_state_dir()
        self.policy = policy or (fetcher.policy if fetcher else FetchPolicy())
        self.fetcher = fetcher or Fetcher(self.policy)
        self.store = RecipeStore(self.state_dir / "recipes.sqlite")

    # -- page acquisition ----------------------------------------------------
    def _first(self, url: str | None, html: str | None, base_url: str | None) -> FetchResult:
        if html is not None:
            if url:
                raise DragonFruitMeError("SOURCE_AMBIGUOUS", "Pass either 'url' or 'html', not both.")
            return self.fetcher.inline(html, base_url or "")
        if not url:
            raise DragonFruitMeError("SOURCE_REQUIRED", "Pass 'url' (fetched by policy) or 'html' (already in hand).")
        return self.fetcher.http(url)

    # -- public operations ---------------------------------------------------
    def observe(self, url: str | None = None, html: str | None = None, base_url: str | None = None,
                max_outline: int = 40, max_links: int = 25) -> dict[str, Any]:
        result = self._first(url, html, base_url)
        graph = build_graph(result.html, result.final_url or result.url)
        return {
            "ok": True,
            "page": result.info(),
            "scope": scope_of(result.final_url or result.url),
            "summary": summarize(graph, max_outline=max_outline, max_links=max_links),
            "render_available": self.fetcher.can_render(),
        }

    def locate(self, query: str, url: str | None = None, html: str | None = None, base_url: str | None = None,
               max_results: int = 8, max_context_chars: int = 4000) -> dict[str, Any]:
        if not query or not query.strip():
            raise DragonFruitMeError("QUERY_REQUIRED", "Pass a non-empty query.")
        result = self._first(url, html, base_url)
        graph = build_graph(result.html, result.final_url or result.url)
        found = locate_core(graph, query, max_results=max_results, max_context_chars=max_context_chars)
        return {"ok": True, "page": result.info(), **found}

    def extract(self, fields: list[Any], url: str | None = None, html: str | None = None,
                base_url: str | None = None, scope: str | None = None, render: str = "auto",
                learn: bool = True) -> dict[str, Any]:
        result = self._first(url, html, base_url)
        first = page_from(result)
        resolved_scope = scope or scope_of(result.final_url or result.url)
        render_page: Callable[[], Page] | None = None
        if url:
            def render_page() -> Page:
                # Raises RENDER_NOT_AVAILABLE without the browser extra; recorded as an attempt.
                return page_from(self.fetcher.render(url))
        return extract_core(
            store=self.store,
            scope=resolved_scope,
            fields=fields,
            first_page=first,
            render_page=render_page,
            render=render,
            learn=learn,
        )

    # -- host/CLI helpers (not agent operations) -----------------------------
    def recipes(self, scope: str | None = None) -> dict[str, Any]:
        return {"ok": True, "recipes": [r.as_dict() for r in self.store.list(scope)]}

    def forget(self, scope: str, field: str | None = None) -> dict[str, Any]:
        return {"ok": True, "removed": self.store.forget(scope, field)}

    def close(self) -> None:
        self.store.close()


class DragonFruitMe:
    """Three-operation web interface for agents: observe → locate → extract."""

    def __init__(self, state_dir: str | Path | None = None, policy: FetchPolicy | None = None,
                 fetcher: Fetcher | None = None):
        self.engine = Engine(state_dir=state_dir, policy=policy, fetcher=fetcher)

    def observe(self, **kwargs: Any) -> dict[str, Any]:
        return safe_call(self.engine.observe, **kwargs)

    def locate(self, **kwargs: Any) -> dict[str, Any]:
        return safe_call(self.engine.locate, **kwargs)

    def extract(self, **kwargs: Any) -> dict[str, Any]:
        return safe_call(self.engine.extract, **kwargs)

    def recipes(self, **kwargs: Any) -> dict[str, Any]:
        return safe_call(self.engine.recipes, **kwargs)

    def forget(self, **kwargs: Any) -> dict[str, Any]:
        return safe_call(self.engine.forget, **kwargs)

    def close(self) -> None:
        """Close the recipe store connection (also happens automatically on garbage collection)."""
        self.engine.close()

    def __enter__(self) -> "DragonFruitMe":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()
