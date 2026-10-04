# Protocol

All interfaces (Python, CLI, MCP) return JSON-compatible dicts. Success always has `"ok": true`; failure is `{"ok": false, "error": {"code", "message", "recoverable", "details"}}`.

## Source

Every operation takes exactly one source:

* `url` — fetched under the host's FetchPolicy, or
* `html` (+ optional `base_url`) — already in hand; no network.

## `observe(url | html, base_url?)`

Returns `page` (fetch info), `scope`, `summary` and `render_available`.

`summary`: `title, lang, meta_description, meta_robots, canonical, h1[], outline[] (+outline_truncated), counts{blocks, text_chars, links_internal, links_external, links_nofollow, images, images_missing_alt}, structured_data{jsonld_blocks, embedded_json_blocks, itemprops, open_graph[]}, feeds[], internal_links[] (+internal_links_truncated), js_shell_score, needs_render`.

## `locate(query, url | html, base_url?, max_results=8, max_context_chars=4000)`

Returns `hits[]` with `block_id, kind, text, section[], region, score, matched[], links[]?, next?`, plus `total_candidates, results_truncated, context_truncated`.

## `extract(fields[], url | html, base_url?, scope?, render="auto", learn=true)`

Field spec:

```json
{
  "name": "kaufpreis",
  "type": "price",
  "aliases": ["Kaufpreis", "Preis"],
  "paths": ["offers.price"],
  "min": 1000,
  "max": 50000000,
  "pattern": null,
  "teach": null,
  "min_provenance": null
}
```

A bare string is shorthand for `{"name": "...", "type": "text"}`.

Types: `text, price, area, number, integer, date, email, phone, url`.

Field result: `name, type, status, value, normalized, stage, page_stage, evidence, recipe, provenance, confirmed_by[], signals[], attempts[]`; `UNCONFIRMED` adds `required_provenance`; `NEEDS_AGENT` adds `candidates[]` and `next`.

Field status: `FOUND`, `UNCONFIRMED` (value below `min_provenance`), `NEEDS_AGENT`, `NOT_GROUNDED`.

Provenance: `AGENT_TAUGHT` < `ENGINE_OBSERVED` < `CROSS_CONFIRMED`. `min_provenance` accepts these names case-insensitively.

Signals:

| code | meaning |
|---|---|
| `STAGE_DISAGREEMENT` | an independent stage answered differently (`disagreements[]: {stage, value}`), but the value was kept |
| `REPEATED_VALUE` | the same unconfirmed value on `urls` ≥ 3 different consecutive URLs of the scope |

Call result: `status (COMPLETE | PARTIAL | INCOMPLETE), scope, fields[], pages[], found, total`.

## `extract-batch` (host-side CLI only, not an agent operation)

```bash
dragonfruitme extract-batch --urls-file FILE --fields-json JSON [--output OUT.jsonl] [--resume]
                            [--render auto|on_miss|never] [--no-learn] [--fail-on-error]
```

* Input: one URL per line (`#` comments allowed) or a JSON array; duplicates are removed, order is kept.
* Output: one JSONL row per URL, `{"url": ..., <extract result>}`, flushed immediately; stdout if `--output` is omitted.
* Summary on stderr: `{"ok": true, "batch": {total, skipped, attempted, complete, partial, incomplete, errors, unconfirmed_fields, flagged_rows}}`; `flagged_rows` counts rows with at least one field signal.
* `--resume` (requires `--output`): repairs a torn last line, skips URLs whose latest row is final (`ok: true` or `error.recoverable: false`), retries the rest. The latest row per URL is authoritative.
* Exit code 0 unless `--fail-on-error` is set and at least one URL failed (then 2).

## Attempt outcomes

| stage | outcomes |
|---|---|
| `recipe` | `SKIP` (`NO_RECIPE`), `HIT`, `MISS` (+ validation reason or `CONTRADICTED` with `recipe_value`, `contradicted_by[]`, and `recipe_status`) |
| `structured` | `HIT`, `MISS` (`NO_CANDIDATE`, `NO_VALID_CANDIDATE`) |
| `label` | `HIT`, `MISS` (`NO_LABEL`, `NO_VALID_CANDIDATE`) |
| `render` | `RENDERED`, `SKIP` (`PAGE_NOT_JS_SHELL`, `NO_URL_OR_RENDERER`, `RENDER_NEVER`), `UNAVAILABLE`, `FAILED` |
| `taught` | `GROUNDED`, `REJECTED` (`VALUE_NOT_IN_PAGE` or validation reason) |
| `agent` | `NEEDS_AGENT` |

## Error codes

| code | recoverable | meaning |
|---|---|---|
| `BLOCKED` | no | bot challenge, rate limit (`RATE_LIMITED`) or 401/403; see `details.reason`, `details.next` |
| `ROBOTS_DISALLOWED` | no | robots.txt forbids the URL for this User-Agent |
| `HTTP_ERROR` | 5xx: yes | non-2xx response |
| `FETCH_FAILED` | yes | DNS/connection/timeout |
| `URL_SCHEME_DENIED`, `URL_INVALID`, `HOST_DENIED` | no | policy |
| `RENDER_NOT_AVAILABLE` | yes | browser extra missing (recorded as attempt during extract) |
| `SOURCE_REQUIRED`, `SOURCE_AMBIGUOUS` | yes | pass exactly one of `url` / `html` |
| `QUERY_REQUIRED` | yes | empty locate query |
| `FIELDS_REQUIRED`, `FIELD_INVALID`, `FIELD_TYPE_UNKNOWN`, `FIELD_DUPLICATE` | yes | field spec problems |
| `RENDER_MODE_INVALID` | yes | `render` not in `auto, on_miss, never` |
| `STATE_DIR_UNAVAILABLE`, `STATE_DIR_SYMLINK` | no | host configuration |
| `UNEXPECTED_ERROR` | no | bug; `details.type` names the exception class |
