def test_structural_label_finds_typed_value_across_component_wrappers(tool):
    html = """<html><body><main>
    <div class="contact-card">
      <div class="field-label"><span>Telefon</span></div>
      <div class="field-help"><small>Direkter Ansprechpartner</small></div>
      <div class="field-value"><strong>+49 40 12345678</strong></div>
    </div>
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


def test_structural_label_supports_css_heavy_email_layout(tool):
    html = """<html><body><main>
    <section class="profile">
      <div class="property">
        <div class="caption"><span>E-Mail</span></div>
        <div class="icon-slot">Kontakt</div>
        <div class="content"><span><strong>studio@example.de</strong></span></div>
      </div>
    </section>
    </main></body></html>"""
    result = tool.extract(
        fields=[{"name": "email", "type": "email", "aliases": ["E-Mail"]}],
        html=html,
        base_url="https://studio.example/profil/1",
        render="never",
    )
    field = result["fields"][0]
    assert field["status"] == "FOUND"
    assert field["stage"] == "label"
    assert field["value"] == "studio@example.de"


def test_structural_label_does_not_cross_component_boundary(tool):
    html = """<html><body><main>
    <div class="card">
      <div class="label"><span>Telefon</span></div>
      <div class="hint">Keine Telefonnummer veröffentlicht</div>
    </div>
    <div class="card">
      <div class="title">Anderes Studio</div>
      <div class="value">+49 40 99999999</div>
    </div>
    </main></body></html>"""
    result = tool.extract(
        fields=[{"name": "telefon", "type": "phone", "aliases": ["Telefon"]}],
        html=html,
        base_url="https://directory.example/list/1",
        render="never",
    )
    field = result["fields"][0]
    assert field["status"] == "NEEDS_AGENT"
    assert field["value"] is None


def test_direct_adjacent_label_behavior_remains_preferred(tool):
    html = """<html><body><main>
    <dl><dt>Telefon</dt><dd>+49 40 11111111</dd></dl>
    </main></body></html>"""
    result = tool.extract(
        fields=[{"name": "telefon", "type": "phone", "aliases": ["Telefon"]}],
        html=html,
        base_url="https://studio.example/direct/1",
        render="never",
    )
    field = result["fields"][0]
    assert field["status"] == "FOUND"
    assert field["stage"] == "label"
    assert field["value"] == "+49 40 11111111"
