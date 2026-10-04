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


def test_extract_batch_resume_repairs_torn_last_line(tmp_path, monkeypatch, capsys):
    urls = tmp_path / "urls.txt"
    urls.write_text("https://example.org/a\nhttps://example.org/b\n", encoding="utf-8")
    out = tmp_path / "out.jsonl"
    # process was killed while writing the row for /b: no closing brace, no newline
    out.write_text(
        json.dumps({"url": "https://example.org/a", "ok": True, "status": "COMPLETE"}) + "\n"
        + '{"url":"https://example.org/b","ok":tr',
        encoding="utf-8",
    )
    fake = FakeTool()
    monkeypatch.setattr(cli, "DragonFruitMe", lambda **kwargs: fake)

    with pytest.raises(SystemExit):
        cli.main(["extract-batch", "--urls-file", str(urls), "--fields-json", '[{"name":"email"}]',
                  "--output", str(out), "--resume"])

    assert fake.calls == ["https://example.org/b"]
    lines = out.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3
    assert json.loads(lines[2])["url"] == "https://example.org/b"  # new row is intact, not glued to the torn one
    capsys.readouterr()


def test_extract_batch_resume_retries_transient_but_not_final_failures(tmp_path, monkeypatch, capsys):
    urls = tmp_path / "urls.txt"
    urls.write_text("https://example.org/flaky\nhttps://example.org/blocked\nhttps://example.org/fixed\n",
                    encoding="utf-8")
    out = tmp_path / "out.jsonl"
    rows = [
        {"url": "https://example.org/flaky", "ok": False, "error": {"code": "FETCH_FAILED", "recoverable": True}},
        {"url": "https://example.org/blocked", "ok": False, "error": {"code": "BLOCKED", "recoverable": False}},
        {"url": "https://example.org/fixed", "ok": False, "error": {"code": "HTTP_ERROR", "recoverable": True}},
        {"url": "https://example.org/fixed", "ok": True, "status": "COMPLETE"},  # latest row wins
    ]
    out.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    fake = FakeTool()
    monkeypatch.setattr(cli, "DragonFruitMe", lambda **kwargs: fake)

    with pytest.raises(SystemExit):
        cli.main(["extract-batch", "--urls-file", str(urls), "--fields-json", '[{"name":"email"}]',
                  "--output", str(out), "--resume"])

    assert fake.calls == ["https://example.org/flaky"]
    summary = json.loads(capsys.readouterr().err)
    assert summary["batch"]["skipped"] == 2 and summary["batch"]["attempted"] == 1


def test_extract_batch_end_to_end_reuses_recipes_across_urls(tmp_path, site, capsys):
    from conftest import expose

    site.route("/expose/1", expose(id="1"))
    site.route("/expose/2", expose(id="2", price="1.250.000 €"))
    urls = tmp_path / "urls.txt"
    urls.write_text(f"{site.base}/expose/1\n{site.base}/expose/2\n", encoding="utf-8")
    out = tmp_path / "out.jsonl"

    with pytest.raises(SystemExit) as exit_info:
        cli.main(["--state-dir", str(tmp_path / "state"), "--min-interval", "0", "extract-batch",
                  "--urls-file", str(urls), "--fields-json", '[{"name":"kaufpreis","type":"price"}]',
                  "--output", str(out), "--render", "never"])

    assert exit_info.value.code == 0
    first, second = [json.loads(line)["fields"][0] for line in out.read_text(encoding="utf-8").splitlines()]
    assert first["stage"] == "label" and first["normalized"] == 349000.0
    assert second["stage"] == "recipe" and second["normalized"] == 1250000.0
    capsys.readouterr()
