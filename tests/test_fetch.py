from conftest import expose

from dragonfruitme.policy import detect_challenge

ROBOTS = "User-agent: *\nDisallow: /private\n"


def test_http_fetch_extract_and_page_cache(site, make_tool):
    site.route("/robots.txt", ROBOTS, headers={"Content-Type": "text/plain"})
    site.route("/expose/1", expose(), headers={"Content-Encoding": "gzip"})
    tool = make_tool()
    url = site.base + "/expose/1"
    observed = tool.observe(url=url)
    assert observed["ok"] and observed["page"]["stage"] == "http" and observed["page"]["status"] == 200
    assert observed["summary"]["title"] == "Exposé: 3-Zimmer-Wohnung in Kiel"
    result = tool.extract(fields=[{"name": "kaufpreis", "type": "price"}], url=url)
    assert result["fields"][0]["normalized"] == 349000.0
    assert "cache_hit" in result["pages"][0]["notes"]
    assert site.hits["/expose/1"] == 1  # observe + extract = one request
    assert site.hits["/robots.txt"] == 1


def test_robots_disallow_is_final(site, make_tool):
    site.route("/robots.txt", ROBOTS, headers={"Content-Type": "text/plain"})
    site.route("/private/x", expose())
    result = make_tool().observe(url=site.base + "/private/x")
    assert result["ok"] is False and result["error"]["code"] == "ROBOTS_DISALLOWED"
    assert "/private/x" not in site.hits
    ignored = make_tool(respect_robots=False).observe(url=site.base + "/private/x")
    assert ignored["ok"]  # host-level decision only


def test_missing_robots_allows_and_errors_are_structured(site, make_tool):
    site.route("/ok", expose())
    tool = make_tool()
    assert tool.observe(url=site.base + "/ok")["ok"]
    missing = tool.observe(url=site.base + "/nope")
    assert missing["error"]["code"] == "HTTP_ERROR" and missing["error"]["details"]["status"] == 404
    assert tool.observe(url="ftp://example.org/x")["error"]["code"] == "URL_SCHEME_DENIED"
    assert make_tool(deny_hosts=("127.0.0.1",)).observe(url=site.base + "/ok")["error"]["code"] == "HOST_DENIED"


def test_challenges_end_with_blocked_and_are_not_bypassed(site, make_tool):
    site.route("/cf", '<html><body><div class="cf-chl-widget">Checking your browser</div></body></html>', status=403)
    site.route("/slow", "Too many requests", status=429)
    tool = make_tool()
    blocked = tool.extract(fields=["preis"], url=site.base + "/cf")
    assert blocked["ok"] is False
    assert blocked["error"]["code"] == "BLOCKED"
    assert blocked["error"]["details"]["reason"].startswith("CHALLENGE")
    assert "human" in blocked["error"]["details"]["next"]
    assert tool.observe(url=site.base + "/slow")["error"]["details"]["reason"] == "RATE_LIMITED"
    assert site.hits["/cf"] == 1  # no retry, no second strategy


def test_captcha_widget_inside_a_normal_page_is_not_a_block():
    contact = "<html><body>" + "<p>Ein ganz normaler Absatz mit Inhalt.</p>" * 40 + '<div class="g-recaptcha"></div></body></html>'
    assert detect_challenge(200, contact) is None
    assert detect_challenge(200, '<html><body><div class="g-recaptcha"></div></body></html>').startswith("CHALLENGE")
    assert detect_challenge(403, "<html><body>Access denied</body></html>").startswith("CHALLENGE")
    assert detect_challenge(403, "<p>forbidden</p>" * 200) == "HTTP_403"
    assert detect_challenge(200, "<p>Drehkreuz und Turnstile im Stadion</p>") is None


def test_render_stage_only_for_js_shells_and_feeds_the_same_graph(site, make_tool):
    shell = '<html><head><script src="/a.js"></script></head><body><div id="root"></div></body></html>'
    site.route("/app/1", shell)
    calls = []

    def fake_renderer(url, policy):
        calls.append(url)
        return expose(), url, 200

    tool = make_tool(renderer=fake_renderer)
    result = tool.extract(fields=[{"name": "kaufpreis", "type": "price"}], url=site.base + "/app/1")
    price = result["fields"][0]
    assert price["status"] == "FOUND" and price["page_stage"] == "render"
    assert [p["stage"] for p in result["pages"]] == ["http", "render"]
    assert calls == [site.base + "/app/1"]

    site.route("/static/1", expose().replace("Kaufpreis", "Preisangabe"))
    calls.clear()
    plain = tool.extract(fields=[{"name": "kaufpreis", "type": "price"}], url=site.base + "/static/1")
    assert plain["fields"][0]["status"] == "NEEDS_AGENT"
    assert calls == []  # content-rich page: rendering would not help, so it is not attempted
    on_miss = tool.extract(fields=[{"name": "kaufpreis", "type": "price"}], url=site.base + "/static/1", render="on_miss")
    assert calls == [site.base + "/static/1"]
    assert on_miss["fields"][0]["status"] == "FOUND"


def test_render_unavailable_is_recorded_not_raised(site, make_tool, monkeypatch):
    import dragonfruitme.fetch as fetch

    monkeypatch.setattr(fetch, "render_available", lambda: False)
    site.route("/app/2", '<html><body><div id="root"></div></body></html>')
    result = make_tool().extract(fields=["preis"], url=site.base + "/app/2")
    attempts = result["fields"][0]["attempts"]
    assert {"stage": "render", "outcome": "UNAVAILABLE", "reason": "RENDER_NOT_AVAILABLE"} in attempts
    assert result["fields"][0]["status"] == "NEEDS_AGENT"
