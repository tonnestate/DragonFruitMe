"""The escalation graph: per-field extraction from cheapest to most expensive.

    recipe ──miss──▶ structured ──miss──▶ label ──miss──▶ render ──miss──▶ agent
      ▲                  │                  │               │
      └──── compile ◀────┴──────────────────┴───────────────┘
                         (and agent-taught values)

Every stage validates its candidate against the field type before it counts.
A hit in a costly stage is compiled into a recipe so the next page of the same
template is served by stage 1. The agent stage never invents a value: it
returns bounded evidence, and a value the agent teaches must be grounded in the
page before it is accepted.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Iterator

from . import structured
from .errors import DragonFruitMeError
from .graph import Block, PageGraph, build_graph
from .locate import locate
from .recipes import Recipe, RecipeStore, apply_regex, derive_regex
from .values import FIELD_TYPES, equivalent, find_typed, fold, normalize_text, validate, value_regex

LABEL_MAX_CHARS = 60
STRUCTURAL_LOOKAHEAD = 8
RENDER_MODES = {"auto", "on_miss", "never"}

# Who stands behind a value (lowest to highest). An agent can never raise the
# provenance of its own claim: only independent engine stages can.
PROVENANCE = ("AGENT_TAUGHT", "ENGINE_OBSERVED", "CROSS_CONFIRMED")
# The same unconfirmed value on this many different URLs of one scope is a
# signal that a recipe is anchored on something page-independent (a sidebar,
# a teaser, a default) - activity that is not progress.
REPEAT_THRESHOLD = 3


@dataclass
class Page:
    html: str
    graph: PageGraph
    stage: str  # inline | http | render
    info: dict[str, Any]


def normalize_spec(raw: Any) -> dict[str, Any]:
    if isinstance(raw, str):
        raw = {"name": raw}
    if not isinstance(raw, dict) or not str(raw.get("name", "")).strip():
        raise DragonFruitMeError("FIELD_INVALID", "Each field needs at least a non-empty 'name'.", details={"field": raw})
    spec = dict(raw)
    spec["name"] = str(spec["name"]).strip()
    spec["type"] = str(spec.get("type", "text")).strip().lower()
    if spec["type"] not in FIELD_TYPES:
        raise DragonFruitMeError(
            "FIELD_TYPE_UNKNOWN",
            f"Unknown field type '{spec['type']}'.",
            details={"field": spec["name"], "allowed": sorted(FIELD_TYPES)},
        )
    aliases = spec.get("aliases") or []
    if isinstance(aliases, str):
        aliases = [aliases]
    names = [spec["name"], *[str(a) for a in aliases]]
    spec["aliases"] = list(dict.fromkeys(n.strip() for n in names if str(n).strip()))
    paths = spec.get("paths") or []
    spec["paths"] = [paths] if isinstance(paths, str) else list(paths)
    minimum = spec.get("min_provenance")
    if minimum is not None:
        minimum = str(minimum).strip().upper()
        if minimum not in PROVENANCE:
            raise DragonFruitMeError(
                "FIELD_INVALID",
                f"Unknown min_provenance '{spec['min_provenance']}'.",
                details={"field": spec["name"], "allowed": list(PROVENANCE)},
            )
        spec["min_provenance"] = minimum
    return spec


# -- label heuristic --------------------------------------------------------

def _alias_regex(alias: str) -> re.Pattern[str]:
    folded = re.escape(fold(alias))
    for plain, umlaut in (("ae", "ä"), ("oe", "ö"), ("ue", "ü"), ("ss", "ß")):
        folded = folded.replace(plain, f"(?:{plain}|{umlaut})")
    folded = folded.replace(r"\ ", r"\s+")
    return re.compile(rf"(?<![\wäöüß]){folded}(?![a-zäöüß])", re.I)


def _value_from(text: str, field_type: str) -> str | None:
    text = normalize_text(text).lstrip(":-–— \t")
    if not text:
        return None
    if field_type == "text":
        return text[:300]
    return find_typed(text, field_type)


def _next_value_block(graph: PageGraph, block: Block) -> Block | None:
    blocks = graph.blocks
    index = block.id + 1
    if index >= len(blocks):
        return None
    nxt = blocks[index]
    if block.row is not None:
        return nxt if nxt.row == block.row else None
    return nxt if nxt.section == block.section else None


def _shared_ancestry(left: Block, right: Block) -> int:
    """Number of identical structural container nodes from the document root."""
    shared = 0
    for a, b in zip(left.ancestry, right.ancestry):
        if a != b:
            break
        shared += 1
    return shared


def _structural_value_blocks(
    graph: PageGraph,
    label_block: Block,
    patterns: list[re.Pattern[str]],
    field_type: str,
) -> Iterator[Block]:
    """Yield nearby value blocks in the same bounded DOM neighbourhood.

    Modern component/CSS layouts often put a label and its value in sibling
    wrappers with decorative/help blocks in between. Adjacency alone misses
    those layouts. We therefore inspect only the next few blocks and require a
    shared structural ancestor, same section and same page region. Typed
    validation still decides whether a candidate can count.
    """
    ranked: list[tuple[int, int, Block]] = []
    upper = min(len(graph.blocks), label_block.id + STRUCTURAL_LOOKAHEAD + 1)
    for candidate in graph.blocks[label_block.id + 1:upper]:
        if candidate.heading_level is not None:
            continue
        if candidate.region != label_block.region or candidate.section != label_block.section:
            continue
        shared = _shared_ancestry(label_block, candidate)
        # Require the two blocks to diverge only at (roughly) their immediate
        # wrapper. This admits sibling component wrappers but prevents a label
        # in one card from stealing a typed value from the next card.
        required_shared = max(1, min(len(label_block.ancestry), len(candidate.ancestry)) - 1)
        if shared < required_shared:
            continue
        # A short block that itself looks like one of our labels is not a value.
        if len(candidate.text) <= LABEL_MAX_CHARS and any(p.search(candidate.text) for p in patterns):
            continue
        if _value_from(candidate.text, field_type) is None:
            continue
        distance = candidate.id - label_block.id
        ranked.append((shared, -distance, candidate))
    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    for _, _, candidate in ranked:
        yield candidate


def label_candidates(graph: PageGraph, labels: list[str], field_type: str) -> Iterator[tuple[str, str, int]]:
    """Yield ``(raw_value, label_text, block_id)``: main region first, exact labels first."""
    patterns = [_alias_regex(a) for a in labels if a.strip()]
    if not patterns:
        return
    ordered = sorted(graph.blocks, key=lambda b: (b.region != "main", b.id))
    exact: list[tuple[str, str, int]] = []
    attributed: list[tuple[str, str, int]] = []
    structural: list[tuple[str, str, int]] = []
    inline: list[tuple[str, str, int]] = []
    structural_seen: set[tuple[int, int]] = set()
    for block in ordered:
        if block.heading_level is not None and len(block.text) > LABEL_MAX_CHARS:
            continue
        for hint in block.label_hints:
            if not any(pattern.search(hint) for pattern in patterns):
                continue
            value = _value_from(block.text, field_type)
            if value and fold(value) != fold(hint):
                attributed.append((value, hint.rstrip(": "), block.id))
                break
        for pattern in patterns:
            match = pattern.search(block.text)
            if not match:
                continue
            rest = block.text[match.end():]
            is_label = len(block.text) <= LABEL_MAX_CHARS and len(normalize_text(rest).strip(":-–— ")) == 0
            if is_label:
                nxt = _next_value_block(graph, block)
                if nxt is not None:
                    value = _value_from(nxt.text, field_type)
                    if value:
                        exact.append((value, block.text.rstrip(": "), nxt.id))
                        structural_seen.add((block.id, nxt.id))
                for candidate in _structural_value_blocks(graph, block, patterns, field_type):
                    if (block.id, candidate.id) in structural_seen:
                        continue
                    value = _value_from(candidate.text, field_type)
                    if value:
                        structural.append((value, block.text.rstrip(": "), candidate.id))
                        structural_seen.add((block.id, candidate.id))
            else:
                value = _value_from(rest, field_type)
                if value:
                    inline.append((value, match.group(0), block.id))
            break
    yield from exact
    yield from attributed
    yield from structural
    yield from inline


# -- independent confirmation ----------------------------------------------------

def _stage_view(candidates: Iterator[tuple[str, Any]], spec: dict[str, Any], value: str,
                typed_source: bool) -> tuple[bool, str | None]:
    """Return (agrees, primary) for one engine stage: does any valid candidate
    equal ``value``, and what would the stage itself have answered?"""
    primary = None
    for raw, _ in candidates:
        ok, _, _ = validate(raw, spec, typed_source=typed_source)
        if not ok:
            continue
        if primary is None:
            primary = normalize_text(raw)
        if equivalent(raw, value, spec["type"]):
            return True, primary
    return False, primary


def independent_check(page: Page, spec: dict[str, Any], value: str) -> tuple[list[str], list[dict[str, Any]]]:
    """Ask the structured and label stages independently about ``value``.

    Returns ``(confirmed_by, disagreements)``. A stage disagrees when it has a
    valid answer of its own and none of its candidates equals ``value``.
    """
    confirmed: list[str] = []
    disagreements: list[dict[str, Any]] = []
    views = {
        "structured": _stage_view(((raw, path) for _, path, raw in structured.candidates(page.graph, spec)),
                                  spec, value, typed_source=True),
        "label": _stage_view(((raw, label) for raw, label, _ in label_candidates(page.graph, spec["aliases"], spec["type"])),
                             spec, value, typed_source=False),
    }
    for stage, (agrees, primary) in views.items():
        if agrees:
            confirmed.append(stage)
        elif primary is not None:
            disagreements.append({"stage": stage, "value": primary})
    return confirmed, disagreements


def provenance_of(origin: str, confirmed: list[str]) -> str:
    if len(confirmed) >= 2:
        return "CROSS_CONFIRMED"
    if confirmed:
        return "ENGINE_OBSERVED"
    return "AGENT_TAUGHT" if origin == "taught" else "ENGINE_OBSERVED"


# -- the graph ----------------------------------------------------------------

class Extractor:
    def __init__(self, store: RecipeStore):
        self.store = store

    # stage 1
    def _apply_recipe(self, recipe: Recipe, page: Page, spec: dict[str, Any]) -> tuple[str | None, dict[str, Any]]:
        if recipe.kind == "json":
            return structured.lookup(page.graph, recipe.pattern), {"path": recipe.pattern}
        if recipe.kind == "regex":
            return apply_regex(recipe.pattern, page.html), {}
        if recipe.kind == "label":
            for value, label, block_id in label_candidates(page.graph, [recipe.pattern], spec["type"]):
                return value, {"label": label, "block_id": block_id}
        return None, {}

    def _compile(self, scope: str, spec: dict[str, Any], page: Page, value: str, origin: str,
                 json_path: str | None = None, label: str | None = None) -> dict[str, Any] | None:
        """Turn a costly hit into a cheap recipe. Most stable kind wins."""
        kind = pattern = None
        if json_path:
            kind, pattern = "json", json_path
        else:
            regex = derive_regex(page.html, value, spec["type"], label=label)
            if regex:
                kind, pattern = "regex", regex
            elif label:
                kind, pattern = "label", label
        if not kind or not pattern:
            return None
        recipe = Recipe(scope=scope, field=spec["name"], kind=kind, pattern=pattern, origin=origin)
        self.store.put(recipe)
        return {"kind": kind, "pattern": pattern, "scope": scope, "compiled": True}

    def run_cheap_stages(self, scope: str, spec: dict[str, Any], page: Page, attempts: list[dict[str, Any]],
                         learn: bool) -> dict[str, Any] | None:
        name = spec["name"]
        # 1 recipe
        recipe = self.store.get(scope, name)
        if recipe is None:
            attempts.append({"stage": "recipe", "page": page.stage, "outcome": "SKIP", "reason": "NO_RECIPE"})
        else:
            raw, evidence = self._apply_recipe(recipe, page, spec)
            ok, normalized, reason = validate(raw, spec) if raw else (False, None, "NO_MATCH")
            check = independent_check(page, spec, raw) if ok else ([], [])
            contradicted = (
                ok
                and recipe.kind in {"regex", "label"}
                and recipe.origin in {"label", "structured"}
                and not check[0]
                and bool(check[1])
            )
            if ok and not contradicted:
                self.store.record(scope, name, True)
                attempts.append({"stage": "recipe", "page": page.stage, "outcome": "HIT", "kind": recipe.kind})
                found = self._found(spec, raw, normalized, "recipe", page,
                                    {"recipe_kind": recipe.kind, **evidence},
                                    {"kind": recipe.kind, "pattern": recipe.pattern, "scope": scope, "compiled": False})
                return self._assess(scope, spec, page, found, recipe.origin, check)
            if contradicted:
                reason = "CONTRADICTED"
            status = self.store.record(scope, name, False)
            miss = {"stage": "recipe", "page": page.stage, "outcome": "MISS", "reason": reason,
                    "kind": recipe.kind, "recipe_status": status}
            if contradicted:
                # the recipe answered, but an independent stage on this page says otherwise
                miss["recipe_value"] = normalize_text(raw)
                miss["contradicted_by"] = check[1]
            attempts.append(miss)
        # 2 structured
        seen = 0
        for source, path, raw in structured.candidates(page.graph, spec):
            seen += 1
            ok, normalized, reason = validate(raw, spec, typed_source=True)
            if ok:
                kind = source.split("[", 1)[0]
                attempts.append({"stage": "structured", "page": page.stage, "outcome": "HIT", "source": kind})
                compiled = self._compile(scope, spec, page, raw, "structured", json_path=f"{kind}|{path}") if learn else None
                found = self._found(spec, raw, normalized, "structured", page, {"source": kind, "path": path}, compiled)
                return self._assess(scope, spec, page, found, "structured")
        attempts.append({"stage": "structured", "page": page.stage, "outcome": "MISS",
                         "reason": "NO_VALID_CANDIDATE" if seen else "NO_CANDIDATE", "candidates_checked": seen})
        # 3 label
        seen = 0
        for raw, label, block_id in label_candidates(page.graph, spec["aliases"], spec["type"]):
            seen += 1
            ok, normalized, reason = validate(raw, spec)
            if ok:
                attempts.append({"stage": "label", "page": page.stage, "outcome": "HIT", "label": label})
                compiled = self._compile(scope, spec, page, raw, "label", label=label) if learn else None
                found = self._found(spec, raw, normalized, "label", page, {"label": label, "block_id": block_id}, compiled)
                return self._assess(scope, spec, page, found, "label")
        attempts.append({"stage": "label", "page": page.stage, "outcome": "MISS",
                         "reason": "NO_VALID_CANDIDATE" if seen else "NO_LABEL", "candidates_checked": seen})
        return None

    def teach(self, scope: str, spec: dict[str, Any], page: Page, attempts: list[dict[str, Any]]) -> dict[str, Any]:
        """Ground an agent-supplied value in the page and compile it."""
        taught = normalize_text(spec["teach"])
        ok, normalized, reason = validate(taught, spec)
        if not ok:
            attempts.append({"stage": "taught", "page": page.stage, "outcome": "REJECTED", "reason": reason})
            return self._status(spec, "NOT_GROUNDED", attempts, reason=f"taught value fails validation: {reason}")
        json_path = None
        for source, path, raw in structured.candidates(page.graph, {**spec, "aliases": spec["aliases"]}):
            raw_ok, raw_normalized, _ = validate(raw, spec, typed_source=True)
            if raw_ok and raw_normalized == normalized:
                json_path = f"{source.split('[', 1)[0]}|{path}"
                break
        in_html = re.search(value_regex(taught), page.html, re.I) is not None
        in_text = fold(taught) in fold(" ".join(b.text for b in page.graph.blocks))
        if not (json_path or in_html or in_text):
            attempts.append({"stage": "taught", "page": page.stage, "outcome": "REJECTED", "reason": "VALUE_NOT_IN_PAGE"})
            return self._status(spec, "NOT_GROUNDED", attempts, reason="taught value does not occur in the page")
        label = None
        for block in page.graph.blocks:
            position = block.text.lower().find(taught.lower())
            if position < 0:
                continue
            prefix = block.text[:position].rstrip()
            if prefix.endswith(":"):
                # inline label: "Heizung: Fernwärme, ..." -> "Heizung"
                candidate = re.split(r"[.;,!?]", prefix.rstrip(": "))[-1].strip()
                if 0 < len(candidate) <= LABEL_MAX_CHARS:
                    label = candidate
            elif not prefix and block.id > 0:
                prev = page.graph.blocks[block.id - 1]
                if len(prev.text) <= LABEL_MAX_CHARS and prev.section == block.section:
                    label = prev.text.rstrip(": ")
            break
        compiled = self._compile(scope, spec, page, taught, "taught", json_path=json_path, label=label)
        attempts.append({"stage": "taught", "page": page.stage, "outcome": "GROUNDED",
                         "recipe": compiled["kind"] if compiled else None})
        found = self._found(spec, taught, normalized, "taught", page,
                            {"grounded_in": "structured" if json_path else "html"}, compiled, attempts)
        return self._assess(scope, spec, page, found, "taught")

    def _assess(self, scope: str, spec: dict[str, Any], page: Page, found: dict[str, Any], origin: str,
                check: tuple[list[str], list[dict[str, Any]]] | None = None) -> dict[str, Any]:
        """Attach provenance and signals; enforce ``min_provenance``."""
        confirmed, disagreements = check if check is not None else independent_check(page, spec, found["value"])
        provenance = provenance_of(origin, confirmed)
        signals: list[dict[str, Any]] = []
        if disagreements:
            signals.append({"code": "STAGE_DISAGREEMENT", "disagreements": disagreements})
        url = page.info.get("final_url") or page.info.get("url") or ""
        if url and scope != "inline":
            streak = self.store.observe(scope, spec["name"], url, str(found["normalized"]))
            if streak >= REPEAT_THRESHOLD and not confirmed:
                signals.append({"code": "REPEATED_VALUE", "urls": streak,
                                "hint": "same unconfirmed value on several different pages; check the recipe anchor"})
        found["provenance"] = provenance
        found["confirmed_by"] = confirmed
        found["signals"] = signals
        minimum = spec.get("min_provenance")
        if minimum and PROVENANCE.index(provenance) < PROVENANCE.index(minimum):
            found["status"] = "UNCONFIRMED"
            found["required_provenance"] = minimum
        return found

    # -- result shapes -------------------------------------------------------
    @staticmethod
    def _found(spec, raw, normalized, stage, page: Page, evidence, recipe, attempts=None) -> dict[str, Any]:
        result = {
            "name": spec["name"],
            "type": spec["type"],
            "status": "FOUND",
            "value": normalize_text(raw),
            "normalized": normalized,
            "stage": stage,
            "page_stage": page.stage,
            "evidence": evidence,
            "recipe": recipe,
        }
        if attempts is not None:
            result["attempts"] = attempts
        return result

    @staticmethod
    def _status(spec, status, attempts, **extra) -> dict[str, Any]:
        return {"name": spec["name"], "type": spec["type"], "status": status, "value": None,
                "normalized": None, "attempts": attempts, **extra}


def extract(
    *,
    store: RecipeStore,
    scope: str,
    fields: list[Any],
    first_page: Page,
    render_page: Callable[[], Page] | None,
    render: str = "auto",
    learn: bool = True,
    max_candidates: int = 3,
) -> dict[str, Any]:
    if render not in RENDER_MODES:
        raise DragonFruitMeError("RENDER_MODE_INVALID", f"render must be one of {sorted(RENDER_MODES)}")
    if not fields:
        raise DragonFruitMeError("FIELDS_REQUIRED", "Pass at least one field to extract.")
    specs = [normalize_spec(f) for f in fields]
    names = [s["name"] for s in specs]
    if len(set(names)) != len(names):
        raise DragonFruitMeError("FIELD_DUPLICATE", "Field names must be unique.", details={"fields": names})
    extractor = Extractor(store)
    results: dict[str, dict[str, Any]] = {}
    attempts: dict[str, list[dict[str, Any]]] = {s["name"]: [] for s in specs}
    pages = [first_page]

    # One transaction per page and stage group: recipe lookups, hit/miss
    # counters, observations and newly compiled recipes commit together.
    # Rendering runs outside it so no store lock is held during network I/O.
    with store.transaction():
        for spec in specs:
            if spec.get("teach") not in (None, ""):
                results[spec["name"]] = extractor.teach(scope, spec, first_page, attempts[spec["name"]])
                continue
            found = extractor.run_cheap_stages(scope, spec, first_page, attempts[spec["name"]], learn)
            if found:
                results[spec["name"]] = found

    unresolved = [s for s in specs if s["name"] not in results]
    render_note = None
    if unresolved and render != "never" and render_page is not None:
        wants = render == "on_miss" or first_page.graph.js_shell_score() >= 0.6
        if wants:
            try:
                rendered = render_page()
            except DragonFruitMeError as exc:
                render_note = {"outcome": "UNAVAILABLE" if exc.code == "RENDER_NOT_AVAILABLE" else "FAILED",
                               "reason": exc.code}
            else:
                pages.append(rendered)
                render_note = {"outcome": "RENDERED"}
                with store.transaction():
                    for spec in unresolved:
                        attempts[spec["name"]].append({"stage": "render", "outcome": "RENDERED"})
                        found = extractor.run_cheap_stages(scope, spec, rendered, attempts[spec["name"]], learn)
                        if found:
                            results[spec["name"]] = found
        else:
            render_note = {"outcome": "SKIP", "reason": "PAGE_NOT_JS_SHELL"}
    elif unresolved and render_page is None:
        render_note = {"outcome": "SKIP", "reason": "NO_URL_OR_RENDERER" if render != "never" else "RENDER_NEVER"}
    elif unresolved:
        render_note = {"outcome": "SKIP", "reason": "RENDER_NEVER"}

    last_page = pages[-1]
    for spec in specs:
        name = spec["name"]
        if name in results:
            results[name].setdefault("attempts", attempts[name])
            continue
        if render_note:
            attempts[name].append({"stage": "render", **render_note})
        query = " ".join(spec["aliases"])
        evidence = locate(last_page.graph, query, max_results=max_candidates, max_chars_per_block=240, max_context_chars=1500)
        attempts[name].append({"stage": "agent", "outcome": "NEEDS_AGENT"})
        results[name] = Extractor._status(
            spec, "NEEDS_AGENT", attempts[name],
            candidates=evidence["hits"],
            next="read the candidates; if the value is there, call extract again with this field's 'teach' set to the exact value",
        )

    ordered = [results[s["name"]] for s in specs]
    found = sum(1 for r in ordered if r["status"] == "FOUND")
    status = "COMPLETE" if found == len(ordered) else "PARTIAL" if found else "INCOMPLETE"
    return {
        "ok": True,
        "status": status,
        "scope": scope,
        "fields": ordered,
        "pages": [p.info for p in pages],
        "found": found,
        "total": len(ordered),
    }


def page_from(fetch_result, html_graph: PageGraph | None = None) -> Page:
    graph = html_graph or build_graph(fetch_result.html, fetch_result.final_url or fetch_result.url)
    return Page(html=fetch_result.html, graph=graph, stage=fetch_result.stage, info=fetch_result.info())
