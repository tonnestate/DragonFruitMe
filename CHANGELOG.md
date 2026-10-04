# Changelog

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

Initial DragonFruitMe foundation, modelled on BananaMe's repository and interface discipline.

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
