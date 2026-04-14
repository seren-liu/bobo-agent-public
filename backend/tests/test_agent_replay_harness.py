from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evals.agent_replay import load_suite, run_suite


def test_agent_business_eval_suite_runs_green():
    suite_path = ROOT / "evals" / "cases" / "agent_business_eval_v1.json"

    suite = load_suite(suite_path)
    summary = run_suite(suite_path)

    assert suite["schema_version"] == "bobo-agent-business-eval.v1"
    assert summary.total_cases >= 6
    assert summary.failed_cases == 0
    assert summary.ok is True
