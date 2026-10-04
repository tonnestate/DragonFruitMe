# Changelog

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
