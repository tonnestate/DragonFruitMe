from conftest import expose

BASE = "https://www.makler.example/expose/{id}"
FIELDS = [
    {"name": "kaufpreis", "type": "price"},
    {"name": "wohnflaeche", "type": "area", "aliases": ["Wohnfläche"]},
    {"name": "baujahr", "type": "integer", "min": 1800, "max": 2030},
    {"name": "email", "type": "email", "aliases": ["E-Mail"]},
    {"name": "energie", "aliases": ["Energieträger"]},
]


def by_name(result):
    return {f["name"]: f for f in result["fields"]}


def stages(field):
    return [(a["stage"], a["outcome"]) for a in field["attempts"]]


def test_first_page_escalates_and_compiles_label_anchored_recipes(tool):
    result = tool.extract(fields=FIELDS, html=expose(), base_url=BASE.format(id="4711"))
    assert result["ok"] and result["status"] == "COMPLETE"
    assert result["scope"] == "makler.example/expose/*"
    f = by_name(result)
    assert f["kaufpreis"]["normalized"] == 349000.0
    assert f["wohnflaeche"]["normalized"] == 78.5
    assert f["baujahr"]["normalized"] == 1998
    assert f["email"]["value"] == "max@example.de"
    assert f["energie"]["value"] == "Gas"
    price = f["kaufpreis"]
    assert price["stage"] == "label"
    assert stages(price) == [("recipe", "SKIP"), ("structured", "MISS"), ("label", "HIT")]
    assert price["recipe"]["kind"] == "regex" and price["recipe"]["compiled"]
    assert price["recipe"]["pattern"].startswith("Kaufpreis")  # anchored at the visible label


def test_second_page_of_same_template_is_served_by_recipes(tool):
    tool.extract(fields=FIELDS, html=expose(), base_url=BASE.format(id="4711"))
    page2 = expose(id="98", price="1.250.000 €", area="112 m²", year="2011", ld_price="1250000")
    result = tool.extract(fields=FIELDS, html=page2, base_url=BASE.format(id="98"))
    f = by_name(result)
    assert all(field["stage"] == "recipe" for field in result["fields"])
    assert f["kaufpreis"]["normalized"] == 1250000.0
    assert f["wohnflaeche"]["normalized"] == 112.0
    assert f["baujahr"]["normalized"] == 2011
    assert stages(f["kaufpreis"]) == [("recipe", "HIT")]
    recipes = {r["field"]: r for r in tool.recipes()["recipes"]}
    assert recipes["kaufpreis"]["hits"] == 1


def test_recipe_drift_escalates_and_self_heals(tool):
    tool.extract(fields=FIELDS[:1], html=expose(), base_url=BASE.format(id="1"))
    redesigned = """<html><body><main><h2>Preise</h2>
    <div class="row"><span class="lbl">Kaufpreis:</span> <b>415.000 EUR</b></div></main></body></html>"""
    result = tool.extract(fields=FIELDS[:1], html=redesigned, base_url=BASE.format(id="2"))
    price = result["fields"][0]
    assert price["status"] == "FOUND" and price["normalized"] == 415000.0
    assert stages(price)[0] == ("recipe", "MISS")
    assert price["stage"] == "label" and price["recipe"]["compiled"]
    again = tool.extract(fields=FIELDS[:1], html=redesigned.replace("415.000", "420.000"), base_url=BASE.format(id="3"))
    assert again["fields"][0]["stage"] == "recipe"
    assert again["fields"][0]["normalized"] == 420000.0


def test_structured_paths_win_and_compile_json_recipes(tool):
    fields = [{"name": "preis", "type": "price", "paths": ["offers.price"]}]
    result = tool.extract(fields=fields, html=expose(), base_url=BASE.format(id="1"))
    preis = result["fields"][0]
    assert preis["stage"] == "structured" and preis["normalized"] == 349000.0
    assert preis["recipe"] == {"kind": "json", "pattern": "jsonld|offers.price", "scope": "makler.example/expose/*", "compiled": True}
    page2 = expose(ld_price="199000", price="199.000 €")
    assert tool.extract(fields=fields, html=page2, base_url=BASE.format(id="2"))["fields"][0]["normalized"] == 199000.0


def test_missing_field_returns_bounded_candidates_for_the_agent(tool):
    result = tool.extract(fields=[{"name": "heizung", "aliases": ["Heizungsart"]}, "zimmer"], html=expose(),
                          base_url=BASE.format(id="1"))
    assert result["status"] == "PARTIAL"
    heizung, zimmer = result["fields"]
    assert zimmer["status"] == "FOUND" and zimmer["value"] == "3"
    assert heizung["status"] == "NEEDS_AGENT"
    assert stages(heizung)[-1] == ("agent", "NEEDS_AGENT")
    assert ("render", "SKIP") in stages(heizung)
    assert "teach" in heizung["next"]


def test_teach_is_grounded_compiled_and_reused(tool):
    html = expose().replace("<p>Diese", "<p>Heizung: Fernwärme, modernisiert 2019.</p><p>Diese")
    taught = tool.extract(fields=[{"name": "heizung", "teach": "Fernwärme"}], html=html, base_url=BASE.format(id="1"))
    field = taught["fields"][0]
    assert field["status"] == "FOUND" and field["stage"] == "taught"
    assert field["recipe"]["compiled"]
    reused = tool.extract(fields=["heizung"], html=html.replace("4711", "5"), base_url=BASE.format(id="5"))
    assert reused["fields"][0]["stage"] == "recipe"
    assert reused["fields"][0]["value"].startswith("Fernwärme")


def test_teach_rejects_values_not_in_the_page(tool):
    result = tool.extract(fields=[{"name": "heizung", "teach": "Wärmepumpe"}], html=expose(), base_url=BASE.format(id="1"))
    field = result["fields"][0]
    assert field["status"] == "NOT_GROUNDED"
    assert tool.recipes()["recipes"] == []


def test_scopes_keep_templates_apart_and_learn_false_stores_nothing(tool):
    tool.extract(fields=FIELDS[:1], html=expose(), base_url="https://makler.example/angebote", learn=False)
    assert tool.recipes()["recipes"] == []
    tool.extract(fields=FIELDS[:1], html=expose(), base_url="https://makler.example/angebote")
    tool.extract(fields=FIELDS[:1], html=expose(), base_url=BASE.format(id="7"))
    scopes = sorted(r["scope"] for r in tool.recipes()["recipes"])
    assert scopes == ["makler.example/angebote", "makler.example/expose/*"]
    assert tool.forget(scope="makler.example/angebote")["removed"] == 1


def test_field_errors_are_machine_readable(tool):
    assert tool.extract(fields=[], html="<p>x</p>")["error"]["code"] == "FIELDS_REQUIRED"
    assert tool.extract(fields=[{"name": "a", "type": "money"}], html="<p>x</p>")["error"]["code"] == "FIELD_TYPE_UNKNOWN"
    assert tool.extract(fields=["a", "a"], html="<p>x</p>")["error"]["code"] == "FIELD_DUPLICATE"
    assert tool.extract(fields=["a"])["error"]["code"] == "SOURCE_REQUIRED"
    assert tool.extract(fields=["a"], html="<p>x</p>", render="always")["error"]["code"] == "RENDER_MODE_INVALID"
