from __future__ import annotations

from types import SimpleNamespace

import pytest

from dragonfruitme.core import DragonFruitMe
from dragonfruitme.donors import ScrapeGraphApiDonor, ScrapeGraphOssDonor, donor_from_env
from dragonfruitme.fetch import Fetcher
from dragonfruitme.policy import FetchPolicy


class _Response:
    status = 200
    code = 200

    def __init__(self, body: str, url: str):
        self._body = body.encode("utf-8")
        self._url = url
        self.headers = {"Content-Type": "text/html; charset=utf-8"}

    def read(self, _limit: int) -> bytes:
        return self._body

    def geturl(self) -> str:
        return self._url

    def close(self) -> None:
        pass


def _tool(tmp_path, html: str, donor):
    policy = FetchPolicy(respect_robots=False, min_interval_seconds=0)
    fetcher = Fetcher(policy, opener=lambda req, timeout: _Response(html, req.full_url))
    return DragonFruitMe(state_dir=tmp_path, fetcher=fetcher, donor=donor)


class StubDonor:
    name = "stub"

    def __init__(self, values):
        self.values = values
        self.calls = []

    def extract(self, *, url, fields, html):
        self.calls.append({"url": url, "fields": [f["name"] for f in fields], "html": html})
        return {
            "donor": self.name,
            "values": dict(self.values),
            "evidence": {"source": "test"},
        }

    def status(self):
        return {"name": self.name, "installed": True, "configured": True}


def test_donor_runs_after_native_stages_miss_and_compiles_grounded_value(tmp_path):
    donor = StubDonor({"decision_maker": "Ada Example"})
    html = "<html><body><section><p data-person='1'>Ada Example</p></section></body></html>"
    with _tool(tmp_path, html, donor) as tool:
        result = tool.extract(
            url="https://example.org/company/1",
            fields=[{"name": "decision_maker", "type": "text"}],
            render="never",
        )

    field = result["fields"][0]
    assert result["status"] == "COMPLETE"
    assert field["stage"] == "donor:stub"
    assert field["value"] == "Ada Example"
    assert field["provenance"] == "DONOR_OBSERVED"
    assert donor.calls and donor.calls[0]["fields"] == ["decision_maker"]
    assert field["recipe"] is not None


def test_native_hit_prevents_donor_call(tmp_path):
    donor = StubDonor({"email": "wrong@example.org"})
    html = "<html><body><dl><dt>E-Mail</dt><dd>right@example.org</dd></dl></body></html>"
    with _tool(tmp_path, html, donor) as tool:
        result = tool.extract(
            url="https://example.org/company/1",
            fields=[{"name": "email", "type": "email", "aliases": ["E-Mail"]}],
            render="never",
        )

    assert result["fields"][0]["value"] == "right@example.org"
    assert result["fields"][0]["stage"] == "label"
    assert donor.calls == []


def test_invalid_donor_value_is_rejected_then_agent_fallback(tmp_path):
    donor = StubDonor({"email": "not-an-email"})
    html = "<html><body><p>contact unavailable</p></body></html>"
    with _tool(tmp_path, html, donor) as tool:
        result = tool.extract(
            url="https://example.org/company/1",
            fields=[{"name": "email", "type": "email"}],
            render="never",
        )

    field = result["fields"][0]
    assert field["status"] == "NEEDS_AGENT"
    assert any(
        attempt["stage"] == "donor"
        and attempt["outcome"] == "REJECTED"
        and attempt["reason"] == "TYPE_MISMATCH"
        for attempt in field["attempts"]
    )


def test_one_donor_call_handles_all_unresolved_fields(tmp_path):
    donor = StubDonor({"owner": "Ada Example", "city": "Hamburg"})
    html = "<html><body><p>Ada Example</p><p>Hamburg</p></body></html>"
    with _tool(tmp_path, html, donor) as tool:
        result = tool.extract(
            url="https://example.org/company/1",
            fields=[
                {"name": "owner", "type": "text"},
                {"name": "city", "type": "text"},
            ],
            render="never",
        )

    assert result["status"] == "COMPLETE"
    assert len(donor.calls) == 1
    assert donor.calls[0]["fields"] == ["owner", "city"]


def test_scrapegraph_api_uses_existing_html_not_target_fetch():
    calls = []

    class FakeClient:
        def extract(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                status="success",
                data=SimpleNamespace(json_data={"email": "ada@example.org"}),
                elapsed_ms=12,
            )

    donor = ScrapeGraphApiDonor(client_factory=FakeClient)
    result = donor.extract(
        url="https://example.org/company/1",
        fields=[{"name": "email", "type": "email", "aliases": ["E-Mail"]}],
        html="<html><body>ada@example.org</body></html>",
    )

    assert result["values"]["email"] == "ada@example.org"
    assert calls[0]["html"].startswith("<html>")
    assert "url" not in calls[0]
    assert calls[0]["mode"] == "reader"


def test_scrapegraph_oss_uses_existing_html_and_host_config():
    calls = []

    class FakeGraph:
        def run(self):
            return {"owner": "Ada Example"}

    def factory(**kwargs):
        calls.append(kwargs)
        return FakeGraph()

    donor = ScrapeGraphOssDonor(
        config={"llm": {"model": "test/provider-model"}},
        graph_factory=factory,
    )
    result = donor.extract(
        url="https://example.org/company/1",
        fields=[{"name": "owner", "type": "text"}],
        html="<html><body>Ada Example</body></html>",
    )

    assert result["values"]["owner"] == "Ada Example"
    assert calls[0]["source"].startswith("<html>")
    assert calls[0]["config"]["llm"]["model"] == "test/provider-model"
    assert calls[0]["config"]["verbose"] is False


def test_donor_disabled_by_default(monkeypatch):
    monkeypatch.delenv("DRAGONFRUITME_WEB_DONOR", raising=False)
    assert donor_from_env() is None


def test_valid_but_ungrounded_donor_value_is_rejected(tmp_path):
    donor = StubDonor({"founder": "Invented Person"})
    html = "<html><body><p>Generic company record with no leadership information.</p></body></html>"
    with _tool(tmp_path, html, donor) as tool:
        result = tool.extract(
            url="https://example.org/company/1",
            fields=[{"name": "founder", "type": "text"}],
            render="never",
        )

    field = result["fields"][0]
    assert field["status"] == "NEEDS_AGENT"
    assert any(
        attempt["stage"] == "donor"
        and attempt["outcome"] == "REJECTED"
        and attempt["reason"] == "VALUE_NOT_IN_PAGE"
        for attempt in field["attempts"]
    )
