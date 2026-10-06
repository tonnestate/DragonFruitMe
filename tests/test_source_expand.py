import json

import pytest

from dragonfruitme import cli


def _listing(ids, *, next_page=None, last_page=None):
    links = "".join(f'<a href="/studio/{value}">Studio {value}</a>' for value in ids)
    nav = ""
    if next_page is not None:
        nav += f'<a href="/studios?page={next_page}" rel="next">Weiter</a>'
    if last_page is not None:
        nav += f'<a href="/studios?page={last_page}" rel="last">Letzte</a>'
    return f"<html><body><main>{links}</main><nav>{nav}</nav></body></html>"


def test_expand_source_exhausts_bounded_pagination_without_fetching_details(site, make_tool):
    site.route("/studios?page=1", _listing([1001, 1002], next_page=2, last_page=3))
    site.route("/studios?page=2", _listing([1003, 1004], next_page=3, last_page=3))
    site.route("/studios?page=3", _listing([1005, 1006]))

    with make_tool() as tool:
        result = tool.expand_source(
            seed_urls=[f"{site.base}/studios?page=1"],
            max_pages=10,
            max_urls=100,
            max_depth=2,
        )

    assert result["ok"] is True
    assert result["frontier_exhausted"] is True
    assert result["source_pages_fetched"] == 3
    assert result["detail_count"] == 6
    assert result["fanout_per_source_page"] == 2.0
    assert result["detail_urls"] == [
        f"{site.base}/studio/1001",
        f"{site.base}/studio/1002",
        f"{site.base}/studio/1003",
        f"{site.base}/studio/1004",
        f"{site.base}/studio/1005",
        f"{site.base}/studio/1006",
    ]
    assert not any(path.startswith("/studio/") for path in site.hits)


def test_expand_source_stops_at_host_owned_page_budget(site, make_tool):
    site.route("/studios?page=1", _listing([1001, 1002], next_page=2, last_page=4))
    site.route("/studios?page=2", _listing([1003, 1004], next_page=3, last_page=4))
    site.route("/studios?page=3", _listing([1005, 1006], next_page=4, last_page=4))
    site.route("/studios?page=4", _listing([1007, 1008]))

    with make_tool() as tool:
        result = tool.expand_source(
            seed_urls=[f"{site.base}/studios?page=1"],
            max_pages=2,
            max_urls=100,
            max_depth=2,
        )

    assert result["frontier_exhausted"] is False
    assert result["limited_by"] == "max_pages"
    assert result["source_pages_fetched"] == 2
    assert result["detail_count"] == 4
    assert result["remaining_source_pages"] == 0


def test_expand_source_deduplicates_detail_urls_across_listing_pages(site, make_tool):
    site.route("/studios?page=1", _listing([1001, 1002], next_page=2, last_page=2))
    site.route("/studios?page=2", _listing([1002, 1003]))

    with make_tool() as tool:
        result = tool.expand_source(
            seed_urls=[f"{site.base}/studios?page=1"],
            max_pages=10,
            max_urls=100,
        )

    assert result["detail_count"] == 3
    assert result["detail_urls"] == [
        f"{site.base}/studio/1001",
        f"{site.base}/studio/1002",
        f"{site.base}/studio/1003",
    ]


def test_expand_source_cli_materializes_detail_queue(site, tmp_path, capsys):
    site.route("/studios?page=1", _listing([1001, 1002], next_page=2, last_page=2))
    site.route("/studios?page=2", _listing([1003, 1004]))

    seeds = tmp_path / "sources.txt"
    seeds.write_text(f"{site.base}/studios?page=1\n", encoding="utf-8")
    out = tmp_path / "details.txt"

    with pytest.raises(SystemExit) as exit_info:
        cli.main([
            "--state-dir", str(tmp_path / "state"),
            "--min-interval", "0",
            "expand-source",
            "--urls-file", str(seeds),
            "--output", str(out),
            "--max-pages", "10",
            "--max-urls", "100",
        ])

    assert exit_info.value.code == 0
    assert out.read_text(encoding="utf-8").splitlines() == [
        f"{site.base}/studio/1001",
        f"{site.base}/studio/1002",
        f"{site.base}/studio/1003",
        f"{site.base}/studio/1004",
    ]
    summary = json.loads(capsys.readouterr().err)
    assert summary["detail_count"] == 4
    assert summary["frontier_exhausted"] is True


def test_expand_source_caps_absurd_terminal_page_before_queue_growth(site, make_tool):
    site.route("/studios?page=1", _listing([1001, 1002], next_page=2, last_page=1_000_000_000))
    site.route("/studios?page=2", _listing([1003, 1004]))
    site.route("/studios?page=3", _listing([1005, 1006]))

    with make_tool() as tool:
        result = tool.expand_source(
            seed_urls=[f"{site.base}/studios?page=1"],
            max_pages=3,
            max_urls=100,
            max_depth=2,
        )

    assert result["source_pages_fetched"] == 3
    assert result["limited_by"] == "max_pages"
    assert result["frontier_exhausted"] is False
    assert result["detail_count"] == 6
    assert set(site.hits) == {
        "/robots.txt",
        "/studios?page=1",
        "/studios?page=2",
        "/studios?page=3",
    }
