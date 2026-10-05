"""Scenario matrix: drive the real engine through extraction pathologies and
false-positive controls, and render docs/EVIDENCE.md from the results.

    python scripts/scenario_matrix.py            # regenerate docs/EVIDENCE.md
    python scripts/scenario_matrix.py --check    # fail if the file is out of date

A detector that fires on healthy pages is itself a failure mode, so controls
carry the same weight as pathologies. Everything runs offline and is
deterministic; the test suite re-checks the generated file.
"""
from __future__ import annotations

import argparse
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dragonfruitme import DragonFruitMe  # noqa: E402
from dragonfruitme.errors import DragonFruitMeError  # noqa: E402
from dragonfruitme.fetch import Fetcher  # noqa: E402
from dragonfruitme.policy import FetchPolicy  # noqa: E402
from dragonfruitme.recipes import Recipe  # noqa: E402

EVIDENCE = ROOT / "docs" / "EVIDENCE.md"
BASE = "https://makler.example/expose/{}"
PRICE = [{"name": "kaufpreis", "type": "price"}]


def expose(n: int, price: str, *, teaser: str = "", extra: str = "", id_attr: str | None = None) -> str:
    data_id = id_attr if id_attr is not None else str(n)
    return (
        "<html><body><main>"
        f"{teaser}<h1>Wohnung {n}</h1><h2>Objektdaten</h2>"
        f'<dl><dt>Kaufpreis</dt><dd class="price" data-id="{data_id}">{price}</dd>'
        "<dt>Zimmer</dt><dd>3</dd></dl>"
        f"<p>E-Mail: info@makler.example</p>{extra}"
        "</main></body></html>"
    )


class _Response:
    def __init__(self, url: str, status: int, body: str):
        self.status, self._url, self._body = status, url, body.encode("utf-8")
        self.headers = {"Content-Type": "text/html; charset=utf-8"}

    def geturl(self) -> str:
        return self._url

    def read(self, n: int = -1) -> bytes:
        return self._body

    def close(self) -> None:
        pass


def offline_tool(state: Path, pages: dict[str, tuple[int, str]], renderer=None) -> DragonFruitMe:
    def opener(request, timeout):
        status, body = pages[request.full_url]
        return _Response(request.full_url, status, body)

    policy = FetchPolicy(respect_robots=False, min_interval_seconds=0.0)
    return DragonFruitMe(state_dir=state, policy=policy, fetcher=Fetcher(policy, opener=opener, renderer=renderer))


@dataclass
class Scenario:
    id: str
    kind: str  # pathology | control
    title: str
    expectation: str
    run: Callable[[Path], tuple[bool, str]]


# -- pathologies ---------------------------------------------------------------

def p1_recipe_drift(state: Path) -> tuple[bool, str]:
    tool = DragonFruitMe(state_dir=state)
    tool.extract(fields=PRICE, html=expose(1, "349.000 €"), base_url=BASE.format(1))
    redesigned = '<html><body><div class="box"><span>Kaufpreis:</span> <b>415.000 EUR</b></div></body></html>'
    f = tool.extract(fields=PRICE, html=redesigned, base_url=BASE.format(2))["fields"][0]
    again = tool.extract(fields=PRICE, html=redesigned.replace("415", "420"), base_url=BASE.format(3))["fields"][0]
    ok = f["attempts"][0]["outcome"] == "MISS" and f["normalized"] == 415000.0 and again["stage"] == "recipe"
    return ok, f"recipe MISS → {f['stage']} {f['value']} → next page served by {again['stage']}"


def p2_false_anchor(state: Path) -> tuple[bool, str]:
    tool = DragonFruitMe(state_dir=state)
    tool.engine.store.put(Recipe(scope="makler.example/expose/*", field="kaufpreis", kind="regex",
                                 pattern=r'<span class="teaser">\s*([^<]+?)(?=\s*<)', origin="label"))
    teaser = '<span class="teaser">199.000 €</span>'
    f = tool.extract(fields=PRICE, html=expose(2, "415.000 €", teaser=teaser), base_url=BASE.format(2))["fields"][0]
    attempt = f["attempts"][0]
    ok = attempt.get("reason") == "CONTRADICTED" and f["normalized"] == 415000.0
    return ok, f"recipe said {attempt.get('recipe_value')} → {attempt.get('reason')} → {f['stage']} {f['value']}"


def p3_repeated_unconfirmed(state: Path) -> tuple[bool, str]:
    tool = DragonFruitMe(state_dir=state)
    tool.engine.store.put(Recipe(scope="makler.example/expose/*", field="highlight", kind="regex",
                                 pattern=r'<span class="teaser">\s*([^<]+?)(?=\s*<)', origin="label"))
    codes = []
    for n in range(1, 4):
        html = f'<html><body><span class="teaser">Top-Angebot</span><p>Objekt {n}</p></body></html>'
        f = tool.extract(fields=["highlight"], html=html, base_url=BASE.format(n))["fields"][0]
        codes.append(",".join(s["code"] for s in f["signals"]) or "-")
    return codes[-1] == "REPEATED_VALUE", "signals per page: " + " / ".join(codes)


def p4_challenge_page(state: Path) -> tuple[bool, str]:
    url = "https://shop.example/item/1"
    tool = offline_tool(state, {url: (403, '<html><body><div class="cf-chl-widget">Checking</div></body></html>')})
    r = tool.extract(fields=PRICE, url=url)
    err = r.get("error", {})
    return err.get("code") == "BLOCKED", f"{err.get('code')} ({err.get('details', {}).get('reason')}), no retry"


def p5_js_shell_without_renderer(state: Path) -> tuple[bool, str]:
    url = "https://app.example/listing/1"

    def unavailable(u, policy):
        raise DragonFruitMeError("RENDER_NOT_AVAILABLE", "no browser")

    tool = offline_tool(state, {url: (200, '<html><body><div id="root"></div></body></html>')}, renderer=unavailable)
    f = tool.extract(fields=PRICE, url=url)["fields"][0]
    render = next(a for a in f["attempts"] if a["stage"] == "render")
    return f["status"] == "NEEDS_AGENT" and render["outcome"] == "UNAVAILABLE", \
        f"render {render['outcome']} → {f['status']}"


def p6_ungrounded_teach(state: Path) -> tuple[bool, str]:
    tool = DragonFruitMe(state_dir=state)
    f = tool.extract(fields=[{"name": "heizung", "teach": "Wärmepumpe"}], html=expose(1, "349.000 €"),
                     base_url=BASE.format(1))["fields"][0]
    return f["status"] == "NOT_GROUNDED" and not tool.recipes()["recipes"], f"{f['status']}, no recipe stored"


def p7_agent_claim_below_minimum(state: Path) -> tuple[bool, str]:
    tool = DragonFruitMe(state_dir=state)
    html = expose(1, "349.000 €", extra="<p>Ausstattung mit Fußbodenheizung.</p>")
    f = tool.extract(fields=[{"name": "ausstattung", "teach": "Fußbodenheizung", "min_provenance": "ENGINE_OBSERVED"}],
                     html=html, base_url=BASE.format(1))["fields"][0]
    return f["status"] == "UNCONFIRMED", f"{f['provenance']} < {f.get('required_provenance')} → {f['status']}"


_PROJECT = ('<html><body><main><h1>Neubauprojekt</h1><div class="project-box">'
            '<span class="lbl">Kaufpreis ab:</span> <b>{} EUR</b></div></main></body></html>')


def p8_outlier_overwrites_primary(state: Path) -> tuple[bool, str]:
    tool = DragonFruitMe(state_dir=state)
    for n in range(1, 6):
        tool.extract(fields=PRICE, html=expose(n, f"{300 + n}.000 €"), base_url=BASE.format(n))
    tool.extract(fields=PRICE, html=_PROJECT.format("499.000"), base_url=BASE.format(6))
    f = tool.extract(fields=PRICE, html=expose(7, "320.000 €"), base_url=BASE.format(7))["fields"][0]
    first = f["attempts"][0]
    ok = (first["outcome"], first["role"]) == ("HIT", "primary") and len(f["attempts"]) == 1
    return ok, f"after 1 outlier, next page: {first['role']} {first['outcome']} ({len(f['attempts'])} attempt)"


def p9_redesign_promotes_alternative(state: Path) -> tuple[bool, str]:
    tool = DragonFruitMe(state_dir=state)
    tool.extract(fields=PRICE, html=expose(1, "349.000 €"), base_url=BASE.format(1))
    steps = []
    for n, price in ((2, "410.000"), (3, "420.000"), (4, "430.000")):
        f = tool.extract(fields=PRICE, html=_PROJECT.format(price), base_url=BASE.format(n))["fields"][0]
        hit = f["attempts"][-1]
        steps.append(f"{f['stage']}" + ("/promoted" if hit.get("promoted") else ""))
    ok = steps == ["label", "recipe/promoted", "recipe"] and f["attempts"][0].get("role") == "primary"
    return ok, " → ".join(steps)


# -- false-positive controls -------------------------------------------------------

def c1_captcha_widget_in_content_page(state: Path) -> tuple[bool, str]:
    url = "https://makler.example/kontakt"
    body = expose(1, "349.000 €", extra="<p>Schreiben Sie uns.</p>" * 30 + '<div class="g-recaptcha"></div>')
    tool = offline_tool(state, {url: (200, body)})
    r = tool.extract(fields=PRICE, url=url)
    ok = r.get("ok") and r["fields"][0]["status"] == "FOUND"
    return bool(ok), "not blocked, " + (r["fields"][0]["status"] if r.get("ok") else r["error"]["code"])


def c2_price_change_same_template(state: Path) -> tuple[bool, str]:
    tool = DragonFruitMe(state_dir=state)
    tool.extract(fields=PRICE, html=expose(1, "349.000 €"), base_url=BASE.format(1))
    f = tool.extract(fields=PRICE, html=expose(2, "1.250.000 €"), base_url=BASE.format(2))["fields"][0]
    ok = f["stage"] == "recipe" and f["normalized"] == 1250000.0 and not f["signals"]
    return ok, f"{f['stage']} {f['value']}, signals: {len(f['signals'])}"


def c3_short_ids(state: Path) -> tuple[bool, str]:
    tool = DragonFruitMe(state_dir=state)
    tool.extract(fields=PRICE, html=expose(1, "349.000 €", id_attr="1"), base_url=BASE.format(1))
    f = tool.extract(fields=PRICE, html=expose(2, "199.000 €", id_attr="2"), base_url=BASE.format(2))["fields"][0]
    return f["stage"] == "recipe" and f["normalized"] == 199000.0, f"{f['stage']} {f['value']}"


def c4_confirmed_constant(state: Path) -> tuple[bool, str]:
    tool = DragonFruitMe(state_dir=state)
    flagged = 0
    for n in range(1, 6):
        f = tool.extract(fields=[{"name": "email", "type": "email", "aliases": ["E-Mail"]}],
                         html=expose(n, f"{300 + n}.000 €"), base_url=BASE.format(n))["fields"][0]
        flagged += bool(f["signals"])
    return flagged == 0, f"same e-mail on 5 pages, flagged: {flagged}"


def c5_cross_confirmation(state: Path) -> tuple[bool, str]:
    tool = DragonFruitMe(state_dir=state)
    html = expose(1, "349.000 €").replace(
        "<html><body>",
        '<html><head><script type="application/ld+json">{"offers":{"price":"349000"}}</script></head><body>',
    )
    f = tool.extract(fields=[{"name": "kaufpreis", "type": "price", "paths": ["offers.price"]}], html=html,
                     base_url=BASE.format(1))["fields"][0]
    return f["provenance"] == "CROSS_CONFIRMED", f"{f['stage']} → {f['provenance']} by {'+'.join(f['confirmed_by'])}"


def c6_same_url_re_extracted(state: Path) -> tuple[bool, str]:
    tool = DragonFruitMe(state_dir=state)
    tool.engine.store.put(Recipe(scope="makler.example/expose/*", field="highlight", kind="regex",
                                 pattern=r'<span class="teaser">\s*([^<]+?)(?=\s*<)', origin="label"))
    html = '<html><body><span class="teaser">Top-Angebot</span></body></html>'
    for _ in range(5):
        f = tool.extract(fields=["highlight"], html=html, base_url=BASE.format(1))["fields"][0]
    return not f["signals"], f"5× same URL, signals: {len(f['signals'])}"


def c7_healthy_primary_runs_alone(state: Path) -> tuple[bool, str]:
    tool = DragonFruitMe(state_dir=state)
    tool.extract(fields=PRICE, html=expose(1, "349.000 €"), base_url=BASE.format(1))
    tool.extract(fields=PRICE, html=_PROJECT.format("499.000"), base_url=BASE.format(2))  # an alternative exists
    f = tool.extract(fields=PRICE, html=expose(3, "199.000 €"), base_url=BASE.format(3))["fields"][0]
    ok = len(f["attempts"]) == 1 and not f["signals"]
    return ok, f"alternatives stored, recipes tried: {len(f['attempts'])}, signals: {len(f['signals'])}"


SCENARIOS = [
    Scenario("P1", "pathology", "Recipe drift after a redesign", "recipe misses, label stage relearns, next page uses new recipe", p1_recipe_drift),
    Scenario("P2", "pathology", "Recipe anchored on a teaser price", "independent stage contradicts, recipe discarded, correct price", p2_false_anchor),
    Scenario("P3", "pathology", "Same unconfirmed value on every page", "REPEATED_VALUE signal from the third different URL", p3_repeated_unconfirmed),
    Scenario("P4", "pathology", "Bot challenge page", "BLOCKED, no bypass, no retry", p4_challenge_page),
    Scenario("P5", "pathology", "JavaScript shell, no browser", "render UNAVAILABLE recorded, NEEDS_AGENT", p5_js_shell_without_renderer),
    Scenario("P6", "pathology", "Agent teaches a value that is not on the page", "NOT_GROUNDED, nothing compiled", p6_ungrounded_teach),
    Scenario("P7", "pathology", "Agent claim below the required provenance", "UNCONFIRMED instead of FOUND", p7_agent_claim_below_minimum),
    Scenario("P8", "pathology", "Outlier page (project layout) on a proven template", "learned as candidate; primary keeps answering the next normal page", p8_outlier_overwrites_primary),
    Scenario("P9", "pathology", "Whole template redesigned", "candidate proves itself, is promoted, then runs alone", p9_redesign_promotes_alternative),
    Scenario("C1", "control", "CAPTCHA widget inside a normal content page", "not BLOCKED, value found", c1_captcha_widget_in_content_page),
    Scenario("C2", "control", "New price on the same template", "recipe hit, no signal", c2_price_change_same_template),
    Scenario("C3", "control", "Short per-page ids in markup", "recipe still reused", c3_short_ids),
    Scenario("C4", "control", "Legitimately constant value (agency e-mail)", "never flagged when confirmed by its label", c4_confirmed_constant),
    Scenario("C5", "control", "Structured data and label agree", "CROSS_CONFIRMED", c5_cross_confirmation),
    Scenario("C6", "control", "Same URL extracted repeatedly", "not counted as repetition", c6_same_url_re_extracted),
    Scenario("C7", "control", "Healthy primary with stored alternatives", "only the primary runs, no signal", c7_healthy_primary_runs_alone),
]


def run() -> list[tuple[Scenario, bool, str]]:
    rows = []
    for scenario in SCENARIOS:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            try:
                ok, observed = scenario.run(Path(tmp) / "state")
            except Exception as exc:  # a crashing scenario is a failing scenario
                ok, observed = False, f"crashed: {type(exc).__name__}: {exc}"
        rows.append((scenario, ok, observed))
    return rows


def render(rows: list[tuple[Scenario, bool, str]]) -> str:
    passed = sum(ok for _, ok, _ in rows)
    lines = [
        "# Evidence",
        "",
        "Generated by `scripts/scenario_matrix.py` and re-checked by the test suite. Do not edit by hand.",
        "",
        "Every scenario drives the real engine offline. **Pathologies** must be detected;",
        "**controls** are healthy situations that must *not* trigger a detector — a detector that fires",
        "on healthy pages is itself a failure mode, so both carry equal weight.",
        "",
        f"**Result: {passed}/{len(rows)} scenarios pass.**",
        "",
        "| ID | Kind | Scenario | Expected | Observed | Result |",
        "|---|---|---|---|---|---|",
    ]
    for scenario, ok, observed in rows:
        lines.append(
            f"| {scenario.id} | {scenario.kind} | {scenario.title} | {scenario.expectation} | "
            f"{observed.replace('|', '/')} | {'PASS' if ok else 'FAIL'} |"
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="exit 1 if docs/EVIDENCE.md is stale or a scenario fails")
    args = parser.parse_args(argv)
    rows = run()
    text = render(rows)
    failed = [s.id for s, ok, _ in rows if not ok]
    if args.check:
        current = EVIDENCE.read_text(encoding="utf-8") if EVIDENCE.exists() else ""
        if current != text:
            print("docs/EVIDENCE.md is out of date; run scripts/scenario_matrix.py", file=sys.stderr)
            return 1
    else:
        EVIDENCE.write_text(text, encoding="utf-8")
    if failed:
        print("failing scenarios: " + ", ".join(failed), file=sys.stderr)
        return 1
    print(f"{len(rows) - len(failed)}/{len(rows)} scenarios pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
