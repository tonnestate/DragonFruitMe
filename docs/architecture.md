# Architecture and Specification (v0.1)

DragonFruitMe has one job: give an AI agent precise, cheap and provable access to the content of web pages. It is built from two graphs and a recipe store.

```text
                 ┌──────────── host-bound ────────────┐
                 │ FetchPolicy        RecipeStore     │
                 │ (robots, rate,     (SQLite, per    │
                 │  UA, deny hosts)   scope + field)  │
                 └────────┬──────────────────┬────────┘
                          │                  │
 agent ── observe ──▶ Fetch ladder ──▶ Page graph ──▶ summary
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
* **Block** = `id, kind, text, section path, region, weight, emphasis, row, heading_level`.
* **Block boundaries:** headings and block tags (`p li td th dt dd …`) open blocks; container tags (`div section table tr ul …`) flush them. Malformed HTML degrades into more, smaller blocks — never into lost text.
* **Regions:** `main`, `header`, `nav`, `aside`, `footer` (nesting-aware).
* **Weights (BM25F field weights):** H1 4.0 · H2 3.0 · H3 2.5 · H4–6 2.0 · body 1.0, multiplied by region (main 1.0, header 0.6, aside 0.5, nav/footer 0.3). Title weight 5.0 is applied as a page-level bonus. Emphasised tokens (`strong b em mark`) get a bonus.
* **JS-shell score** (0..1): thin text (+0.45), app root `#root/#app/#__next/#__nuxt` (+0.3), ≥5 scripts (+0.15), embedded JSON (−0.2), JSON-LD (−0.1). `needs_render` = score ≥ 0.6.

## 3. Locate (`locate.py`)

BM25 (k1 = 1.2, b = 0.75) per block, multiplied by the block weight, plus:

* emphasis bonus when a query term is emphasised in the block,
* section bonus when a query term occurs in the block's heading path (closer headings count more),
* title bonus for blocks matching terms that also occur in the page title,
* a coverage factor (share of query terms matched).

Label-like hits (short block) carry the `next` block in the same section — that is where the value usually is. Output is bounded by `max_results` and `max_context_chars` and flags `results_truncated` / `context_truncated`.

## 4. Escalation graph (`extract.py`)

Per field, in this order. A stage only counts when its candidate passes `validate()`.

| # | Stage | Candidate source | On hit |
|---|---|---|---|
| 1 | `recipe` | stored recipe for `(scope, field)` | count hit |
| 2 | `structured` | JSON-LD / embedded JSON / microdata / meta, by explicit `paths` first, then by alias = last key | compile `json` recipe |
| 3 | `label` | exact label blocks (dt→dd, th→td same row, label→next block) first, then inline `Label: value`; main region first | compile `regex` (label-anchored) or `label` recipe |
| 4 | `render` | re-run 1–3 on the rendered page | as above |
| 5 | `agent` | — | `NEEDS_AGENT` with ≤3 ranked candidates |

Render policy: `auto` renders only if a field is unresolved **and** the page is a JS shell; `on_miss` renders whenever a field is unresolved; `never` never renders. Missing browser support is recorded as an attempt (`UNAVAILABLE`), not raised.

**Teaching:** a field with `teach` skips stages 1–5 and is checked instead: it must validate against the type and occur in the page (structured value, raw HTML or visible text). Otherwise → `NOT_GROUNDED`. Grounded values are compiled like stage 2/3 hits.

## 5. Recipes (`recipes.py`)

* **Scope** = host (without `www.`) + up to four path segments, where segments with digits or long slugs become `*`. `/expose/123456` and `/expose/987` share a scope; `/angebote` has its own.
* **Kinds** in order of preference: `json` (structured path) › `regex` › `label`.
* **Regex derivation:** find the value in raw HTML (whitespace/entity-tolerant). First try an anchor that starts at the nearest preceding occurrence of the visible label (≤300 chars before). Otherwise try markup anchors of increasing length (16…160 chars), snapped to a tag start. Whitespace becomes `\s*`, digit runs ≥3 become `\d+` (ids change per page). The capture group is **typed** (`price`, `area`, …) or a text node (`[^<]+?` up to `<` or the literal delimiter that followed the value). A candidate is accepted only if its first match equals the value and all matches agree.
* **Lifecycle:** hit → `hits+1, misses=0, active`; miss → `misses+1`, `stale` after 2 misses; any later hit in stages 2–5 overwrites the recipe (self-healing).

## 6. Invariants

1. No stage may return a value that fails type validation.
2. No taught value is accepted unless it occurs in the page.
3. Every field result carries the full `attempts` path; nothing is reported as found without `stage` and `evidence`.
4. Blocks, robots disallows and rate limits are final for the call.
5. The agent can neither choose the filesystem location of the recipe store nor relax the fetch policy.
6. The core imports only the Python standard library.

## 7. Deferred (not in v0.1)

* CSS/XPath recipe kind and DOM-path anchors next to regex anchors.
* Multi-value fields (lists, tables → rows).
* Recipe confidence across many pages of a scope; A/B validation before overwriting an active recipe.
* Optional HTTP clients (HTTP/2) as an extra stage between `http` and `render`.
* Off-page SEO providers (PageSpeed Insights, CrUX, Search Console, OpenRush) as optional plugins.
