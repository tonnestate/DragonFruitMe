from conftest import expose

from dragonfruitme.graph import build_graph, summarize


def test_graph_sections_regions_and_rows():
    graph = build_graph(expose(), "https://www.makler.example/expose/4711")
    by_text = {b.text: b for b in graph.blocks}
    assert graph.title == "Exposé: 3-Zimmer-Wohnung in Kiel"
    assert graph.lang == "de"
    price = by_text["349.000 €"]
    assert price.kind == "dd"
    assert price.section == ("3-Zimmer-Wohnung in Kiel", "Objektdaten")
    assert by_text["Baujahr"].row == by_text["1998"].row is not None
    assert by_text["Start Angebote Kontakt"].region == "nav"
    assert by_text["Impressum · Kaufpreis-Rechner · Datenschutz"].region == "footer"
    assert "helle Wohnung" in by_text["Diese helle Wohnung liegt ruhig. Der Balkon zeigt nach Süden."].emphasis
    h1 = by_text["3-Zimmer-Wohnung in Kiel"]
    assert h1.heading_level == 1 and h1.weight > price.weight


def test_graph_links_meta_scripts_images():
    graph = build_graph(expose(), "https://www.makler.example/expose/4711")
    hrefs = {l.href: l for l in graph.links}
    assert hrefs["https://www.makler.example/kontakt"].internal
    partner = hrefs["https://partner.example.org/liste"]
    assert not partner.internal and partner.nofollow
    assert graph.canonical == "https://www.makler.example/expose/4711"
    assert graph.feeds == ["https://www.makler.example/feed.xml"]
    assert graph.meta["description"].startswith("Helle Wohnung")
    assert [s["type"] for s in graph.scripts] == ["jsonld"]
    assert graph.images == 2 and graph.images_missing_alt == 1


def test_summary_is_bounded_and_flags_js_shell():
    summary = summarize(build_graph(expose(), "https://www.makler.example/expose/4711"), max_outline=2, max_links=1)
    assert summary["outline_truncated"] is True
    assert len(summary["internal_links"]) == 1 and summary["internal_links_truncated"] is True
    assert summary["needs_render"] is False
    shell = build_graph('<html><body><div id="root"></div><script src="a.js"></script>' + "<script></script>" * 5 + "</body></html>")
    assert summarize(shell)["needs_render"] is True


def test_malformed_html_does_not_break_the_graph():
    graph = build_graph("<p>Kaufpreis<p>349.000 €<li>Zimmer<td>3</b></i><script>var x='<p>';</script><p>Ende")
    assert [b.text for b in graph.blocks] == ["Kaufpreis", "349.000 €", "Zimmer", "3", "Ende"]
