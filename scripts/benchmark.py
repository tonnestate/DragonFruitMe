"""Reproducible offline benchmark of the extraction hot path.

    python scripts/benchmark.py              # 400 pages x 5 fields
    python scripts/benchmark.py --pages 2000
    python scripts/benchmark.py --scaling    # 1 / 10 / 100 / 1,000 / 100,000 stored recipes

Measures what DragonFruitMe itself costs per page once recipes are warm
(the typical batch case) and for the first, learning page. No network: in a
real batch the per-host politeness interval (default 1 s) dominates; this
number matters for many hosts, re-runs and HTML already in hand.
"""
from __future__ import annotations

import argparse
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dragonfruitme import DragonFruitMe  # noqa: E402

FIELDS = [
    {"name": "kaufpreis", "type": "price"},
    {"name": "wohnflaeche", "type": "area", "aliases": ["Wohnfläche"]},
    {"name": "baujahr", "type": "integer"},
    {"name": "email", "type": "email", "aliases": ["E-Mail"]},
    {"name": "energie", "aliases": ["Energieträger"]},
]


def page(n: int) -> str:
    return f"""<!doctype html><html lang="de"><head><title>Exposé {n}</title>
<script type="application/ld+json">{{"@type":"Offer","offers":{{"price":"{200000 + n * 1000}"}}}}</script></head>
<body><nav><a href="/">Start</a> <a href="/angebote">Angebote</a></nav><main>
<h1>Wohnung {n}</h1><p>Eine <strong>helle Wohnung</strong> mit Balkon.</p><h2>Objektdaten</h2>
<dl><dt>Kaufpreis</dt><dd class="price" data-id="{n}">{200 + n}.000 €</dd>
<dt>Wohnfläche</dt><dd>{50 + n % 90},5 m²</dd><dt>Zimmer</dt><dd>3</dd></dl>
<table><tr><th>Baujahr</th><td>{1950 + n % 70}</td></tr><tr><th>Energieträger</th><td>Gas</td></tr></table>
<h2>Kontakt</h2><p>Ihr Makler, E-Mail: info@makler.example</p></main>
<footer><p>Impressum Datenschutz</p></footer></body></html>"""


def _prefill(tool: DragonFruitMe, stored: int) -> None:
    """Fill the store with ``stored`` recipes: half in other buckets, half retired in the hot bucket."""
    if stored <= 0:
        return
    db, now = tool.engine.store._db, time.time()
    rows = []
    for i in range(stored):
        if i % 2:
            rows.append((f"other{i % 997}.example/x/*", f"f{i % 7}", f"p{i}(.*)", "candidate"))
        else:
            rows.append(("makler.example/expose/*", "kaufpreis", f"old{i}(.*)", "retired"))
    with tool.engine.store.transaction():
        db.executemany(
            "INSERT INTO recipe_set (scope, field, kind, pattern, origin, role, hits, misses, total_misses, "
            "contradictions, confirmed_hits, last_validated, created_at, updated_at) "
            "VALUES (?,?,'regex',?,'label',?,1,0,0,0,0,NULL,?,?)",
            [(scope, field, pattern, role, now, now) for scope, field, pattern, role in rows],
        )


def run(pages_count: int, stored: int = 0) -> tuple[float, list[float]]:
    pages = [page(n) for n in range(pages_count + 1)]
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp, DragonFruitMe(state_dir=tmp) as tool:
        started = time.perf_counter()
        first = tool.extract(fields=FIELDS, html=pages[0], base_url="https://makler.example/expose/0")
        cold_ms = (time.perf_counter() - started) * 1000
        assert first["status"] == "COMPLETE", first
        _prefill(tool, stored)
        timings = []
        for n, html in enumerate(pages[1:], 1):
            t = time.perf_counter()
            result = tool.extract(fields=FIELDS, html=html, base_url=f"https://makler.example/expose/{n}")
            timings.append((time.perf_counter() - t) * 1000)
            assert result["status"] == "COMPLETE", result
            assert all(f["stage"] == "recipe" for f in result["fields"]), result
    timings.sort()
    return cold_ms, timings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="offline extraction benchmark")
    parser.add_argument("--pages", type=int, default=400)
    parser.add_argument("--scaling", action="store_true",
                        help="healthy-primary latency with 1 / 10 / 100 / 1,000 / 100,000 stored recipes")
    args = parser.parse_args(argv)
    if args.scaling:
        print("stored recipes | median ms per warm page (5 fields)")
        for stored in (1, 10, 100, 1_000, 100_000):
            _, timings = run(min(args.pages, 300), stored)
            print(f"{stored:>14,} | {statistics.median(timings):.2f}")
        return 0
    cold_ms, timings = run(args.pages)
    p95 = timings[int(len(timings) * 0.95) - 1]
    print(f"first page (learning): {cold_ms:.1f} ms")
    print(f"warm pages: {len(timings)} x {len(FIELDS)} fields, "
          f"median {statistics.median(timings):.2f} ms, p95 {p95:.2f} ms, "
          f"throughput {1000 / statistics.mean(timings):.0f} pages/s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
