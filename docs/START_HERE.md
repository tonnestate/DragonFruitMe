# Start Here

DragonFruitMe is a headless web interface for AI agents:

```text
observe → locate → extract
```

It does not browse on its own, does not decide what to read next and contains no LLM. It fetches politely, turns pages into a graph, finds the relevant blocks and extracts typed values cheapest-first.

## Mental model

```text
Agent: "I need price, living area and year of construction from this exposé"
      │
      ▼
EXTRACT — per field
  recipe? ─no─▶ structured data? ─no─▶ label → value? ─no─▶ render (JS shell)? ─no─▶ NEEDS_AGENT
     │yes             │yes                    │yes                  │yes
     ▼                ▼                       ▼                     ▼
  FOUND           FOUND + compile         FOUND + compile       FOUND + compile
      │
      ▼
Next exposé of the same site → stage 1 (one regex) for every field
```

## First workflow

### 1. Look at the page as a graph

```bash
dragonfruitme observe --url https://example.org/expose/4711
```

Check `outline`, `structured_data` and `needs_render`. If `jsonld_blocks > 0`, give your fields `paths` such as `offers.price`.

### 2. Find where things are

```bash
dragonfruitme locate --url https://example.org/expose/4711 --query "Kaufpreis Provision"
```

The top hit is usually the label; its `next` block is usually the value.

### 3. Extract typed fields

```bash
dragonfruitme extract --url https://example.org/expose/4711 --fields-json '[
  {"name": "kaufpreis", "type": "price", "aliases": ["Kaufpreis"]},
  {"name": "provision", "type": "text", "aliases": ["Provision", "Käuferprovision"]}
]'
```

Read `stage` and `attempts` to see how each value was found. Run the same command on another exposé of the same site: every field should now come from `stage: "recipe"`.

### 4. Teach what the cheap stages missed

If a field returns `NEEDS_AGENT`, read its `candidates`. If the value is there:

```bash
dragonfruitme extract --url https://example.org/expose/4711 --fields-json '[
  {"name": "provision", "teach": "3,57 % inkl. MwSt."}
]'
```

The value is accepted only if it occurs in the page, then compiled into a recipe.

### 5. Inspect and reset recipes

```bash
dragonfruitme recipes --scope example.org/expose/*
dragonfruitme forget --scope example.org/expose/* --field provision
```

## Many URLs at once

For an explicit list of already-known URLs, use the host-side batch runner instead of a hand-written loop:

```bash
dragonfruitme extract-batch --urls-file urls.txt --fields-json '[{"name": "kaufpreis", "type": "price"}]' \
  --output results.jsonl --resume
```

One recipe store stays warm across the run, so the first pages teach recipes and the rest are answered by stage 1. Each URL is written and flushed as one JSONL row. After an interruption, run the same command again: finished URLs are skipped, transient failures are retried, and a row torn by the interruption is repaired.

## When the answer is BLOCKED

`BLOCKED`, `RATE_LIMITED` and `ROBOTS_DISALLOWED` are final. Use the site's official API or feed, ask a human, or choose another source. DragonFruitMe will not try to get around them, and agents using it should not either.
