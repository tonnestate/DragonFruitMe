# Contributing

DragonFruitMe is intentionally small. Contributions should preserve the public AI-facing surface:

```text
observe
locate
extract
```

Prefer reuse of proven libraries and tools over new internal frameworks. New dependencies must be optional extras unless they have an overwhelming runtime benefit and a GPL-compatible license.

Core invariants:

- no embedded LLM, planner, autonomous crawl loop or model router;
- no CAPTCHA solving, stealth, fingerprint spoofing or proxy rotation — challenges end the call with `BLOCKED`;
- the fetch policy and the recipe store are bound by the host, never by the agent;
- every stage validates its candidate against the field type before it counts;
- taught values must be grounded in the page;
- every field result carries its escalation path, evidence and provenance;
- an agent can never raise the provenance of its own claim;
- outputs stay bounded and report truncation;
- the core imports only the Python standard library;
- package/repository layout stays shallow: no ordinary project file deeper than two directories; `.github/workflows/` is the exception.

Before opening a pull request, run:

```bash
python -m pip install -e '.[dev]'
python -m compileall -q src
python scripts/scenario_matrix.py   # regenerates docs/EVIDENCE.md
pytest
```

A change that adds or alters a detector must add both a pathology it catches and a healthy control it must not fire on to `scripts/scenario_matrix.py`.

Tests must not reach the public internet; use inline HTML or the local test server in `tests/conftest.py`.

If a contribution directly reuses third-party source code rather than only a concept or public interface, update `THIRD_PARTY_NOTICES.md` and preserve all license obligations.
