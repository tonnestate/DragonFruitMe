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


def test_style_template_and_noscript_noise_are_ignored(tool):
    html = """<html><body><main>
    <style>.phone:after { content: "+49 40 99999999"; }</style>
    <template><div>Telefon: +49 40 88888888</div></template>
    <noscript>Telefon: +49 40 77777777</noscript>
    <div class="card"><div>Telefon</div><div>+49 40 12345678</div></div>
    </main></body></html>"""
    result = tool.extract(
        fields=[{"name": "telefon", "type": "phone", "aliases": ["Telefon"]}],
        html=html,
        base_url="https://broken.example/noise",
        render="never",
    )
    field = result["fields"][0]
    assert field["status"] == "FOUND"
    assert field["value"] == "+49 40 12345678"


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


def test_accessible_name_is_not_promoted_to_field_label(tool):
    html = """<html><body><main>
    <div aria-label="Telefon"><span>+49 40 12345678</span></div>
    </main></body></html>"""
    result = tool.extract(
        fields=[{"name": "telefon", "type": "phone", "aliases": ["Telefon"]}],
        html=html,
        base_url="https://studio.example/aria-only",
        render="never",
    )
    field = result["fields"][0]
    assert field["status"] == "NEEDS_AGENT"
    assert field["value"] is None


def test_deeply_nested_but_same_component_remains_extractable(tool):
    html = """<html><body><main>
    <div class="card">
      <div class="left"><div><span>Telefon</span></div></div>
      <div class="decor"><span>Kontakt</span></div>
      <div class="right"><div><strong>+49 40 33333333</strong></div></div>
    </div>
    </main></body></html>"""
    result = tool.extract(
        fields=[{"name": "telefon", "type": "phone", "aliases": ["Telefon"]}],
        html=html,
        base_url="https://studio.example/nested",
        render="never",
    )
    field = result["fields"][0]
    assert field["status"] == "FOUND"
    assert field["value"] == "+49 40 33333333"
