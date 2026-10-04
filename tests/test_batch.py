import json

import pytest

from dragonfruitme import cli


class FakeTool:
    def __init__(self):
        self.calls = []

    def extract(self, *, fields, url, render, learn):
        self.calls.append(url)
        if "bad" in url:
            return {"ok": False, "error": {"code": "FETCH_FAILED"}}
        return {
            "ok": True,
            "status": "COMPLETE",
            "scope": "example.org/*",
            "fields": [{"name": fields[0]["name"], "status": "FOUND", "value": url}],
            "pages": [],
            "found": 1,
            "total": 1,
        }


def test_extract_batch_deduplicates_flushes_and_continues(tmp_path, monkeypatch, capsys):
    urls = tmp_path / "urls.txt"
    urls.write_text(
        "# explicit queue\nhttps://example.org/a\nhttps://example.org/a\n"
        "https://bad.example/x\nhttps://example.org/b\n",
        encoding="utf-8",
    )
    out = tmp_path / "out.jsonl"
    fake = FakeTool()
    monkeypatch.setattr(cli, "DragonFruitMe", lambda **kwargs: fake)

    with pytest.raises(SystemExit) as exit_info:
        cli.main([
            "extract-batch",
            "--urls-file", str(urls),
            "--fields-json", '[{"name":"phone","type":"phone"}]',
            "--output", str(out),
            "--render", "never",
        ])

    assert exit_info.value.code == 0
    assert fake.calls == [
        "https://example.org/a",
        "https://bad.example/x",
        "https://example.org/b",
    ]
    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert [row["url"] for row in rows] == fake.calls
    assert rows[1]["ok"] is False
    summary = json.loads(capsys.readouterr().err)
    assert summary["batch"] == {
        "total": 3,
        "skipped": 0,
        "attempted": 3,
        "complete": 2,
        "partial": 0,
        "incomplete": 0,
        "errors": 1,
    }


def test_extract_batch_resume_skips_durable_rows(tmp_path, monkeypatch, capsys):
    urls = tmp_path / "urls.json"
    urls.write_text(json.dumps([
        "https://example.org/a",
        "https://example.org/b",
        "https://example.org/c",
    ]), encoding="utf-8")
    out = tmp_path / "out.jsonl"
    out.write_text(
        json.dumps({"url": "https://example.org/a", "ok": True, "status": "COMPLETE"}) + "\n",
        encoding="utf-8",
    )
    fake = FakeTool()
    monkeypatch.setattr(cli, "DragonFruitMe", lambda **kwargs: fake)

    with pytest.raises(SystemExit) as exit_info:
        cli.main([
            "extract-batch",
            "--urls-file", str(urls),
            "--fields-json", '[{"name":"email","type":"email"}]',
            "--output", str(out),
            "--resume",
        ])

    assert exit_info.value.code == 0
    assert fake.calls == ["https://example.org/b", "https://example.org/c"]
    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert [row["url"] for row in rows] == [
        "https://example.org/a",
        "https://example.org/b",
        "https://example.org/c",
    ]
    summary = json.loads(capsys.readouterr().err)
    assert summary["batch"]["skipped"] == 1
    assert summary["batch"]["attempted"] == 2


def test_extract_batch_fail_on_error_is_opt_in(tmp_path, monkeypatch, capsys):
    urls = tmp_path / "urls.txt"
    urls.write_text("https://bad.example/x\n", encoding="utf-8")
    fake = FakeTool()
    monkeypatch.setattr(cli, "DragonFruitMe", lambda **kwargs: fake)

    with pytest.raises(SystemExit) as exit_info:
        cli.main([
            "extract-batch",
            "--urls-file", str(urls),
            "--fields-json", '[{"name":"phone","type":"phone"}]',
            "--fail-on-error",
        ])

    assert exit_info.value.code == 2
    captured = capsys.readouterr()
    assert json.loads(captured.out)["url"] == "https://bad.example/x"
    assert json.loads(captured.err)["batch"]["errors"] == 1
