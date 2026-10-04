# DragonFruitMe

<p align="center">
  <img src="docs/dragonfruitme-banner.png" alt="DragonFruitMe — escalating web-to-graph extraction for AI agents" width="100%">
</p>

<p align="center">
  <strong>Escalating web-to-graph extraction for AI agents.</strong><br>
  Cheapest first. Compile once. Never bypass.
</p>

<p align="center">
  <img alt="License" src="https://img.shields.io/badge/license-GPL--3.0-blue">
  <img alt="Status" src="https://img.shields.io/badge/status-experimental-orange">
  <img alt="Version" src="https://img.shields.io/badge/version-0.3.0-ff2d8a">
  <img alt="Python" src="https://img.shields.io/badge/python-%3E%3D3.10-3776AB">
  <img alt="Dependencies" src="https://img.shields.io/badge/runtime%20deps-none-brightgreen">
  <img alt="MCP" src="https://img.shields.io/badge/MCP-optional-5b5bd6">
  <img alt="Agent Skill" src="https://img.shields.io/badge/agent-skill-purple">
  <img alt="LLM" src="https://img.shields.io/badge/built--in%20LLM-none-black">
</p>

---

## What DragonFruitMe is

DragonFruitMe is a **headless web interface for AI agents**. It sits between an agent and a web page and turns "read this page and find X" into a small, deterministic machine interface.

Most agent web tools pick one cost level and stay there: either a plain request that fails on harder pages, or a full browser plus an LLM for every page, every time. In practice a plain request plus a precise pattern is enough for the vast majority of pages; a headless browser is needed only rarely. DragonFruitMe makes that observation the architecture:

```text
OBSERVE  →  LOCATE  →  EXTRACT
```

* **observe** turns a page into a bounded **page graph** and adds **traversal evidence**: pagination bounds, next/last controls, repeated child/detail link scopes and conservative coverage status.
* **locate** answers "where on this page is …?" with the smallest relevant sub-graph, ranked by **on-page relevance signals** (title, heading level, emphasis, anchor text, page region).
* **extract** pulls typed fields through an **escalation graph** — per field, cheapest stage first — and **compiles** every costly hit into a deterministic recipe, so the next page of the same template is answered by a single regex.

DragonFruitMe contains no LLM, no planner, no stealth tooling and no CAPTCHA solver. The agent reasons; DragonFruitMe observes, ranks, extracts and grounds.

> **Agents decide. DragonFruitMe fetches politely, extracts cheaply and proves where every value came from.**

---

## The three graphs

### 1. Page graph (website → graph)

```text
page ──contains──▶ section (H1 › H2 › H3 …) ──contains──▶ block (p, li, dt/dd, td …)
                                                             │
                                                             ├─ links_to ──▶ url (anchor text, internal/external, nofollow)
                                                             └─ weight = heading level × region × emphasis
```

Agents get blocks with their section path instead of raw HTML. Weights come from classic on-page SEO signals, used here not as a report but as a **relevance function**: a term in an H2 or in `<strong>` matters more than the same term in the footer.

### 2. Escalation graph (per field, not per page)

```text
field "kaufpreis" (type: price)

 1 recipe       stored regex / JSON path / label for this URL template      µs
 2 structured   JSON-LD, embedded JSON, microdata, meta / Open Graph
 3 label        visible label → value (dt/dd, th/td, "Kaufpreis: 349.000 €")
 4 render       optional headless rendering — only for JS shells or on request
 5 agent        NEEDS_AGENT + bounded candidates → agent may *teach* a value
        │
        └── every hit in 2–5 is validated, then compiled back into stage 1
```

Each stage must **validate** its candidate against the field type (`price`, `area`, `number`, `integer`, `date`, `email`, `phone`, `url`, `text`, plus optional `min`, `max`, `pattern`). A stage that finds something that is not a valid price does not count, and the field escalates.

### 3. Traversal graph (page → open coverage edges)

For enumeration tasks, finding records on one page is not proof that the source is exhausted. `observe` therefore returns a separate `traversal` object:

```text
listing page
   ├── pagination ──▶ next page ──▶ … ──▶ last page
   └── child scope ─▶ detail page ──▶ phone / e-mail / deeper data
```

DragonFruitMe recognizes `rel=next` / `rel=last`, numeric page links, common page/offset URL parameters, `/page/42` / `/seite/42`, and navigation controls such as `>`, `>>`, `>>>`, `»»`. When the UI exposes a terminal page, it returns `last_page`, `last_url`, `remaining_pages` and a deterministic URL `template` such as `?page={page}`.

Repeated internal URL scopes are grouped as candidate child/detail levels. Three links such as `/studio/1001`, `/studio/1002`, `/studio/1003` therefore become one `detail_candidates` collection instead of being treated as unrelated links.

The coverage rule is intentionally fail-closed:

> **One observed page may prove that traversal is incomplete, but it can never by itself prove that a whole source is complete.**

`coverage_status` is therefore `INCOMPLETE` when pagination or lower-level candidates are visible, otherwise `UNKNOWN`. `claim_complete` remains false. DragonFruitMe exposes the frontier; it still does not autonomously crawl it.

**Compile once.** When stage 2–5 finds a field, DragonFruitMe derives the cheapest stable recipe and stores it per *scope* (host + URL template, e.g. `example.de/expose/*`):

| Recipe | Derived from | Example |
|---|---|---|
| `json` | structured data hit | `jsonld\|offers.price` |
| `regex` | label/taught hit, anchored at the **visible label** | `Kaufpreis</dt><dd\s*class="price"\s*data\-id="\d+">\s*(<typed price>)` |
| `label` | label text when no stable regex exists | `Kaufpreis` |

On the next page of the same template, stage 1 answers in microseconds. If the site is redesigned, the recipe misses, the field escalates, a new recipe is compiled — **self-healing without an LLM in the loop**.

**Teach, but grounded.** If every cheap stage fails, the agent gets `NEEDS_AGENT` with a few ranked candidate blocks. If it can read the value there, it calls `extract` again with `"teach": "<exact value>"`. DragonFruitMe accepts the value only if it **occurs in the page** (`NOT_GROUNDED` otherwise), then compiles it like any other hit. The LLM is the last resort *and* a one-time teacher, never the per-page extractor.

### Trust: who stands behind a value

A found value is only as good as what confirms it. Every field therefore carries a **provenance**:

```text
AGENT_TAUGHT  <  ENGINE_OBSERVED  <  CROSS_CONFIRMED
```

* `AGENT_TAUGHT` — the agent supplied the value (grounded in the page, but no engine stage found it on its own).
* `ENGINE_OBSERVED` — the structured or label stage finds it independently.
* `CROSS_CONFIRMED` — structured data *and* the visible label agree.

An agent can never raise the provenance of its own claim; only independent stages can. A field can demand a minimum, e.g. `"min_provenance": "ENGINE_OBSERVED"` for prices. Below it, the field comes back `UNCONFIRMED`: the value is visible, but it does not count as found.

**A recipe hit is not automatically right.** Every recipe answer is checked against the structured and label stages on the same page:

* If an independent stage has its own answer and none agrees, a label-derived recipe is **contradicted**: the hit is discarded (`MISS`, reason `CONTRADICTED`), the field escalates and the recipe is relearned.
* If the same *unconfirmed* value comes back on three or more different URLs of one template, the field carries a `REPEATED_VALUE` signal — the classic sign of a recipe anchored on a teaser, sidebar or default. Activity is not progress. A legitimately constant value, such as the agency e-mail on every exposé, is confirmed by its label each time and is never flagged.

`docs/EVIDENCE.md` is generated by `scripts/scenario_matrix.py` and re-checked by the tests: it drives the real engine through extraction pathologies **and** false-positive controls, because a detector that fires on healthy pages is itself a failure mode.

---

## What DragonFruitMe v0.3.0 can do today

| Capability | v0.3.0 behavior |
|---|---|
| Fetch ladder | Inline HTML → stdlib HTTP (gzip/deflate, charset detection) → optional Playwright rendering. |
| Polite by default | Honest User-Agent, robots.txt honoured, per-host minimum interval, response size cap, page cache (observe + locate + extract = one request). |
| Challenge handling | CAPTCHA/bot challenges and HTTP 429 end with `BLOCKED` / `RATE_LIMITED` and a `next` hint (human-in-the-loop, official API/feed). A CAPTCHA widget inside a normal content page is *not* treated as a block. |
| Page graph | Sections from heading hierarchy, block kinds, table rows, regions (main/nav/header/aside/footer), emphasis, links with anchor text, canonical, feeds, meta, images missing `alt`. |
| Traversal / coverage | `observe.traversal` detects pagination, last-page bounds and URL templates plus repeated child/detail link scopes. Signals: `PAGINATION_OPEN`, `PAGINATION_UNBOUNDED`, `CHILD_LEVEL_CANDIDATES`. A single page never yields a completeness claim. |
| JS-shell detection | `js_shell_score` / `needs_render` from text volume, app roots, script count and presence of embedded data. |
| Relevance search | BM25F-style ranking with heading/region/emphasis weights, section-heading propagation and label → value neighbours. Bounded and truncation-flagged. |
| Structured data | JSON-LD (incl. `@graph`), `application/json` / `__NEXT_DATA__`, microdata `itemprop`, meta and Open Graph — matched by alias or explicit dotted path. |
| Label heuristics | `dt/dd`, `th/td` in the same row, adjacent label/value blocks, inline `Label: value`, umlaut-tolerant (`Wohnflaeche` = `Wohnfläche`). |
| Typed validation | German and English number formats (`349.000,50 €`, `1,200.50`), areas (`m²`, `qm`), dates, e-mails, phones, URLs, bounds and custom patterns. |
| Compile-once recipes | JSON-path, label-anchored regex or label recipes per host + URL template in SQLite; hit/miss counters, stale marking, self-healing re-compilation. |
| Throughput | One SQLite connection per store (WAL, `synchronous=NORMAL`), one transaction per page, structured data parsed once per page. `scripts/benchmark.py`: ~1.3 ms per warm page with 5 fields (~750 pages/s offline, before v0.2.1: ~14 ms). |
| Agent teaching | `teach` values must be grounded in the page; then compiled into recipes. |
| Provenance | `AGENT_TAUGHT` < `ENGINE_OBSERVED` < `CROSS_CONFIRMED` per field, with `confirmed_by`; optional `min_provenance` turns weaker values into `UNCONFIRMED`. |
| Recipe assurance | Recipe hits are checked against independent stages; contradicted recipes are discarded and relearned; unconfirmed values repeating across different URLs raise `REPEATED_VALUE`. |
| Scenario evidence | `docs/EVIDENCE.md`: 7 pathologies and 6 false-positive controls driven through the real engine, regenerated and re-checked by the tests. |
| Evidence trail | Every field returns `stage`, `page_stage`, `evidence`, `recipe`, `provenance`, `confirmed_by`, `signals` and the full `attempts` path through the escalation graph. |
| Interfaces | Python API, CLI, Agent Skill and optional MCP server — all on the same core. State directory and fetch policy are **host-bound**, never agent-supplied. |
| Batch extraction | Host-side `extract-batch` processes explicit URL sets with one warm recipe store, immediate JSONL persistence, de-duplication and progress-preserving per-URL failure handling. `--resume` repairs a torn last line, skips final rows and retries transient failures. The summary counts `unconfirmed_fields` and `flagged_rows`. |

The core has **no mandatory third-party runtime dependencies**. MCP and the browser stage are optional extras.

### Repository/layout invariant

DragonFruitMe keeps its own release repository shallow: no ordinary project file deeper than two directories (`.github/workflows/` is the exception). The package itself has no sub-packages.

---

## Quick start

```bash
pip install -e '.[dev]'          # core + tests
pip install -e '.[mcp]'          # optional MCP server
pip install -e '.[browser]'      # optional render stage
playwright install chromium      # only if you use the render stage
```

### CLI

```bash
dragonfruitme observe --url https://example.org/

dragonfruitme locate --url https://example.org/expose/4711 --query "Kaufpreis Provision"

dragonfruitme extract --url https://example.org/expose/4711 --fields-json '[
  {"name": "kaufpreis",   "type": "price",   "aliases": ["Kaufpreis", "Preis"], "paths": ["offers.price"]},
  {"name": "wohnflaeche", "type": "area",    "aliases": ["Wohnfläche"]},
  {"name": "baujahr",     "type": "integer", "min": 1800, "max": 2030}
]'

dragonfruitme recipes                       # what has been compiled
dragonfruitme forget --scope example.org/expose/*
```

`--html-file page.html --base-url https://…` works everywhere instead of `--url` when the HTML is already in hand.


### Large explicit URL sets

For thousands of already-discovered URLs, do not make the agent write its own loop, temporary CSV shards or merge script. Use the host-side batch runner:

```bash
dragonfruitme extract-batch \
  --urls-file urls.txt \
  --fields-json '[{"name":"name","type":"text"},{"name":"phone","type":"phone"},{"name":"email","type":"email"}]' \
  --output results.jsonl \
  --resume
```

The input is either one URL per line or a JSON array. DragonFruitMe keeps one recipe store warm across the run, writes and flushes one JSONL record after every URL, and continues after per-URL failures. On `--resume` it repairs a row torn by an interruption, skips URLs whose latest row is final (success, or a non-recoverable error such as `BLOCKED`) and retries transient failures such as `FETCH_FAILED` or HTTP 5xx. Retried URLs get a new row; consumers take the last row per URL. This is deliberately **not** a crawler frontier: DragonFruitMe processes the explicit URL set it is given and does not invent discovery strategy.

### Python

```python
from dragonfruitme import DragonFruitMe

dfm = DragonFruitMe()
result = dfm.extract(
    url="https://example.org/expose/4711",
    fields=[{"name": "kaufpreis", "type": "price", "aliases": ["Kaufpreis"]}],
)
field = result["fields"][0]
field["status"], field["normalized"], field["stage"]   # ('FOUND', 349000.0, 'label')
```

### Result shape (one field)

```json
{
  "name": "kaufpreis",
  "type": "price",
  "status": "FOUND",
  "value": "349.000 €",
  "normalized": 349000.0,
  "stage": "label",
  "page_stage": "http",
  "evidence": {"label": "Kaufpreis", "block_id": 4},
  "recipe": {"kind": "regex", "pattern": "Kaufpreis</dt><dd…", "scope": "example.org/expose/*", "compiled": true},
  "provenance": "ENGINE_OBSERVED",
  "confirmed_by": ["label"],
  "signals": [],
  "attempts": [
    {"stage": "recipe", "page": "http", "outcome": "SKIP", "reason": "NO_RECIPE"},
    {"stage": "structured", "page": "http", "outcome": "MISS", "reason": "NO_CANDIDATE", "candidates_checked": 0},
    {"stage": "label", "page": "http", "outcome": "HIT", "label": "Kaufpreis"}
  ]
}
```

Field status is `FOUND`, `UNCONFIRMED`, `NEEDS_AGENT` or `NOT_GROUNDED`; the call status is `COMPLETE`, `PARTIAL` or `INCOMPLETE`. Page-level failures (`BLOCKED`, `RATE_LIMITED`, `ROBOTS_DISALLOWED`, `HTTP_ERROR`, …) come back as `{"ok": false, "error": {...}}`.

---

## MCP

```bash
DRAGONFRUITME_STATE_DIR=~/.dragonfruitme dragonfruitme-mcp
```

Tools: `observe`, `locate`, `extract`. The host configures everything else through the environment:

| Variable | Meaning |
|---|---|
| `DRAGONFRUITME_STATE_DIR` | recipe store (default `~/.dragonfruitme`) |
| `DRAGONFRUITME_USER_AGENT` | override the honest default User-Agent |
| `DRAGONFRUITME_RESPECT_ROBOTS` | `false` only if the host has the right to ignore robots.txt |
| `DRAGONFRUITME_MIN_INTERVAL` | seconds between requests to the same host (default 1.0) |
| `DRAGONFRUITME_TIMEOUT` | request timeout in seconds |
| `DRAGONFRUITME_DENY_HOSTS` | comma-separated hosts the agent may never fetch |
| `DRAGONFRUITME_MCP_TRANSPORT` | `stdio` (default), `streamable-http` or `sse` |

## Agent Skill

The Skill lives at `src/dragonfruitme/SKILL.md` (packaged) with byte-identical mirrors at `skill/SKILL.md` and `skills/dragonfruitme/SKILL.md`. It tells an agent to prefer `extract` over reading pages, to give good aliases, to teach only grounded values and to treat `BLOCKED` as final.

---

## What DragonFruitMe deliberately does not do

* **No CAPTCHA solving, no stealth, no fingerprint spoofing, no proxy rotation.** A challenge is the site owner saying "no bots". DragonFruitMe stops, says so and points to the honest alternatives. This keeps the project publishable and usable inside companies.
* **No SEO crawler.** On-page signals are used for relevance ranking, not for reports. Off-page metrics belong to dedicated providers.
* **No LLM inside.** Extraction is deterministic; the agent is the teacher of last resort.
* **No autonomous crawling frontier.** DragonFruitMe detects pagination and candidate hierarchy levels, but following them remains the agent/host decision under the same fetch policy.

## Donors and prior art

See [`docs/donor-map.md`](docs/donor-map.md) and [`docs/research-basis.md`](docs/research-basis.md). In short: Crawlee's adaptive HTTP/browser switching, Scrapling's layered fetchers, Agent Reach's ordered fallback channels, ScrapeGraphAI's graph pipelines, AutoScraper and decades of wrapper-induction research. DragonFruitMe's delta is the combination: **per-field escalation + compile-once, label-anchored recipes + an SEO-weighted page graph as the agent interface + grounded teaching**, in a zero-dependency core.

## Documentation

* [`docs/START_HERE.md`](docs/START_HERE.md) — first workflow
* [`docs/architecture.md`](docs/architecture.md) — specification of both graphs, stages and invariants
* [`docs/protocol.md`](docs/protocol.md) — machine interface and error codes
* [`docs/EVIDENCE.md`](docs/EVIDENCE.md) — generated scenario matrix (pathologies and controls)
* [`docs/donor-map.md`](docs/donor-map.md) · [`docs/research-basis.md`](docs/research-basis.md)
* [`SECURITY.md`](SECURITY.md) · [`CONTRIBUTING.md`](CONTRIBUTING.md) · [`CHANGELOG.md`](CHANGELOG.md)

## Status

v0.3.0 is experimental. The public agent surface (`observe → locate → extract`) remains intentionally small; `extract-batch` is a host-side throughput path for explicit URL sets. Recipe derivation, ranking and the render stage will evolve. Licensed under GPL-3.0-only.
