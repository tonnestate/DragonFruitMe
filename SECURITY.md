# Security and Responsible Use

DragonFruitMe fetches untrusted web content on behalf of an AI agent. It therefore treats every page as untrusted input and every agent request as bounded by host policy.

## v0.1.0 protections

- only `http`/`https`; host deny-list via `DRAGONFRUITME_DENY_HOSTS`;
- robots.txt honoured by default; only the host can disable this;
- honest, identifiable User-Agent by default;
- per-host minimum request interval, request timeout and response size cap;
- bot challenges, CAPTCHAs, HTTP 401/403/429 end the call with `BLOCKED` and are never retried through another strategy;
- no stealth, fingerprint spoofing, proxy rotation or CAPTCHA solving — and no plugin interface for them;
- page content is parsed, never executed, in the core (`html.parser`, `json`, `re`); JavaScript runs only in the optional, isolated render stage;
- the recipe store location is host-bound (`DRAGONFRUITME_STATE_DIR`) and may not be a symlink; agents cannot pass filesystem paths;
- taught values are accepted only if they occur in the fetched page;
- bounded outputs (`max_results`, `max_context_chars`, outline/link limits) with explicit truncation flags.

## Limits of the guarantee

- Text extracted from pages can contain prompt-injection attempts. DragonFruitMe returns it as data with provenance; the host agent must still treat it as untrusted.
- Regex recipes are derived from page content. Patterns are built from escaped literals and fixed typed captures, and run on size-capped input, but a host processing hostile sites at scale should run DragonFruitMe in its own sandbox with CPU/time limits.
- The local network is reachable by default because agent tools often work against local services. Hosts exposing DragonFruitMe to untrusted agents should deny internal hosts.
- Complying with a site's terms of service, copyright and data-protection law (e.g. GDPR for personal data, database rights) remains the responsibility of the operator.

## Reporting

Please report vulnerabilities privately via GitHub security advisories on `tonnestate/DragonFruitMe`.
