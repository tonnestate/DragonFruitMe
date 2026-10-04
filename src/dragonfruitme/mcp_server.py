from __future__ import annotations

import os
from typing import Any

try:
    from mcp.server import MCPServer
except ImportError as exc:  # pragma: no cover - optional dependency
    raise RuntimeError("Install DragonFruitMe with the 'mcp' extra: pip install 'dragonfruitme[mcp]'") from exc

from . import __version__
from .core import DragonFruitMe
from .policy import FetchPolicy

# Host-bound: state directory and fetch policy come from the environment the
# host starts the server with. No tool accepts a filesystem path or a policy.
_tool = DragonFruitMe(policy=FetchPolicy.from_env())

mcp = MCPServer(
    "DragonFruitMe",
    description="Escalating web-to-graph extraction and traversal evidence for AI agents: observe, locate, extract.",
    instructions=(
        "DragonFruitMe exposes exactly three operations: observe, locate, extract. It contains no LLM. "
        "observe returns a bounded page-graph overview plus traversal evidence: pagination, candidate child/detail "
        "levels and conservative coverage status. A single page never proves source completeness. locate returns the most relevant blocks for a query, "
        "ranked by on-page signals. extract escalates per field: stored recipe -> structured data -> label "
        "heuristics -> optional rendering -> NEEDS_AGENT with bounded candidates. If a field comes back "
        "NEEDS_AGENT and you can read the value in the candidates, call extract again with that field's "
        "'teach' set to the exact value; it is accepted only if it is grounded in the page, and it is "
        "compiled into a recipe. Every found value carries a provenance (AGENT_TAUGHT < ENGINE_OBSERVED < "
        "CROSS_CONFIRMED) and signals; set min_provenance on fields that matter. BLOCKED and ROBOTS_DISALLOWED are final: DragonFruitMe never bypasses "
        "CAPTCHAs, bot challenges or robots.txt - use an official API/feed or ask a human."
    ),
    version=__version__,
)


@mcp.tool()
def observe(url: str | None = None, html: str | None = None, base_url: str | None = None) -> dict[str, Any]:
    """Fetch (or take) a page and return page-graph plus traversal/coverage evidence."""
    return _tool.observe(url=url, html=html, base_url=base_url)


@mcp.tool()
def locate(query: str, url: str | None = None, html: str | None = None, base_url: str | None = None,
           max_results: int = 8) -> dict[str, Any]:
    """Return the page blocks most relevant to the query, with section path, links and label/value neighbours."""
    return _tool.locate(query=query, url=url, html=html, base_url=base_url, max_results=max_results)


@mcp.tool()
def extract(fields: list[dict[str, Any]], url: str | None = None, html: str | None = None,
            base_url: str | None = None, render: str = "auto") -> dict[str, Any]:
    """Extract typed fields through the escalation graph. Field: {name, type, aliases?, paths?, min?, max?, pattern?, teach?, min_provenance?}."""
    return _tool.extract(fields=fields, url=url, html=html, base_url=base_url, render=render)


def main() -> None:
    transport = os.environ.get("DRAGONFRUITME_MCP_TRANSPORT", "stdio")
    kwargs: dict[str, Any] = {}
    if transport in {"streamable-http", "sse"}:
        kwargs["host"] = os.environ.get("DRAGONFRUITME_MCP_HOST", "127.0.0.1")
        kwargs["port"] = int(os.environ.get("DRAGONFRUITME_MCP_PORT", "8012"))
    mcp.run(transport=transport, **kwargs)


if __name__ == "__main__":
    main()
