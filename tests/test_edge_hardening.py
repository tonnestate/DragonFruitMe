from dragonfruitme.graph import build_graph


def test_accessible_aria_label_wrapper_is_extractable(tool):
    html = """<html><body><main>
    <div class="contact" aria-label="Telefon"><span>+49 40 12345678</span></div>
    </main></body></html>"""
    result = tool.extract(
        fields=[{"name": "telefon", "type": "phone", "aliases": ["Telefon"]}],
        html=html,
        base_url="https://studio.example/kontakt/1",
        render="never",
    )
    field = result["fields"][0]
    assert field["status"] == "FOUND"
    assert field["stage"] == "label"
    assert field["value"] == "+49 40 12345678"
    assert field["evidence"]["label"] == "Telefon"


def test_data_label_wrapper_is_extractable(tool):
    html = """<html><body><main>
    <div class="contact-value" data-label="E-Mail"><strong>studio@example.de</strong></div>
    </main></body></html>"""
    result = tool.extract(
        fields=[{"name": "email", "type": "email", "aliases": ["E-Mail"]}],
        html=html,
        base_url="https://studio.example/kontakt/2",
        render="never",
    )
    field = result["fields"][0]
    assert field["status"] == "FOUND"
    assert field["value"] == "studio@example.de"


def test_label_hint_still_requires_typed_validation(tool):
    html = """<html><body><main>
    <div aria-label="Telefon">Montag bis Freitag</div>
    </main></body></html>"""
    result = tool.extract(
        fields=[{"name": "telefon", "type": "phone", "aliases": ["Telefon"]}],
        html=html,
        base_url="https://studio.example/kontakt/3",
        render="never",
    )
    field = result["fields"][0]
    assert field["status"] == "NEEDS_AGENT"
    assert field["value"] is None


def test_duplicate_label_cards_do_not_stop_at_invalid_first_card(tool):
    html = """<html><body><main>
    <div class="card">
      <div><span>Telefon</span></div>
      <div><span>Nicht veröffentlicht</span></div>
    </div>
    <div class="card">
      <div><span>Telefon</span></div>
      <div><strong>+49 40 87654321</strong></div>
    </div>
    </main></body></html>"""
    result = tool.extract(
        fields=[{"name": "telefon", "type": "phone", "aliases": ["Telefon"]}],
        html=html,
        base_url="https://directory.example/list/1",
        render="never",
    )
    field = result["fields"][0]
    assert field["status"] == "FOUND"
    assert field["value"] == "+49 40 87654321"


def test_script_noise_and_malformed_closing_tags_do_not_create_false_value(tool):
    html = """<html><body><main>
    <script>var fake = "Telefon: +49 40 99999999";</script>
    <div><p>Telefon<p>+49 40 11111111</b></i></div>
    </main></body></html>"""
    result = tool.extract(
        fields=[{"name": "telefon", "type": "phone", "aliases": ["Telefon"]}],
        html=html,
        base_url="https://broken.example/contact",
        render="never",
    )
    field = result["fields"][0]
    assert field["status"] == "FOUND"
    assert field["value"] == "+49 40 11111111"


def test_structural_fallback_is_bounded(tool):
    noise = "".join(f"<div>Hinweis {i}</div>" for i in range(10))
    html = f"""<html><body><main><div class="card">
    <div>Telefon</div>{noise}<div>+49 40 22222222</div>
    </div></main></body></html>"""
    result = tool.extract(
        fields=[{"name": "telefon", "type": "phone", "aliases": ["Telefon"]}],
        html=html,
        base_url="https://broken.example/bounded",
        render="never",
    )
    field = result["fields"][0]
    assert field["status"] == "NEEDS_AGENT"


def test_graph_retains_attribute_hints_without_exposing_them_as_text():
    graph = build_graph(
        '<html><body><main><div aria-label="Telefon"><span>+49 40 12345678</span></div></main></body></html>',
        "https://studio.example/",
    )
    block = next(b for b in graph.blocks if "+49 40 12345678" in b.text)
    assert "Telefon" in block.label_hints
    assert block.text == "+49 40 12345678"
