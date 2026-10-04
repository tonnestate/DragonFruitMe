import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _matrix():
    spec = importlib.util.spec_from_file_location("scenario_matrix", ROOT / "scripts" / "scenario_matrix.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve their module during exec
    spec.loader.exec_module(module)
    return module


def test_all_scenarios_pass_and_evidence_is_current():
    matrix = _matrix()
    rows = matrix.run()
    failed = [(s.id, observed) for s, ok, observed in rows if not ok]
    assert failed == []
    assert {s.kind for s, _, _ in rows} == {"pathology", "control"}
    committed = (ROOT / "docs" / "EVIDENCE.md").read_text(encoding="utf-8")
    assert committed == matrix.render(rows), "docs/EVIDENCE.md is stale: run python scripts/scenario_matrix.py"
