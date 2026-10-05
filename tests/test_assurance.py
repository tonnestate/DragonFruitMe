"""Provenance levels, recipe contradiction and repetition signals."""
from conftest import expose

BASE = "https://www.makler.example/expose/{id}"


def field(result, name=None):
    fields = result["fields"]
    return fields[0] if name is None else next(f for f in fields if f["name"] == name)


def test_provenance_levels(tool):
    both = tool.extract(fields=[{"name": "kaufpreis", "type": "price", "paths": ["offers.price"]}],
                        html=expose(), base_url=BASE.format(id="1"))
    price = field(both)
    assert price["stage"] == "structured"
    assert price["provenance"] == "CROSS_CONFIRMED" and price["confirmed_by"] == ["structured", "label"]

    label_only = tool.extract(fields=[{"name": "baujahr", "type": "integer"}], html=expose(), base_url=BASE.format(id="1"))
    assert field(label_only)["provenance"] == "ENGINE_OBSERVED"

    html = expose().replace("<p>Diese", "<p>Ausstattung mit Fußbodenheizung.</p><p>Diese")
    taught = tool.extract(fields=[{"name": "ausstattung", "teach": "Fußbodenheizung"}], html=html,
                          base_url=BASE.format(id="1"))
    assert field(taught)["provenance"] == "AGENT_TAUGHT"


def test_min_provenance_gates_agent_claims(tool):
    html = expose().replace("<p>Diese", "<p>Ausstattung mit Fußbodenheizung.</p><p>Diese")
    result = tool.extract(fields=[{"name": "ausstattung", "teach": "Fußbodenheizung", "min_provenance": "engine_observed"}],
                          html=html, base_url=BASE.format(id="1"))
    f = field(result)
    assert f["status"] == "UNCONFIRMED" and f["required_provenance"] == "ENGINE_OBSERVED"
    assert f["value"] == "Fußbodenheizung"  # visible to the agent, but not counted as found
    assert result["status"] == "INCOMPLETE" and result["found"] == 0
    bad = tool.extract(fields=[{"name": "x", "min_provenance": "SURE"}], html="<p>x</p>")
    assert bad["error"]["code"] == "FIELD_INVALID"


def test_contradicted_primary_is_discarded_and_proven_fallback_answers(tool):
    # page 1: markup-anchored recipe (no usable label for the value's container)
    page1 = """<html><body><main><div class="teaser"><span class="v">199.000 €</span></div>
    <h2>Objekt</h2><dl><dt>Kaufpreis</dt><dd>349.000 €</dd></dl></main></body></html>"""
    first = tool.extract(fields=[{"name": "kaufpreis", "type": "price"}], html=page1, base_url=BASE.format(id="1"))
    assert field(first)["normalized"] == 349000.0
    # simulate a recipe that points at the teaser (wrong anchor learned elsewhere)
    from dragonfruitme.recipes import Recipe

    tool.engine.store.put(Recipe(scope="makler.example/expose/*", field="kaufpreis", kind="regex",
                                 pattern=r'<span class="v">\s*([^<]+?)(?=\s*<)', origin="label"))
    page2 = page1.replace("349.000", "415.000").replace("199.000", "205.000")
    second = tool.extract(fields=[{"name": "kaufpreis", "type": "price"}], html=page2, base_url=BASE.format(id="2"))
    f = field(second)
    assert f["normalized"] == 415000.0
    recipe_attempt = f["attempts"][0]
    assert recipe_attempt["outcome"] == "MISS" and recipe_attempt["reason"] == "CONTRADICTED"
    assert recipe_attempt["recipe_value"] == "205.000 €" and recipe_attempt["role"] == "primary"
    # the displaced recipe from page 1 was kept as a fallback and answers without re-escalation
    assert f["stage"] == "recipe" and f["attempts"][1] == {
        "stage": "recipe", "page": "inline", "outcome": "HIT", "kind": "regex", "role": "fallback"}
    assert {"code": "RECIPE_FALLBACK", "role": "fallback", "kind": "regex", "promoted": False} in f["signals"]


def test_contradicted_recipe_without_alternatives_escalates_and_relearns(tool):
    from dragonfruitme.recipes import Recipe

    tool.engine.store.put(Recipe(scope="makler.example/expose/*", field="kaufpreis", kind="regex",
                                 pattern=r'<span class="v">\s*([^<]+?)(?=\s*<)', origin="label"))
    page = """<html><body><main><div class="teaser"><span class="v">205.000 €</span></div>
    <h2>Objekt</h2><dl><dt>Kaufpreis</dt><dd>415.000 €</dd></dl></main></body></html>"""
    f = field(tool.extract(fields=[{"name": "kaufpreis", "type": "price"}], html=page, base_url=BASE.format(id="2")))
    assert f["normalized"] == 415000.0 and f["stage"] == "label"
    assert f["attempts"][0]["reason"] == "CONTRADICTED"
    assert f["recipe"]["compiled"] and f["recipe"]["role"] == "candidate"  # relearned, waits to prove itself


def test_repeated_unconfirmed_value_is_flagged(tool):
    from dragonfruitme.recipes import Recipe

    # a recipe nobody can confirm or contradict, anchored on a constant teaser
    tool.engine.store.put(Recipe(scope="makler.example/expose/*", field="highlight", kind="regex",
                                 pattern=r'<span class="teaser">\s*([^<]+?)(?=\s*<)', origin="label"))
    signals = []
    for i in range(1, 5):
        html = f'<html><body><span class="teaser">Top-Angebot der Woche</span><p>Objekt {i}</p></body></html>'
        result = tool.extract(fields=["highlight"], html=html, base_url=BASE.format(id=str(i)))
        signals.append([s["code"] for s in field(result)["signals"]])
    assert signals == [[], [], ["REPEATED_VALUE"], ["REPEATED_VALUE"]]


def test_confirmed_constant_is_not_flagged(tool):
    # control: the agency e-mail is the same on every exposé and confirmed by its label each time
    for i in range(1, 6):
        result = tool.extract(fields=[{"name": "email", "type": "email", "aliases": ["E-Mail"]}],
                              html=expose(id=str(i), price=f"{300 + i}.000 €"), base_url=BASE.format(id=str(i)))
        f = field(result)
        assert f["status"] == "FOUND" and f["signals"] == []


def test_re_extracting_the_same_url_is_not_repetition(tool):
    from dragonfruitme.recipes import Recipe

    tool.engine.store.put(Recipe(scope="makler.example/expose/*", field="highlight", kind="regex",
                                 pattern=r'<span class="teaser">\s*([^<]+?)(?=\s*<)', origin="label"))
    html = '<html><body><span class="teaser">Top-Angebot</span></body></html>'
    for _ in range(5):
        result = tool.extract(fields=["highlight"], html=html, base_url=BASE.format(id="1"))
    assert field(result)["signals"] == []
