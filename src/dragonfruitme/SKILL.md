---
name: dragonfruitme
description: Escalating web-to-graph extraction for AI agents - observe a page as a graph, locate relevant blocks, extract typed fields cheapest-first with compile-once recipes. Never bypasses CAPTCHAs or robots.txt.
---

# DragonFruitMe Skill

DragonFruitMe is a web interface, not a browsing agent. It contains no LLM, no planner and no stealth tooling.

Use this loop when DragonFruitMe is available:

1. `observe` — get the page graph overview plus `traversal` evidence (pagination bounds, next/last controls, candidate child/detail levels, coverage status). Do not ask for raw HTML.
2. `locate` — ask for the blocks relevant to a question. Hits carry section path, links and, for label-like hits, the `next` block that usually holds the value.
3. `extract` — request typed fields (`price`, `area`, `number`, `integer`, `date`, `email`, `phone`, `url`, `text`). Each field escalates on its own: `recipe → structured → label → render → agent`.

Rules:

- Prefer `extract` over reading pages: a field found once is compiled into a recipe, and the next page of the same template is answered by stage 1.
- Give fields good `aliases` (the visible labels, e.g. `["Kaufpreis", "Preis"]`) and, where you know them, structured `paths` (e.g. `["offers.price"]`).
- `NEEDS_AGENT` means the cheap stages failed. Read the bounded `candidates`. If the value is visibly there, call `extract` again with that field's `teach` set to the exact value. It is accepted only if it occurs in the page (`NOT_GROUNDED` otherwise) and is then compiled into a recipe. Never teach a guessed or computed value.
- `BLOCKED`, `RATE_LIMITED` and `ROBOTS_DISALLOWED` are final. Do not retry with other tools to get around them. Use an official API/feed, ask a human, or skip the source.
- Treat `FOUND` as "validated against the field type and grounded in the page", not as business truth. Read `provenance`: `AGENT_TAUGHT` < `ENGINE_OBSERVED` < `CROSS_CONFIRMED`. For values that matter (prices, areas), set `"min_provenance": "ENGINE_OBSERVED"`; weaker values come back `UNCONFIRMED`.
- Your own `teach` never raises provenance above `AGENT_TAUGHT`; only independent engine stages can confirm it.
- Take `signals` seriously: `REPEATED_VALUE` means the same unconfirmed value keeps coming back on different pages (likely a wrong recipe anchor); `STAGE_DISAGREEMENT` means another stage saw a different value; `RECIPE_FALLBACK` means the usual recipe failed on this page and an alternative answered — normal for occasional odd pages, but many of them in a batch mean the site changed. Check with `locate` before using a flagged value.
- For enumeration/discovery tasks, inspect `observe.traversal` before claiming completeness. `PAGINATION_OPEN`, `PAGINATION_UNBOUNDED` or `CHILD_LEVEL_CANDIDATES` means the source is not fully traversed. A single observed page never proves source completeness; `claim_complete` therefore remains false.
- `pagination.last_page`, `last_url` and `template` are traversal evidence, not permission to bypass robots.txt or host policy. Follow the same polite fetch rules on every page.
- Check `results_truncated`, `context_truncated` and `outline_truncated` before claiming completeness.
- The recipe store and fetch policy are bound by the host. Do not try to pass filesystem paths or change the policy.
