import threading

import pytest

from dragonfruitme.recipes import Recipe, RecipeStore


def recipe(field="kaufpreis", pattern="Kaufpreis(.*)"):
    return Recipe(scope="makler.example/expose/*", field=field, kind="regex", pattern=pattern, origin="label")


def test_store_uses_wal_and_persists_across_instances(tmp_path):
    path = tmp_path / "recipes.sqlite"
    first = RecipeStore(path)
    assert first._db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    first.put(recipe())
    first.close()
    second = RecipeStore(path)
    assert second.get("makler.example/expose/*", "kaufpreis").pattern == "Kaufpreis(.*)"
    second.close()


def test_transaction_groups_writes_and_rolls_back_on_error(tmp_path):
    store = RecipeStore(tmp_path / "r.sqlite")
    with pytest.raises(RuntimeError):
        with store.transaction():
            store.put(recipe())
            store.observe("makler.example/expose/*", "kaufpreis", "https://x/1", "1")
            raise RuntimeError("crash mid-page")
    assert store.get("makler.example/expose/*", "kaufpreis") is None  # nothing half-written
    with store.transaction():
        store.put(recipe())
        with store.transaction():  # nested joins the outer transaction
            store.record("makler.example/expose/*", "kaufpreis", True)
    assert store.get("makler.example/expose/*", "kaufpreis").hits == 1


def test_second_store_on_same_file_sees_committed_writes(tmp_path):
    path = tmp_path / "shared.sqlite"
    writer, reader = RecipeStore(path), RecipeStore(path)  # e.g. MCP server and a CLI batch
    with writer.transaction():
        writer.put(recipe())
    assert reader.get("makler.example/expose/*", "kaufpreis") is not None
    writer.close()
    reader.close()


def test_store_is_thread_safe(tmp_path):
    store = RecipeStore(tmp_path / "t.sqlite")
    store.put(recipe())

    def worker():
        for _ in range(50):
            with store.transaction():
                store.record("makler.example/expose/*", "kaufpreis", True)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert store.get("makler.example/expose/*", "kaufpreis").hits == 400
    store.close()
