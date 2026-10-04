# Changelog

## 0.3.1 — 2026-10-05

Structural label resolution for modern component/CSS layouts.

- page-graph blocks now retain an internal bounded DOM-container ancestry built during the existing one-pass stdlib HTML parse;
- label extraction still prefers exact adjacent pairs and inline `Label: value`, but can now search a bounded eight-block neighbourhood when label and value sit in sibling wrappers with helper/decorative nodes between them;
- structural fallback requires the candidate to remain in the same region and section and to share the component container with the label, preventing cross-card value theft;
- typed validation remains authoritative, so a structurally nearby block cannot count unless it validates for the requested field type;
- no new public operation, MCP tool, crawler frontier, CSS selector engine or runtime dependency;
- regression tests cover nested DIV/CSS-style phone and e-mail layouts, cross-component isolation and unchanged classic `dt/dd` behavior.

## 0.3.0 — 2026-10-05

Traversal awareness: enumeration can no longer mistake one visible level for whole-source coverage.

- `observe` now returns a separate `traversal` object alongside the page summary; the public agent surface remains exactly `observe → locate → extract`;
- pagination detection from `rel=next` / `rel=last`, semantic controls (`>`, `>>`, `>>>`, `»»`, Weiter/Next), numeric links, page/offset query parameters and `/page/N` / `/seite/N` paths;
- when a terminal page is visible, returns `last_page` / `last_url`, `remaining_pages` and a deterministic URL `template`; terminal numeric gaps such as `1 2 3 … 88` are accepted as weaker `terminal-page-gap` evidence;
- repeated internal URL scopes are grouped into `detail_candidates` / `child_candidates`, exposing likely lower hierarchy levels such as listing → company detail → phone/e-mail;
- fail-closed coverage: `coverage_status` is `INCOMPLETE` when an open pagination/child edge is proven and otherwise `UNKNOWN`; a single observed page never yields `claim_complete=true`;
- link `rel` metadata is retained in the page graph for navigation semantics;
- agent Skill, MCP instructions, README and protocol now require traversal evidence to be checked before completeness claims in enumeration tasks;
- dedicated traversal tests cover explicit last controls, triple arrows, terminal numeric gaps, unbounded next links, path pagination, repeated detail scopes and the no-false-complete invariant.

## 0.2.1 — 2026-10-05

Throughput: about 11× faster per warm page, with unchanged assurance.

- recipe store keeps one SQLite connection per store (WAL journal, `synchronous=NORMAL`, thread-safe) instead of opening, committing and closing a connection per field and operation;
- one transaction per page and stage group (`BEGIN IMMEDIATE … COMMIT`); rendering runs outside the transaction so no lock is held during network I/O; a failed page rolls back cleanly;
- structured data (JSON-LD, embedded JSON, microdata, meta) is parsed and flattened once per page and shared by all fields and independent checks;
- `DragonFruitMe.close()` and context-manager support; connections also close on garbage collection;
- `scripts/benchmark.py`: offline benchmark (400 pages × 5 fields) — median 13.9 ms → 1.3 ms per warm page, 70 → ~750 pages/s;
- the independent check from 0.2.0 is deliberately kept on every hit; it costs ~10 % of the remaining time;
- store tests for WAL, persistence across instances, rollback, nested transactions, two stores on one file and thread safety; 61 tests.

## 0.2.0 — 2026-10-04

Extraction assurance: a found value now says who stands behind it, and a recipe hit is no longer trusted blindly.

- provenance per field: `AGENT_TAUGHT` < `ENGINE_OBSERVED` < `CROSS_CONFIRMED`, with `confirmed_by`; an agent cannot raise the provenance of its own taught value;
- optional `min_provenance` per field; weaker values return `UNCONFIRMED` (value visible, not counted as found);
- independent check of every found value against the structured and label stages on the same page;
- contradicted recipes: a label-derived regex/label recipe whose answer no stage confirms while an independent stage disagrees is discarded (`MISS`, reason `CONTRADICTED`, with `recipe_value` and `contradicted_by`), the field escalates and the recipe is relearned;
- signals per field: `STAGE_DISAGREEMENT` and `REPEATED_VALUE` (same unconfirmed value on ≥ 3 different consecutive URLs of a scope); confirmed constants and re-extraction of the same URL are never flagged;
- recipe store keeps a small per-scope/field observation record for repetition tracking (new `observations` table, created automatically);
- `extract-batch` summary adds `unconfirmed_fields` and `flagged_rows`;
- `scripts/scenario_matrix.py` drives the real engine through 7 pathologies and 6 false-positive controls and renders `docs/EVIDENCE.md`; the test suite fails if a scenario fails or the file is stale;
- Skill, README, architecture and protocol documentation updated; 57 tests.

## 0.1.2 — 2026-10-04

Batch robustness and recipe stability.

- `extract-batch --resume` terminates a torn last line before appending, so a row interrupted mid-write can no longer corrupt the next record;
- `--resume` now skips only URLs whose latest row is final (success or `recoverable: false`, e.g. `BLOCKED`, `ROBOTS_DISALLOWED`) and retries transient failures (`FETCH_FAILED`, HTTP 5xx); the latest row per URL wins;
- regex recipes now generalise every digit run inside markup (`data-id="1"` → `\d+`), not only runs of three or more digits; digits in visible label text stay literal. Previously short per-page ids broke recipe reuse;
- end-to-end batch test proving that the second URL of a template is served by stage 1;
- README table fix; `extract-batch` documented in `docs/START_HERE.md` and `docs/protocol.md`;
- documentation describes DragonFruitMe on its own, without references to related projects.

## 0.1.1 — 2026-10-04

Progress-preserving batch extraction for large explicit URL sets.

- new host-side CLI command `extract-batch`; the public agent surface remains `observe`, `locate`, `extract`;
- accepts newline-delimited URL files or JSON arrays and de-duplicates while preserving order;
- reuses one DragonFruitMe instance and one recipe store across the entire batch, so early pages teach recipes that later pages reuse;
- writes one JSONL record per URL and flushes immediately, preserving completed work if the process is interrupted;
- `--resume` appends to an existing JSONL file and skips URLs already durably written;
- per-URL failures do not stop the batch by default; `--fail-on-error` restores fail-fast workflow semantics;
- no crawler frontier, search engine, planner or new MCP tool was added.

## 0.1.0 — 2026-10-04

Initial DragonFruitMe foundation.

- public agent surface: `observe`, `locate`, `extract` (Python API, CLI, Agent Skill, optional MCP server on one core);
- fetch ladder: inline HTML → stdlib HTTP (gzip/deflate, charset detection) → optional Playwright render stage;
- host-bound FetchPolicy: honest User-Agent, robots.txt, per-host rate limit, size cap, page cache, deny-listed hosts;
- challenge detection that ends with `BLOCKED` / `RATE_LIMITED` instead of bypassing; CAPTCHA widgets inside normal content pages are not treated as blocks;
- page graph: heading-hierarchy sections, typed blocks, table rows, regions, emphasis, links, canonical, feeds, meta, JS-shell score;
- `locate`: BM25F-style ranking with on-page weights, section-heading propagation and label → value neighbours, bounded and truncation-flagged;
- escalation graph per field: recipe → structured data → label heuristics → render → agent;
- typed validation for price, area, number, integer, date, email, phone, url and text with German/English number formats;
- compile-once recipes (`json`, label-anchored `regex`, `label`) per host + URL template in SQLite with hit/miss counts, stale marking and self-healing;
- grounded agent teaching (`teach`): accepted only if the value occurs in the page, then compiled;
- full per-field evidence trail (`stage`, `page_stage`, `evidence`, `recipe`, `attempts`);
- 43 tests including a real local HTTP server for robots.txt, gzip, caching, challenge and render paths; zero mandatory runtime dependencies.
