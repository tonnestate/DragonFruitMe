"""Reproducible offline benchmark of the extraction hot path.

    python scripts/benchmark.py              # 400 pages x 5 fields
    python scripts/benchmark.py --pages 2000

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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="offline extraction benchmark")
    parser.add_argument("--pages", type=int, default=400)
    args = parser.parse_args(argv)
    pages = [page(n) for n in range(args.pages + 1)]
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp, DragonFruitMe(state_dir=tmp) as tool:
        started = time.perf_counter()
        first = tool.extract(fields=FIELDS, html=pages[0], base_url="https://makler.example/expose/0")
        cold_ms = (time.perf_counter() - started) * 1000
        assert first["status"] == "COMPLETE", first
        timings = []
        for n, html in enumerate(pages[1:], 1):
            t = time.perf_counter()
            result = tool.extract(fields=FIELDS, html=html, base_url=f"https://makler.example/expose/{n}")
            timings.append((time.perf_counter() - t) * 1000)
            assert result["status"] == "COMPLETE", result
            assert all(f["stage"] == "recipe" for f in result["fields"]), result
    timings.sort()
    p95 = timings[int(len(timings) * 0.95) - 1]
    print(f"first page (learning): {cold_ms:.1f} ms")
    print(f"warm pages: {len(timings)} x {len(FIELDS)} fields, "
          f"median {statistics.median(timings):.2f} ms, p95 {p95:.2f} ms, "
          f"throughput {1000 / statistics.mean(timings):.0f} pages/s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
