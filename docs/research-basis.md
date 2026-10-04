# Research Basis

DragonFruitMe's ideas are old; the combination and the agent interface are new. This page names the lineage so claims can be checked.

## Wrapper induction — "compile once"

* N. Kushmerick, D. S. Weld, R. Doorenbos: *Wrapper Induction for Information Extraction*, IJCAI 1997. Learning left/right delimiter wrappers (LR wrappers) from labelled examples.
  → DragonFruitMe's regex recipes are LR-style wrappers: a left anchor plus a typed capture, induced from a single validated example instead of a labelled set.
* V. Crescenzi, G. Mecca, P. Merialdo: *RoadRunner: Towards Automatic Data Extraction from Large Web Sites*, VLDB 2001. Templates inferred by comparing pages of the same site.
  → Motivates per-template scopes (`host/expose/*`) and the planned multi-page recipe confirmation.
* N. Dalvi, R. Kumar, M. Soliman: *Automatic Wrappers for Large Scale Web Extraction*, PVLDB 2011. Robust wrappers under noisy annotations at scale.
  → Motivates validation-gated recipe acceptance.

## Wrapper maintenance — "self-healing"

* N. Kushmerick: *Wrapper Verification*, World Wide Web journal, 2000. Detecting when a wrapper silently breaks.
* K. Lerman, S. Minton, C. Knoblock: *Wrapper Maintenance: A Machine Learning Approach*, JAIR 2003. Re-inducing wrappers after site changes.
  → DragonFruitMe verifies every recipe output against the field type (verification), counts misses, marks stale recipes and re-compiles from the next successful stage (maintenance). Label-anchored recipes are preferred because labels are more stable than markup.

## Relevance on the page — "SEO-weighted graph"

* S. Robertson, H. Zaragoza, M. Taylor: *Simple BM25 Extension to Multiple Weighted Fields*, CIKM 2004 (BM25F).
  → Heading levels, emphasis and regions act as weighted fields.
* C. Kohlschütter, P. Fankhauser, W. Nejdl: *Boilerplate Detection using Shallow Text Features*, WSDM 2010.
  → Region factors (nav/footer/aside down-weighted) are a deliberately simple boilerplate prior.

## Agent interfaces

* The compact-interface argument from SWE-agent / BananaMe applies to the web as well: fewer, well-typed operations with bounded outputs beat raw page dumps for agent reliability and token cost.
* LLM-based extraction (e.g. ScrapeGraphAI) shows that models can extract from arbitrary layouts; DragonFruitMe uses that capability once per template (teaching) rather than per page.

## Open questions for evaluation

1. Recipe survival: share of pages per scope answered by stage 1 over time, and miss-to-recompile latency after redesigns.
2. Token cost per field versus LLM-per-page extraction at equal accuracy.
3. Ranking quality of `locate` versus plain BM25 without on-page weights.
4. False-positive rate of challenge detection on content pages that embed CAPTCHA widgets.
