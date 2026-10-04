"""Structured data already present in the page: JSON-LD, embedded JSON,
microdata ``itemprop`` values and meta/Open Graph tags.

This is escalation stage 2. It is cheap, usually stable across redesigns, and
is checked before any text heuristic.
"""
from __future__ import annotations

import json
from typing import Any, Iterator

from .graph import PageGraph
from .values import fold, normalize_text

MAX_FLAT_ITEMS = 5000


def _load_json(text: str) -> Any:
    text = text.strip()
    if not text:
        return None
    if text.startswith("<!--"):
        text = text[4:]
    if text.endswith("-->"):
        text = text[:-3]
    try:
        return json.loads(text)
    except (ValueError, RecursionError):
        return None


def _flatten(obj: Any, prefix: str, out: list[tuple[str, Any]]) -> None:
    if len(out) >= MAX_FLAT_ITEMS:
        return
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key == "@graph":
                _flatten(value, prefix, out)
                continue
            path = f"{prefix}.{key}" if prefix else str(key)
            _flatten(value, path, out)
    elif isinstance(obj, list):
        for item in obj:
            _flatten(item, prefix, out)
    elif obj is not None and not isinstance(obj, bool):
        out.append((prefix, obj))


def flatten_page(graph: PageGraph) -> list[tuple[str, str, Any]]:
    """Return ``(source, dotted_path, value)`` triples for all structured data."""
    triples: list[tuple[str, str, Any]] = []
    for index, script in enumerate(graph.scripts):
        data = _load_json(script["content"])
        if data is None:
            continue
        flat: list[tuple[str, Any]] = []
        _flatten(data, "", flat)
        source = f"{script['type']}[{index}]"
        triples.extend((source, path, value) for path, value in flat)
    for name, value in graph.itemprops:
        triples.append(("microdata", name, value))
    for key, value in graph.meta.items():
        triples.append(("meta", key, value))
    return triples[: MAX_FLAT_ITEMS * 2]


def _path_keys(path: str) -> list[str]:
    return [fold(part) for part in path.replace(":", ".").split(".") if part]


def candidates(graph: PageGraph, spec: dict[str, Any]) -> Iterator[tuple[str, str, str]]:
    """Yield ``(source, path, raw_value)`` matching the field's aliases or paths.

    Explicit ``paths`` (dotted, e.g. ``offers.price``) match the end of a path;
    ``aliases`` match the last key of a path.
    """
    aliases = {fold(a) for a in spec.get("aliases", [])} | {fold(spec["name"])}
    aliases = {a.replace(" ", "") for a in aliases} | aliases
    paths = [_path_keys(p) for p in spec.get("paths", [])]
    explicit: list[tuple[str, str, str]] = []
    by_alias: list[tuple[str, str, str]] = []
    for source, path, value in flatten_page(graph):
        keys = _path_keys(path)
        if not keys:
            continue
        raw = normalize_text(value)
        if not raw:
            continue
        if any(keys[-len(p):] == p for p in paths if p and len(p) <= len(keys)):
            explicit.append((source, path, raw))
        elif keys[-1] in aliases:
            by_alias.append((source, path, raw))
    yield from explicit
    yield from by_alias


def lookup(graph: PageGraph, path: str) -> str | None:
    """Resolve a stored ``source|path`` json recipe against the page."""
    source_kind, _, dotted = path.partition("|")
    for source, candidate_path, value in flatten_page(graph):
        kind = source.split("[", 1)[0]
        if kind == source_kind and candidate_path == dotted:
            return normalize_text(value)
    return None
