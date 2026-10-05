# Architecture and Specification (v0.4.2)

DragonFruitMe has one job: give an AI agent precise, cheap and provable access to the content of web pages. It is built from three graphs and a recipe store. This specification reflects the v0.4.2 system, including traversal coverage evidence, the bounded recipe portfolio, promotion/demotion lifecycle, constant-time live-recipe dispatch and non-blocking batch sanity feedback.

```text
                 ┌──────────── host-bound ────────────┐
                 │ FetchPolicy        RecipeStore     │
                 │ (robots, rate,     (SQLite, per    │
                 │  UA, deny hosts)   scope + field)  │
                 └────────┬──────────────────┬────────┘
                          │                  │
 agent ── observe ──▶ Fetch ladder ──▶ Page graph ──▶ summary + traversal graph
       ── locate  ──▶ Fetch ladder ──▶ Page graph ──▶ ranked sub-graph
       ── extract ──▶ Fetch ladder ──▶ Page graph ──▶ Escalation graph ──▶ fields + evidence
                                                          │
                                                          └── compile ──▶ RecipeStore
```

## 1. Fetch ladder (`fetch.py`, `policy.py`)

| Stage | When | Notes |
|---|---|---|
| `inline` | caller passes `html` | no network; `base_url` sets link resolution and recipe scope |
| `http` | default for `url` | stdlib `urllib`; gzip/deflate; charset from header → `<meta>` → UTF-8 → cp1252 |
| `render` | only when the escalation graph asks for it | optional Playwright extra; same policy, same challenge detection |

Policy invariants (host-bound, never agent-supplied):

1. Only `http`/`https`; deny-listed hosts fail with `HOST_DENIED`.
2. robots.txt is honoured by default (401/403 on robots.txt = disallow all; 404/5xx = no rules; unreachable = allow).
3. One request per host per `min_interval_seconds` (default 1 s).
4. Responses are capped at `max_bytes` (default 5 MB); truncation is reported.
5. Pages are cached for `cache_ttl_seconds` (default 300 s) so `observe → locate → extract` costs one request.
6. **Challenge = stop.** HTTP 429, or a challenge marker on a challenge status/thin page, raises `BLOCKED` with `reason` and `next`. There is no retry with another strategy.

## 2. Page graph (`graph.py`)

Built with `html.parser` in one pass.

* **Nodes:** page (title, lang, meta, canonical, feeds) · sections (heading hierarchy) · blocks · links.
* **Block** = `id, kind, text, section path, region, weight, emphasis, row, heading_level` plus an internal bounded container ancestry used only for structural neighbourhood checks.
* **Block boundaries:** headings and block tags (`p li td th dt dd …`) open blocks; container tags (`div section table tr ul …`) flush them. Malformed HTML degrades into more, smaller blocks — never into lost text.
* **Regions:** `main`, `header`, `nav`, `aside`, `footer` (nesting-aware).
* **Weights (BM25F field weights):** H1 4.0 · H2 3.0 · H3 2.5 · H4–6 2.0 · body 1.0, multiplied by region (main 1.0, header 0.6, aside 0.5, nav/footer 0.3). Title weight 5.0 is applied as a page-level bonus. Emphasised tokens (`strong b em mark`) get a bonus.
* **JS-shell score** (0..1): thin text (+0.45), app root `#root/#app/#__next/#__nuxt` (+0.3), ≥5 scripts (+0.15), embedded JSON (−0.2), JSON-LD (−0.1). `needs_render` = score ≥ 0.6.

## 3. Traversal and coverage (`traversal.py`)

`observe` analyzes the already-built page graph for navigation structure. It does **not** follow links itself.

Pagination evidence, strongest first:

1. explicit link relations such as `rel=last` / `rel=next`;
2. semantic controls such as `Weiter`, `>`, `>>`, `>>>`, `»»`;
3. page/offset markers in query parameters and paths (`?page=42`, `?offset=100`, `/page/42`, `/seite/42`);
4. a terminal numeric gap such as `1 2 3 … 88`, recorded as weaker `terminal-page-gap` evidence.

When a bound is visible, the result carries `last_page` or `last_offset`, `last_url`, `remaining_pages` and a deterministic URL `template`. Pagination URLs are excluded from child-level inference.

Hierarchy inference groups distinct internal URLs by recipe scope. Repeated scopes such as `/studio/1001`, `/studio/1002` or repeated `/profile?id=...` links become `detail_candidates` / `child_candidates`. This is intentionally evidence, not an automatic crawl plan.

Coverage invariant:

> A single observed page can prove an open traversal edge, but can never prove whole-source completeness.

Therefore `coverage_status` is `INCOMPLETE` when pagination or candidate child levels are open and `UNKNOWN` otherwise. `claim_complete` is always false. This prevents a level-0 listing from being mistaken for complete coverage when phone numbers, e-mail addresses or other fields live one level deeper.

## 4. Locate (`locate.py`)

BM25 (k1 = 1.2, b = 0.75) per block, multiplied by the block weight, plus:

* emphasis bonus when a query term is emphasised in the block,
* section bonus when a query term occurs in the block's heading path (closer headings count more),
* title bonus for blocks matching terms that also occur in the page title,
* a coverage factor (share of query terms matched).

Label-like hits (short block) carry the `next` block in the same section — that is where the value usually is. Output is bounded by `max_results` and `max_context_chars` and flags `results_truncated` / `context_truncated`.

## 5. Escalation graph (`extract.py`)

Per field, in this order. A stage only counts when its candidate passes `validate()`.

| # | Stage | Candidate source | On hit |
|---|---|---|---|
| 1 | `recipe` | stored recipe for `(scope, field)` | count hit |
| 2 | `structured` | JSON-LD / embedded JSON / microdata / meta, by explicit `paths` first, then by alias = last key | compile `json` recipe |
| 3 | `label` | exact label blocks (dt→dd, th→td same row, label→next block) first; then bounded structural sibling-wrapper neighbours inside the same component; then inline `Label: value`; main region first | compile `regex` (label-anchored) or `label` recipe |
| 4 | `render` | re-run 1–3 on the rendered page | as above |
| 5 | `agent` | — | `NEEDS_AGENT` with ≤3 ranked candidates |

**Defining backward edge (public since 2026-10-04):** the escalation graph is not only the forward sequence `1 → 2 → 3 → 4 → 5`. A validated hit from stages 2–5 is compiled back into stage 1 for the same scope and field. That backward compilation edge is what turns escalation into learning: expensive resolution is paid once, then reused deterministically. In v0.4.x the compiled result joins the bounded recipe portfolio instead of blindly overwriting a proven primary.

**Structural label fallback:** modern component layouts often place a short label and its value in sibling `div` wrappers with icon/help nodes between them. DragonFruitMe keeps the normal adjacent rule first, then inspects at most eight following blocks. A candidate must remain in the same section and region and share the label's immediate component ancestry; typed validation still decides whether it can count. This is deterministic neighbourhood reasoning, not CSS/XPath execution.

Render policy: `auto` renders only if a field is unresolved **and** the page is a JS shell; `on_miss` renders whenever a field is unresolved; `never` never renders. Missing browser support is recorded as an attempt (`UNAVAILABLE`), not raised.

**Teaching:** a field with `teach` skips stages 1–5 and is checked instead: it must validate against the type and occur in the page (structured value, raw HTML or visible text). Otherwise → `NOT_GROUNDED`. Grounded values are compiled like stage 2/3 hits.

## 6. Recipes (`recipes.py`)

* **Scope** = host (without `www.`) + up to four path segments, where segments with digits or long slugs become `*`. `/expose/123456` and `/expose/987` share a scope; `/angebote` has its own.
* **Kinds** in order of preference: `json` (structured path) › `regex` › `label`.
* **Regex derivation:** find the value in raw HTML (whitespace/entity-tolerant). First try an anchor that starts at the nearest preceding occurrence of the visible label (≤300 chars before). Otherwise try markup anchors of increasing length (16…160 chars), snapped to a tag start. Whitespace becomes `\s*`, digit runs ≥3 become `\d+` (ids change per page). The capture group is **typed** (`price`, `area`, …) or a text node (`[^<]+?` up to `<` or the literal delimiter that followed the value). A candidate is accepted only if its first match equals the value and all matches agree.
* **Storage:** one SQLite connection per store (WAL journal, `synchronous=NORMAL`, thread-safe via a re-entrant lock). The extractor wraps each page's cheap stages in one `BEGIN IMMEDIATE … COMMIT` transaction (a second one after rendering); rendering itself runs outside any transaction, so no store lock is held during network I/O. A crash loses at most the counters and recipes of the page in progress. Several processes may share one state directory.
* **Portfolio (v0.4.0):** per `(scope, field)` at most `MAX_LIVE` = 4 live recipes with a role: one `primary`, `fallback` (an alternative that has answered when needed) and `candidate` (newly compiled, unproven). Beyond the cap the weakest alternative becomes `retired` (kept for audit, never executed). Every recipe tracks `hits`, consecutive `misses`, `total_misses`, `contradictions`, `confirmed_hits` (hits an independent stage confirmed) and `last_validated`.
* **Execution:** the primary runs alone. Only after it misses or is contradicted on this page are the alternatives tried, ordered by a Laplace-smoothed score `(hits+1)/(hits+total_misses+2·contradictions+2)`; the recipe kind (json › regex › label) only breaks ties. A healthy primary therefore costs the same as a single recipe. Every alternative answer carries a `RECIPE_FALLBACK` signal (`role`, `kind`, `promoted`).
* **Lifecycle:** a compiled recipe becomes the primary only if the field has none; otherwise it joins as a `candidate` and the compiling page counts as its first hit. Hit → `hits+1, misses=0`; miss → `misses+1`, `stale` after 2 in a row. An alternative is **promoted** when the primary is stale on this page and the alternative has at least `PROMOTE_MIN_HITS` = 2 hits; the old primary is demoted to `fallback`, not deleted, so a returning layout is answered immediately. A candidate that answers without promotion becomes a `fallback`. Legacy single-recipe stores (schema version 0) are migrated in place: each recipe becomes its field's primary.
* **Dispatch index (v0.4.1):** recipes have a stable `id`; `scope`, `field` and `role` are mutable, indexed attributes. Live lookups use `recipe_set_bucket (scope, field, role)` with an explicit `role IN (…)` list, so they touch only the ≤ 4 live recipes of one bucket — independent of how many recipes the store holds and of how many were retired in that bucket. Promotion, demotion and retirement update the role in place inside the page transaction, so the index is always consistent. Retired history is capped at `RETIRED_KEEP` = 16 per bucket (oldest deleted). Invariant: a primary miss tries at most `MAX_LIVE − 1` alternatives; there is no global candidate scan. `scripts/benchmark.py --scaling` verifies flat healthy-primary latency from 1 to 100,000 stored recipes.
* **No shadow execution:** candidates are not evaluated in parallel with a healthy primary. They prove themselves exactly when they are needed, which keeps the hot path flat.

## 7. Assurance: provenance, contradiction, repetition

After a stage finds a value, the structured and label stages are asked **independently** about it on the same page:

* `confirmed_by` = engine stages with at least one valid candidate equal to the value;
* a stage *disagrees* when it has a valid answer of its own and none of its candidates equals the value.

**Provenance** (lowest to highest): `AGENT_TAUGHT` (origin taught, no engine confirmation) < `ENGINE_OBSERVED` (one engine stage confirms, or an engine-derived recipe hit without contradiction) < `CROSS_CONFIRMED` (structured and label both confirm). An agent cannot raise the provenance of its own claim. `min_provenance` turns lower values into `UNCONFIRMED` (value shown, not counted as found).

**Contradiction:** a `regex`/`label` recipe derived from the label or structured stage is discarded when nothing confirms it and an independent stage disagrees. The attempt is recorded as `MISS` with reason `CONTRADICTED`, `recipe_value` and `contradicted_by`; the field escalates and the recipe is relearned. Disagreement that does not discard a hit is reported as a `STAGE_DISAGREEMENT` signal.

**Repetition:** the store remembers, per scope and field, the last URL, the last value and how many *different* consecutive URLs produced that value. From `REPEAT_THRESHOLD` = 3 URLs on, an **unconfirmed** value carries a `REPEATED_VALUE` signal. Re-extracting the same URL does not count, and confirmed constants (an agency e-mail with its label) are never flagged.

**Evidence:** `scripts/scenario_matrix.py` drives the engine through 9 pathologies and 7 false-positive controls and renders `docs/EVIDENCE.md`; the test suite fails if a scenario fails or the file is stale. Disabling the independent check makes one pathology and two controls fail; restoring the pre-0.4 "new recipe overwrites the primary" behaviour makes P8, P9 and C7 fail — the matrix measures the mechanisms rather than restating them.

## 8. Batch sanity feedback (host-side)

`extract-batch` already aggregates observed errors, field statuses, extraction stages and field signals. v0.4.2 adds a deliberately small advisory layer over those measurements. It is not an expert system and has no domain knowledge.

The layer uses fixed, inspectable guards: at least 10 attempted rows before low-yield feedback, at least 10 field observations before agent-escalation feedback, ≤10% usable rows for `LOW_BATCH_YIELD`, ≥80% `NEEDS_AGENT` for `HIGH_AGENT_ESCALATION`, and zero `FOUND` across at least 10 observations for a `SYSTEMATIC_FIELD_GAPS` entry. Field-gap evidence is capped at five examples.

An advisory is evidence for the caller to reconsider or confirm its plan, never authority to change that plan. It does not stop the batch, does not mutate the requested fields, does not invent a discovery strategy and does not affect the exit code. This preserves the universal-tool boundary: DragonFruitMe can say "this outcome is unusual" without claiming "this postal code, industry, source or task is wrong."

## 9. Invariants

1. No stage may return a value that fails type validation.
2. No taught value is accepted unless it occurs in the page.
3. Every field result carries the full `attempts` path; nothing is reported as found without `stage`, `evidence` and `provenance`.
4. A recipe hit that an independent stage contradicts is never returned as found.
5. Blocks, robots disallows and rate limits are final for the call.
6. The agent can neither choose the filesystem location of the recipe store nor relax the fetch policy.
7. The core imports only the Python standard library.
8. One observed page never authorizes a whole-source completeness claim; visible pagination or repeated child scopes remain explicit traversal evidence.

## 10. Deferred

* CSS/XPath recipe kind and DOM-path anchors next to regex anchors.
* Multi-value fields (lists, tables → rows).
* JSON Schemas for field specs and results with contract tests.
* Optional HTTP clients (HTTP/2) as an extra stage between `http` and `render`.
* Off-page SEO providers (PageSpeed Insights, CrUX, Search Console, OpenRush) as optional plugins.
