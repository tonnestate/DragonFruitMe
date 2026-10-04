from dragonfruitme.graph import build_graph
from dragonfruitme.traversal import analyze_traversal


def test_pagination_detects_last_control_and_builds_template():
    html = """
    <html><body><nav>
      <a href="/studios?page=1">1</a>
      <a href="/studios?page=2" rel="next">2</a>
      <a href="/studios?page=3">3</a>
      <a href="/studios?page=88">&gt;&gt;</a>
    </nav></body></html>
    """
    graph = build_graph(html, "https://example.org/studios?page=1")
    traversal = analyze_traversal(graph)
    p = traversal["pagination"]

    assert traversal["coverage_status"] == "INCOMPLETE"
    assert traversal["claim_complete"] is False
    assert "PAGINATION_OPEN" in traversal["signals"]
    assert p["current_page"] == 1
    assert p["last_page"] == 88
    assert p["last_url"] == "https://example.org/studios?page=88"
    assert p["last_evidence"] == "last-control"
    assert p["next_url"] == "https://example.org/studios?page=2"
    assert p["remaining_pages"] == 87
    assert p["template"] == "https://example.org/studios?page={page}"


def test_pagination_detects_rel_last_and_triple_arrow():
    html = """
    <html><body><nav>
      <a href="/liste/seite/2" rel="next">&gt;</a>
      <a href="/liste/seite/250" rel="last">&gt;&gt;&gt;</a>
    </nav></body></html>
    """
    graph = build_graph(html, "https://example.org/liste/seite/1")
    traversal = analyze_traversal(graph)
    p = traversal["pagination"]

    assert p["last_page"] == 250
    assert p["last_evidence"] == "rel=last"
    assert p["template"] == "https://example.org/liste/seite/{page}"
    assert p["bounded"] is True
    assert p["has_next"] is True


def test_numeric_terminal_gap_is_weaker_last_page_evidence():
    html = """
    <nav>
      <a href="/directory?page=1">1</a>
      <a href="/directory?page=2">2</a>
      <a href="/directory?page=3">3</a>
      <span>…</span>
      <a href="/directory?page=431">431</a>
    </nav>
    """
    graph = build_graph(html, "https://example.org/directory?page=1")
    p = analyze_traversal(graph)["pagination"]

    assert p["last_page"] == 431
    assert p["last_evidence"] == "terminal-page-gap"
    assert p["remaining_pages"] == 430


def test_next_without_last_is_open_but_unbounded():
    html = '<nav><a href="/results?page=2" rel="next">Weiter</a></nav>'
    graph = build_graph(html, "https://example.org/results?page=1")
    traversal = analyze_traversal(graph)
    p = traversal["pagination"]

    assert traversal["coverage_status"] == "INCOMPLETE"
    assert p["detected"] is True
    assert p["has_next"] is True
    assert p["bounded"] is False
    assert p["last_page"] is None
    assert p["next_url"] == "https://example.org/results?page=2"


def test_repeated_detail_scope_is_exposed_as_child_level():
    html = """
    <main>
      <a href="/studio/1001">Küchenstudio Nord</a>
      <a href="/studio/1002">Küchenstudio Süd</a>
      <a href="/studio/1003">Küchenstudio West</a>
      <a href="/kontakt">Kontakt</a>
    </main>
    """
    graph = build_graph(html, "https://example.org/studios")
    traversal = analyze_traversal(graph)

    assert traversal["coverage_status"] == "INCOMPLETE"
    assert "CHILD_LEVEL_CANDIDATES" in traversal["signals"]
    assert traversal["known_open_urls"] == 3
    groups = traversal["child_collections"]
    assert len(groups) == 1
    assert groups[0]["scope"] == "example.org/studio/*"
    assert groups[0]["kind"] == "detail_candidates"
    assert groups[0]["count"] == 3


def test_query_id_detail_level_is_detected_even_without_dynamic_path():
    html = """
    <main>
      <a href="/profile?id=10">A</a>
      <a href="/profile?id=11">B</a>
      <a href="/profile?id=12">C</a>
    </main>
    """
    graph = build_graph(html, "https://example.org/list")
    groups = analyze_traversal(graph)["child_collections"]

    assert len(groups) == 1
    assert groups[0]["scope"] == "example.org/profile"
    assert groups[0]["kind"] == "detail_candidates"
    assert groups[0]["count"] == 3


def test_single_page_never_claims_source_completeness():
    graph = build_graph("<main><h1>One page</h1><p>No traversal evidence.</p></main>",
                        "https://example.org/one")
    traversal = analyze_traversal(graph)

    assert traversal["coverage_status"] == "UNKNOWN"
    assert traversal["claim_complete"] is False
    assert traversal["signals"] == []
    assert traversal["known_open_urls"] == 0


def test_link_rel_metadata_is_retained_by_graph():
    graph = build_graph('<a href="/p2" rel="next nofollow">Weiter</a>', "https://example.org/p1")
    link = graph.links[0]
    assert link.rel == ("next", "nofollow")
    assert link.nofollow is True


def test_observe_exposes_traversal_without_new_agent_operation(tmp_path):
    from dragonfruitme import DragonFruitMe

    html = '<nav><a href="/directory?page=2" rel="next">2</a><a href="/directory?page=40">&gt;&gt;</a></nav>'
    with DragonFruitMe(state_dir=tmp_path / "state") as tool:
        result = tool.observe(html=html, base_url="https://example.org/directory?page=1")

    assert result["ok"] is True
    assert result["traversal"]["coverage_status"] == "INCOMPLETE"
    assert result["traversal"]["pagination"]["last_page"] == 40
