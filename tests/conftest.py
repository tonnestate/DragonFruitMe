from __future__ import annotations

import gzip
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable

import pytest

from dragonfruitme import DragonFruitMe
from dragonfruitme.fetch import Fetcher
from dragonfruitme.policy import FetchPolicy

EXPOSE = """<!doctype html>
<html lang="de"><head><meta charset="utf-8"><title>Exposé: 3-Zimmer-Wohnung in Kiel</title>
<meta name="description" content="Helle Wohnung mit Balkon in Kiel-Düsternbrook">
<link rel="canonical" href="/expose/{id}">
<link rel="alternate" type="application/rss+xml" href="/feed.xml">
<script type="application/ld+json">{{"@context":"https://schema.org","@type":"Offer","name":"3-Zimmer-Wohnung","offers":{{"price":"{ld_price}","priceCurrency":"EUR"}}}}</script>
<script src="/app.js"></script>
</head><body>
<header><a href="/"><img src="/logo.png" alt="Makler Logo"></a></header>
<nav><a href="/">Start</a> <a href="/angebote">Angebote</a> <a href="/kontakt">Kontakt</a></nav>
<main>
<h1>3-Zimmer-Wohnung in Kiel</h1>
<p>Diese <strong>helle Wohnung</strong> liegt ruhig. Der Balkon zeigt nach Süden.</p>
<h2>Objektdaten</h2>
<dl class="facts">
  <dt>Kaufpreis</dt><dd class="price" data-id="{id}">{price}</dd>
  <dt>Wohnfläche</dt><dd>{area}</dd>
  <dt>Zimmer</dt><dd>3</dd>
</dl>
<table class="details">
  <tr><th>Baujahr</th><td>{year}</td></tr>
  <tr><th>Energieträger</th><td>Gas</td></tr>
</table>
<h2>Ansprechpartner</h2>
<p>Ihr Makler: <strong>Max Muster</strong>, Telefon: +49 431 123456, E-Mail: max@example.de</p>
<p>Weitere Objekte finden Sie unter <a href="https://partner.example.org/liste" rel="nofollow">Partnerliste</a>.</p>
<img src="/grundriss.png">
</main>
<footer><p>Impressum · Kaufpreis-Rechner · Datenschutz</p></footer>
</body></html>"""


def expose(id: str = "4711", price: str = "349.000 €", area: str = "78,5 m²", year: str = "1998",
           ld_price: str = "349000") -> str:
    return EXPOSE.format(id=id, price=price, area=area, year=year, ld_price=ld_price)


@pytest.fixture
def tool(tmp_path):
    policy = FetchPolicy(min_interval_seconds=0.0)
    return DragonFruitMe(state_dir=tmp_path / "state", policy=policy)


class _Site:
    def __init__(self) -> None:
        self.routes: dict[str, tuple[int, str, dict[str, str]]] = {}
        self.hits: dict[str, int] = {}
        self.server: ThreadingHTTPServer | None = None

    def route(self, path: str, body: str, status: int = 200, headers: dict[str, str] | None = None) -> None:
        self.routes[path] = (status, body, headers or {})

    @property
    def base(self) -> str:
        assert self.server is not None
        host, port = self.server.server_address[:2]
        return f"http://{host}:{port}"


@pytest.fixture
def site():
    state = _Site()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            state.hits[self.path] = state.hits.get(self.path, 0) + 1
            status, body, headers = state.routes.get(self.path, (404, "not found", {}))
            data = body.encode("utf-8")
            if headers.get("Content-Encoding") == "gzip":
                data = gzip.compress(data)
            self.send_response(status)
            self.send_header("Content-Type", headers.get("Content-Type", "text/html; charset=utf-8"))
            for key, value in headers.items():
                if key != "Content-Type":
                    self.send_header(key, value)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args) -> None:  # silence test output
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    state.server = server
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def make_tool(tmp_path) -> Callable[..., DragonFruitMe]:
    def factory(renderer=None, **policy_kwargs) -> DragonFruitMe:
        policy = FetchPolicy(min_interval_seconds=0.0, **policy_kwargs)
        return DragonFruitMe(state_dir=tmp_path / "state", policy=policy, fetcher=Fetcher(policy, renderer=renderer))

    return factory
