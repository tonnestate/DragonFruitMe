# Donor Map

DragonFruitMe is a reuse-first composition, not a fork. Concepts are borrowed; no third-party source code is vendored in v0.1.0.

| Area | Donor / prior art | DragonFruitMe delta |
|---|---|---|
| HTTP ↔ browser switching | Crawlee `AdaptivePlaywrightCrawler` (rendering-type prediction, result checker) | escalation per **field**, not per request; validation by field type; render only for JS shells |
| Layered fetchers + MCP | Scrapling (`Fetcher` / `DynamicFetcher` / `StealthyFetcher`, MCP mode) | automatic routing instead of agent-chosen tier; no stealth tier |
| Ordered fallback channels | Agent Reach (per-platform backends, health checks) | fallback inside one page/field, with evidence trail; could serve as Agent Reach's "web (any URL)" backend |
| Graph pipelines | ScrapeGraphAI (fetch → parse → LLM nodes) | LLM-last instead of LLM-per-page; graph is an escalation ladder; output is a deterministic recipe |
| Learning rules from examples | AutoScraper (rules from example values) | rules learned from *validated* stage hits and *grounded* agent teaching, scoped per URL template, self-healing |
| Lightweight rendering for agents | Obscura (Rust headless browser with CDP/MCP) | candidate engine for the optional render stage |
| On-page signals | the author's earlier SEO on-page parser (headings, emphasis, link classes, alt coverage) | used as relevance weights for ranking, not as an SEO report |

See `research-basis.md` for the academic lineage (wrapper induction, wrapper maintenance, boilerplate detection, BM25F).
