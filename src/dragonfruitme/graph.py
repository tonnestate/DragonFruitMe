"""Website -> graph.

Turns raw HTML into a small typed graph that an agent can query instead of
reading markup:

    page ─contains→ section (heading hierarchy) ─contains→ block ─links_to→ url

Each block carries an on-page relevance weight derived from classic SEO
signals (title, heading level, emphasis, anchor text, page region). The graph
is built with the standard-library HTML parser only.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlsplit

from .values import normalize_text

HEADINGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}
BLOCK_TAGS = {
    "p", "li", "td", "th", "dt", "dd", "blockquote", "figcaption", "caption",
    "label", "pre", "address", "summary", "legend", "option",
}
BOUNDARY_TAGS = {
    "div", "section", "article", "main", "header", "footer", "nav", "aside",
    "ul", "ol", "dl", "table", "tr", "tbody", "thead", "form", "body", "html",
    "figure", "details", "fieldset", "select",
}
REGIONS = ("nav", "footer", "aside", "header")
SKIP_TAGS = {"script", "style", "noscript", "template", "svg", "iframe", "object"}
EMPHASIS_TAGS = {"strong", "b", "em", "mark"}
VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
    "param", "source", "track", "wbr",
}

# BM25F-style field weights (on-page relevance signals).
HEADING_WEIGHT = {1: 4.0, 2: 3.0, 3: 2.5, 4: 2.0, 5: 2.0, 6: 2.0}
TITLE_WEIGHT = 5.0
META_DESCRIPTION_WEIGHT = 3.0
BODY_WEIGHT = 1.0
REGION_FACTOR = {"main": 1.0, "header": 0.6, "aside": 0.5, "nav": 0.3, "footer": 0.3}


@dataclass
class Link:
    href: str
    text: str
    internal: bool
    nofollow: bool
    block_id: int | None
    rel: tuple[str, ...] = field(default_factory=tuple)


@dataclass
class Block:
    id: int
    kind: str
    text: str
    section: tuple[str, ...]
    region: str
    weight: float
    emphasis: list[str] = field(default_factory=list)
    row: int | None = None
    heading_level: int | None = None

    def as_dict(self, max_chars: int | None = None) -> dict[str, Any]:
        text = self.text if max_chars is None or len(self.text) <= max_chars else self.text[:max_chars] + "…"
        return {
            "block_id": self.id,
            "kind": self.kind,
            "text": text,
            "section": list(self.section),
            "region": self.region,
        }


@dataclass
class PageGraph:
    url: str
    title: str = ""
    lang: str = ""
    meta: dict[str, str] = field(default_factory=dict)
    canonical: str = ""
    feeds: list[str] = field(default_factory=list)
    blocks: list[Block] = field(default_factory=list)
    links: list[Link] = field(default_factory=list)
    scripts: list[dict[str, str]] = field(default_factory=list)
    itemprops: list[tuple[str, str]] = field(default_factory=list)
    images: int = 0
    images_missing_alt: int = 0
    script_count: int = 0
    app_root: bool = False
    # per-page cache of flattened structured data (filled by structured.flatten_page)
    flat_cache: list[tuple[str, str, Any]] | None = field(default=None, repr=False, compare=False)

    @property
    def text_chars(self) -> int:
        return sum(len(b.text) for b in self.blocks)

    def js_shell_score(self) -> float:
        """0..1 likelihood that meaningful content needs JavaScript rendering."""
        score = 0.0
        if self.text_chars < 400:
            score += 0.45
        if self.app_root:
            score += 0.3
        if self.script_count >= 5:
            score += 0.15
        if any(s["type"] == "embedded_json" for s in self.scripts):
            score -= 0.2  # data is present even without rendering
        if any(s["type"] == "jsonld" for s in self.scripts):
            score -= 0.1
        return max(0.0, min(1.0, round(score, 2)))

    def outline(self, max_items: int = 40) -> list[dict[str, Any]]:
        items = [
            {"level": b.heading_level, "text": b.text, "block_id": b.id}
            for b in self.blocks
            if b.heading_level is not None
        ]
        return items[:max_items]


class _GraphBuilder(HTMLParser):
    def __init__(self, url: str):
        super().__init__(convert_charrefs=True)
        self.graph = PageGraph(url=url)
        self._host = (urlsplit(url).hostname or "").lower()
        self._base = url
        self._skip_depth = 0
        self._skip_tag: list[str] = []
        self._script_attrs: dict[str, str] | None = None
        self._script_buf: list[str] = []
        self._in_title = False
        self._title_buf: list[str] = []
        self._region_depth = {r: 0 for r in REGIONS}
        self._buf: list[str] = []
        self._buf_kind = "text"
        self._emph_depth = 0
        self._emph_buf: list[str] = []
        self._emph_current: list[str] = []
        self._heading_level: int | None = None
        self._sections: list[tuple[int, str]] = []
        self._link: dict[str, Any] | None = None
        self._row_counter = 0
        self._row: int | None = None
        self._itemprop_stack: list[tuple[str, str, list[str]]] = []
        self._next_id = 0

    # -- helpers -----------------------------------------------------------
    def _region(self) -> str:
        for region in REGIONS:
            if self._region_depth[region] > 0:
                return region
        return "main"

    def _flush(self) -> None:
        text = normalize_text("".join(self._buf))
        kind = self._buf_kind
        self._buf = []
        self._buf_kind = "text"
        emphasis = [normalize_text(e) for e in self._emph_buf if normalize_text(e)]
        self._emph_buf = []
        if not text:
            return
        region = self._region()
        level = HEADINGS.get(kind)
        weight = HEADING_WEIGHT[level] if level else BODY_WEIGHT
        block = Block(
            id=self._next_id,
            kind=kind,
            text=text,
            section=tuple(t for _, t in self._sections),
            region=region,
            weight=weight * REGION_FACTOR.get(region, 1.0),
            emphasis=emphasis,
            row=self._row if kind in {"td", "th"} else None,
            heading_level=level,
        )
        self._next_id += 1
        self.graph.blocks.append(block)
        if level:
            while self._sections and self._sections[-1][0] >= level:
                self._sections.pop()
            self._sections.append((level, text))

    # -- HTMLParser hooks --------------------------------------------------
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k.lower(): (v or "") for k, v in attrs}
        if self._skip_depth:
            if tag == self._skip_tag[-1] and tag not in VOID_TAGS:
                self._skip_depth += 1
            return
        if tag == "script":
            self.graph.script_count += 1
            script_type = a.get("type", "").lower()
            if script_type == "application/ld+json" or script_type == "application/json" or a.get("id") == "__NEXT_DATA__":
                self._script_attrs = {"type": "jsonld" if "ld+json" in script_type else "embedded_json", "id": a.get("id", "")}
                self._script_buf = []
            self._skip_tag.append(tag)
            self._skip_depth = 1
            return
        if tag == "title" and not self.graph.title:
            self._in_title = True
            self._title_buf = []
            return
        if tag in SKIP_TAGS:
            self._skip_tag.append(tag)
            self._skip_depth = 1
            return
        if tag == "html" and a.get("lang"):
            self.graph.lang = a["lang"]
        if tag == "meta":
            self._meta(a)
            return
        if tag == "link":
            self._linktag(a)
            return
        if tag == "base" and a.get("href"):
            self._base = urljoin(self._base, a["href"])
            return
        if tag == "img":
            self.graph.images += 1
            if not a.get("alt", "").strip():
                self.graph.images_missing_alt += 1
            return
        if tag == "br":
            self._buf.append(" ")
            return
        if a.get("id") in {"root", "app", "__next", "__nuxt"} and tag == "div":
            self.graph.app_root = True
        if "itemprop" in a:
            if a.get("content"):
                self.graph.itemprops.append((a["itemprop"], a["content"]))
            else:
                self._itemprop_stack.append((tag, a["itemprop"], []))
        if tag in REGIONS:
            self._flush()
            self._region_depth[tag] += 1
        if tag == "tr":
            self._flush()
            self._row_counter += 1
            self._row = self._row_counter
        if tag in HEADINGS or tag in BLOCK_TAGS:
            self._flush()
            self._buf_kind = tag
        elif tag in BOUNDARY_TAGS:
            self._flush()
        if tag in EMPHASIS_TAGS:
            self._emph_depth += 1
            self._emph_current = []
        if tag == "a":
            self._link = {
                "href": a.get("href", ""),
                "rel": a.get("rel", "").lower(),
                "buf": [],
            }

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in VOID_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if self._skip_depth:
            if tag == self._skip_tag[-1]:
                self._skip_depth -= 1
                if self._skip_depth == 0:
                    self._skip_tag.pop()
                    if tag == "script" and self._script_attrs is not None:
                        self.graph.scripts.append({**self._script_attrs, "content": "".join(self._script_buf)})
                        self._script_attrs = None
                        self._script_buf = []
            return
        if tag == "title" and self._in_title:
            self._in_title = False
            self.graph.title = normalize_text("".join(self._title_buf))
            return
        if self._itemprop_stack and self._itemprop_stack[-1][0] == tag:
            _, name, buf = self._itemprop_stack.pop()
            value = normalize_text("".join(buf))
            if value:
                self.graph.itemprops.append((name, value))
        if tag in EMPHASIS_TAGS and self._emph_depth:
            self._emph_depth -= 1
            if self._emph_depth == 0:
                self._emph_buf.append("".join(self._emph_current))
        if tag == "a" and self._link is not None:
            self._finish_link()
        if tag in HEADINGS or tag in BLOCK_TAGS or tag in BOUNDARY_TAGS:
            self._flush()
        if tag == "tr":
            self._row = None
        if tag in REGIONS and self._region_depth[tag] > 0:
            self._flush()
            self._region_depth[tag] -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            if self._script_attrs is not None:
                self._script_buf.append(data)
            return
        if self._in_title:
            self._title_buf.append(data)
            return
        self._buf.append(data)
        if self._emph_depth:
            self._emph_current.append(data)
        if self._link is not None:
            self._link["buf"].append(data)
        for _, _, buf in self._itemprop_stack:
            buf.append(data)

    # -- tag-specific ------------------------------------------------------
    def _meta(self, a: dict[str, str]) -> None:
        key = (a.get("name") or a.get("property") or a.get("itemprop") or "").lower()
        if a.get("charset"):
            self.graph.meta.setdefault("charset", a["charset"])
        if key and "content" in a:
            self.graph.meta.setdefault(key, normalize_text(a["content"]))
            if a.get("itemprop"):
                self.graph.itemprops.append((a["itemprop"], a["content"]))

    def _linktag(self, a: dict[str, str]) -> None:
        rel = a.get("rel", "").lower().split()
        href = a.get("href", "")
        if not href:
            return
        if "canonical" in rel:
            self.graph.canonical = urljoin(self._base, href)
        if "alternate" in rel and a.get("type", "").lower() in {"application/rss+xml", "application/atom+xml", "application/feed+json"}:
            self.graph.feeds.append(urljoin(self._base, href))

    def _finish_link(self) -> None:
        link = self._link
        self._link = None
        if link is None:
            return
        href = link["href"].strip()
        if not href or href.startswith(("javascript:", "mailto:", "tel:", "#")):
            return
        absolute = urljoin(self._base, href)
        host = (urlsplit(absolute).hostname or "").lower()
        rel = tuple(dict.fromkeys(part for part in link["rel"].split() if part))
        self.graph.links.append(
            Link(
                href=absolute,
                text=normalize_text("".join(link["buf"])),
                internal=host == self._host,
                nofollow="nofollow" in rel,
                block_id=self._next_id,  # the block currently being filled
                rel=rel,
            )
        )

    def close(self) -> None:
        super().close()
        self._flush()


def build_graph(html: str, url: str = "") -> PageGraph:
    builder = _GraphBuilder(url)
    builder.feed(html)
    builder.close()
    return builder.graph


def summarize(graph: PageGraph, *, max_outline: int = 40, max_links: int = 25) -> dict[str, Any]:
    """Bounded overview of the page graph (the ``observe`` payload)."""
    internal = [l for l in graph.links if l.internal]
    external = [l for l in graph.links if not l.internal]
    outline = graph.outline(max_outline + 1)
    top_links: list[dict[str, Any]] = []
    seen: set[str] = set()
    for link in internal:
        if link.href in seen or not link.text:
            continue
        seen.add(link.href)
        top_links.append({"href": link.href, "text": link.text})
        if len(top_links) >= max_links:
            break
    js_score = graph.js_shell_score()
    return {
        "title": graph.title,
        "lang": graph.lang,
        "meta_description": graph.meta.get("description", ""),
        "meta_robots": graph.meta.get("robots", ""),
        "canonical": graph.canonical,
        "h1": [b.text for b in graph.blocks if b.heading_level == 1][:5],
        "outline": outline[:max_outline],
        "outline_truncated": len(outline) > max_outline,
        "counts": {
            "blocks": len(graph.blocks),
            "text_chars": graph.text_chars,
            "links_internal": len(internal),
            "links_external": len(external),
            "links_nofollow": sum(1 for l in graph.links if l.nofollow),
            "images": graph.images,
            "images_missing_alt": graph.images_missing_alt,
        },
        "structured_data": {
            "jsonld_blocks": sum(1 for s in graph.scripts if s["type"] == "jsonld"),
            "embedded_json_blocks": sum(1 for s in graph.scripts if s["type"] == "embedded_json"),
            "itemprops": len(graph.itemprops),
            "open_graph": sorted(k for k in graph.meta if k.startswith("og:"))[:20],
        },
        "feeds": graph.feeds[:10],
        "internal_links": top_links,
        "internal_links_truncated": len({l.href for l in internal if l.text}) > len(top_links),
        "js_shell_score": js_score,
        "needs_render": js_score >= 0.6,
    }
