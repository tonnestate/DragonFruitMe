"""Recipe portfolio: primary + alternatives, promotion only after proof."""
import sqlite3
import time

from conftest import expose

from dragonfruitme.recipes import MAX_LIVE, Recipe, RecipeStore

BASE = "https://www.makler.example/expose/{}"
SCOPE = "makler.example/expose/*"
PRICE = [{"name": "kaufpreis", "type": "price"}]
# A project/new-build exposé on the same URL template with a different layout
OUTLIER = """<html><body><main><h1>Neubauprojekt</h1>
<div class="project-box"><span class="lbl">Kaufpreis ab:</span> <b>{price} EUR</b></div></main></body></html>"""


def price_field(tool, html, n):
    return tool.extract(fields=PRICE, html=html, base_url=BASE.format(n))["fields"][0]


def roles(tool):
    return sorted((r["role"], r["hits"]) for r in tool.recipes(scope=SCOPE)["recipes"])


def test_healthy_primary_runs_alone(tool):
    price_field(tool, expose(id="1"), 1)
    f = price_field(tool, expose(id="2", price="199.000 €"), 2)
    assert [(a["stage"], a["outcome"], a["role"]) for a in f["attempts"]] == [("recipe", "HIT", "primary")]
    assert f["signals"] == []


def test_outlier_page_does_not_overwrite_proven_primary(tool):
    for n in range(1, 6):
        price_field(tool, expose(id=str(n), price=f"{300 + n}.000 €"), n)
    outlier = price_field(tool, OUTLIER.format(price="499.000"), 6)
    assert outlier["normalized"] == 499000.0 and outlier["recipe"]["role"] == "candidate"
    # the next normal exposé is still answered by the untouched primary
    normal = price_field(tool, expose(id="7", price="320.000 €"), 7)
    assert normal["attempts"] == [{"stage": "recipe", "page": "inline", "outcome": "HIT", "kind": "regex", "role": "primary"}]
    # the next outlier is answered by the alternative - no escalation, but visible
    again = price_field(tool, OUTLIER.format(price="525.000"), 8)
    assert again["stage"] == "recipe" and again["normalized"] == 525000.0
    assert [a["outcome"] for a in again["attempts"]] == ["MISS", "HIT"]
    assert {"code": "RECIPE_FALLBACK", "role": "candidate", "kind": "regex", "promoted": False} in again["signals"]
    primary = [r for r in tool.recipes(scope=SCOPE)["recipes"] if r["role"] == "primary"][0]
    assert primary["hits"] == 6 and primary["misses"] == 1  # one miss in a row: not stale, not replaced
    assert ("fallback", 2) in roles(tool)


def test_redesign_promotes_the_new_recipe_after_proof(tool):
    price_field(tool, expose(id="1"), 1)
    redesign = OUTLIER  # the whole template switches to the new layout
    first = price_field(tool, redesign.format(price="410.000"), 2)
    assert first["stage"] == "label" and first["recipe"]["role"] == "candidate"
    second = price_field(tool, redesign.format(price="420.000"), 3)
    assert second["stage"] == "recipe" and second["attempts"][-1].get("promoted") is True
    assert {"code": "RECIPE_FALLBACK", "role": "candidate", "kind": "regex", "promoted": True} in second["signals"]
    third = price_field(tool, redesign.format(price="430.000"), 4)
    assert third["attempts"] == [{"stage": "recipe", "page": "inline", "outcome": "HIT", "kind": "regex", "role": "primary"}]
    # the old primary is demoted, not deleted: it can answer again if the old layout returns
    assert [r for r in roles(tool) if r[0] == "fallback"]


def test_portfolio_is_capped_and_retires_the_weakest(tmp_path):
    store = RecipeStore(tmp_path / "r.sqlite")
    for i in range(MAX_LIVE + 2):
        store.add(Recipe(scope=SCOPE, field="f", kind="regex", pattern=f"p{i}(.*)", origin="label"))
    live = store.recipe_set(SCOPE, "f")
    assert len(live) == MAX_LIVE and live[0].role == "primary" and live[0].pattern == "p0(.*)"
    retired = [r for r in store.list(SCOPE) if r.role == "retired"]
    assert len(retired) == 2
    store.close()


def test_alternatives_are_ranked_by_evidence_not_insertion(tmp_path):
    store = RecipeStore(tmp_path / "r.sqlite")
    store.add(Recipe(scope=SCOPE, field="f", kind="regex", pattern="primary(.*)", origin="label"))
    weak, _ = store.add(Recipe(scope=SCOPE, field="f", kind="regex", pattern="weak(.*)", origin="label"))
    strong, _ = store.add(Recipe(scope=SCOPE, field="f", kind="label", pattern="Preis", origin="label"))
    with store.transaction():
        store.record_miss(weak.id, contradicted=True)
        for _ in range(3):
            store.record_hit(strong.id)
    assert [r.pattern for r in store.recipe_set(SCOPE, "f")] == ["primary(.*)", "Preis", "weak(.*)"]
    store.close()


def test_legacy_single_recipe_database_is_migrated(tmp_path):
    path = tmp_path / "recipes.sqlite"
    legacy = sqlite3.connect(path)
    legacy.execute(
        """CREATE TABLE recipes (scope TEXT NOT NULL, field TEXT NOT NULL, kind TEXT NOT NULL, pattern TEXT NOT NULL,
           origin TEXT NOT NULL, hits INTEGER NOT NULL DEFAULT 0, misses INTEGER NOT NULL DEFAULT 0,
           status TEXT NOT NULL DEFAULT 'active', created_at REAL NOT NULL, updated_at REAL NOT NULL,
           PRIMARY KEY (scope, field))"""
    )
    now = time.time()
    legacy.execute("INSERT INTO recipes VALUES (?,?,?,?,?,?,?,?,?,?)",
                   (SCOPE, "kaufpreis", "json", "jsonld|offers.price", "structured", 512, 0, "active", now, now))
    legacy.commit()
    legacy.close()
    store = RecipeStore(path)
    primary = store.get(SCOPE, "kaufpreis")
    assert primary.role == "primary" and primary.hits == 512 and primary.pattern == "jsonld|offers.price"
    assert store._db.execute("PRAGMA user_version").fetchone()[0] == 3
    store.close()
    RecipeStore(path).close()  # re-opening an already migrated store is a no-op


def test_live_lookup_uses_the_bucket_index(tmp_path):
    from dragonfruitme.recipes import _LIVE_SQL

    store = RecipeStore(tmp_path / "r.sqlite")
    plan = store._db.execute(
        f"EXPLAIN QUERY PLAN SELECT * FROM recipe_set WHERE scope=? AND field=? AND {_LIVE_SQL}", ("s", "f")
    ).fetchall()
    assert "USING INDEX recipe_set_bucket" in plan[0][3]  # no table scan, no scan over retired rows
    store.close()


def test_retired_history_is_bounded(tmp_path):
    from dragonfruitme.recipes import RETIRED_KEEP

    store = RecipeStore(tmp_path / "r.sqlite")
    for i in range(200):  # a field that is relearned over and over
        store.add(Recipe(scope=SCOPE, field="f", kind="regex", pattern=f"p{i}(.*)", origin="label"))
    rows = store.list(SCOPE)
    assert len([r for r in rows if r.role == "retired"]) == RETIRED_KEEP
    assert len([r for r in rows if r.role != "retired"]) == MAX_LIVE
    store.close()


def test_v2_store_is_upgraded_to_bucket_index_and_trimmed(tmp_path):
    from dragonfruitme.recipes import RETIRED_KEEP

    path = tmp_path / "v2.sqlite"
    store = RecipeStore(path)
    db = store._db
    now = time.time()
    with store.transaction():  # simulate a 0.4.0 store: old index, unbounded retired rows
        db.execute("DROP INDEX recipe_set_bucket")
        db.execute("CREATE INDEX recipe_set_scope_field ON recipe_set (scope, field)")
        db.executemany(
            "INSERT INTO recipe_set (scope, field, kind, pattern, origin, role, hits, misses, total_misses, "
            "contradictions, confirmed_hits, last_validated, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,0,0,0,0,0,NULL,?,?)",
            [(SCOPE, "f", "regex", f"old{i}(.*)", "label", "retired", now, now + i) for i in range(100)],
        )
        db.execute("PRAGMA user_version=2")
    store.close()
    upgraded = RecipeStore(path)
    indexes = {row[1] for row in upgraded._db.execute("PRAGMA index_list(recipe_set)")}
    assert "recipe_set_bucket" in indexes and "recipe_set_scope_field" not in indexes
    retired = [r for r in upgraded.list(SCOPE) if r.role == "retired"]
    assert len(retired) == RETIRED_KEEP and retired[0].pattern.startswith("old")
    assert upgraded._db.execute("PRAGMA user_version").fetchone()[0] == 3
    upgraded.close()
