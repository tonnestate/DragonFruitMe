from conftest import expose

from dragonfruitme.graph import build_graph
from dragonfruitme.locate import locate


def test_main_label_outranks_footer_and_carries_value_neighbour():
    graph = build_graph(expose(), "https://www.makler.example/expose/4711")
    result = locate(graph, "Kaufpreis")
    top = result["hits"][0]
    assert top["text"] == "Kaufpreis" and top["region"] == "main"
    assert top["next"]["text"] == "349.000 €"
    assert result["hits"][-1]["region"] == "footer"


def test_section_heading_boosts_blocks_without_the_term():
    graph = build_graph(expose(), "https://www.makler.example/expose/4711")
    hits = locate(graph, "Ansprechpartner Telefon")["hits"]
    contact = next(h for h in hits[:2] if h["text"].startswith("Ihr Makler"))
    # "Ansprechpartner" only occurs in the section heading, yet it counts for the paragraph
    assert set(contact["matched"]) == {"ansprechpartner", "telefon"}
    assert contact["section"][-1] == "Ansprechpartner"


def test_locate_is_bounded():
    graph = build_graph(expose(), "https://www.makler.example/expose/4711")
    result = locate(graph, "Wohnung Kiel Balkon", max_results=1)
    assert len(result["hits"]) == 1 and result["results_truncated"] is True
    assert locate(graph, "")["hits"] == []
