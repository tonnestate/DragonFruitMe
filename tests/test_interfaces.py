import json
from importlib.resources import files
from pathlib import Path

import pytest
from conftest import expose

from dragonfruitme import cli
from dragonfruitme.binding import state_dir
from dragonfruitme.recipes import scope_of

ROOT = Path(__file__).resolve().parents[1]


def test_cli_extract_from_html_file(tmp_path, capsys):
    page = tmp_path / "expose.html"
    page.write_text(expose(), encoding="utf-8")
    argv = [
        "--state-dir", str(tmp_path / "state"), "extract", "--html-file", str(page),
        "--base-url", "https://makler.example/expose/1",
        "--fields-json", '[{"name":"kaufpreis","type":"price"}]',
    ]
    with pytest.raises(SystemExit) as exit_info:
        cli.main(argv)
    assert exit_info.value.code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["fields"][0]["normalized"] == 349000.0
    with pytest.raises(SystemExit):
        cli.main(["--state-dir", str(tmp_path / "state"), "recipes"])
    assert json.loads(capsys.readouterr().out)["recipes"][0]["field"] == "kaufpreis"


def test_cli_error_exit_code(tmp_path, capsys):
    page = tmp_path / "p.html"
    page.write_text("<p>x</p>", encoding="utf-8")
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["--state-dir", str(tmp_path / "s"), "locate", "--html-file", str(page), "--query", " "])
    assert exit_info.value.code == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "QUERY_REQUIRED"


def test_state_dir_is_host_bound(tmp_path):
    assert state_dir({"DRAGONFRUITME_STATE_DIR": str(tmp_path / "x")}) == (tmp_path / "x").resolve()


def test_scope_generalises_ids_and_slugs():
    assert scope_of("https://www.example.de/expose/123456") == "example.de/expose/*"
    assert scope_of("https://example.de/immobilien/3-zimmer-wohnung-mit-balkon-in-kiel.html") == "example.de/immobilien/*"
    assert scope_of("https://example.de/angebote?page=2") == "example.de/angebote"
    assert scope_of("") == "inline"


def test_packaged_skill_matches_all_mirrors():
    packaged = (files("dragonfruitme") / "SKILL.md").read_text(encoding="utf-8")
    for mirror in ("skill/SKILL.md", "skills/dragonfruitme/SKILL.md"):
        assert (ROOT / mirror).read_text(encoding="utf-8") == packaged


def test_repository_layout_is_shallow():
    package = ROOT / "src" / "dragonfruitme"
    assert [p for p in package.iterdir() if p.is_dir() and p.name != "__pycache__"] == []
    ignored = {".git", ".github", ".pytest_cache", "__pycache__", "build", "dist"}
    for path in ROOT.rglob("*"):
        rel = path.relative_to(ROOT)
        if any(part in ignored or part.endswith(".egg-info") for part in rel.parts):
            continue
        if path.is_file():
            assert len(rel.parts) <= 3, f"too deep: {rel}"


def test_mcp_server_imports_when_extra_installed(tmp_path, monkeypatch):
    pytest.importorskip("mcp")
    monkeypatch.setenv("DRAGONFRUITME_STATE_DIR", str(tmp_path / "mcp-state"))
    import importlib

    module = importlib.import_module("dragonfruitme.mcp_server")
    result = module._tool.observe(html=expose(), base_url="https://makler.example/expose/1")
    assert result["ok"] and result["summary"]["h1"] == ["3-Zimmer-Wohnung in Kiel"]
