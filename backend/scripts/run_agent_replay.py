from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evals.agent_replay import render_summary_markdown, run_suite  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Bobo Agent offline replay eval suite.")
    parser.add_argument(
        "--suite",
        default=str(ROOT / "evals" / "cases" / "agent_business_eval_v1.json"),
        help="Path to eval suite JSON.",
    )
    parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        help="Output format.",
    )
    args = parser.parse_args()

    summary = run_suite(args.suite)
    if args.format == "json":
        print(
            json.dumps(
                {
                    "suite_name": summary.suite_name,
                    "total_cases": summary.total_cases,
                    "passed_cases": summary.passed_cases,
                    "failed_cases": summary.failed_cases,
                    "ok": summary.ok,
                    "results": summary.results,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(render_summary_markdown(summary))

    return 0 if summary.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
