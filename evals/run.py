"""
Eval runner - Dataset -> Solver -> Scorer, repeated per scenario.

Usage:
    python -m evals.run                       # all scenarios, 3 trials each
    python -m evals.run --trials 5
    python -m evals.run --scenario <name> --scenario <name>

Calls the real LLM (costs money), so it is not part of pytest or CI.
Results are saved to outputs/evals/<timestamp>.json.
"""

import argparse
import json
import os
from datetime import datetime

from config import settings
from evals.dataset import load_scenarios
from evals.scorer import score
from evals.solver import solve


def run_scenario(scenario, trials: int) -> dict:
    results = []
    for i in range(1, trials + 1):
        try:
            report = solve(scenario)
            s = score(scenario, report)
            results.append({
                "trial": i,
                "passed": s.passed,
                "failures": s.failures,
                "flagged": [f.model_dump() for f in report.flagged_products],
            })
        except Exception as e:  # noqa: BLE001 - a crashed run is a failed trial, not a crashed eval
            results.append({"trial": i, "passed": False, "failures": [f"error: {e}"], "flagged": []})
        mark = "PASS" if results[-1]["passed"] else "FAIL"
        print(f"  trial {i}/{trials}: {mark} {'; '.join(results[-1]['failures'])}")

    passed = sum(r["passed"] for r in results)
    return {"name": scenario.name, "why": scenario.why, "passed": passed, "trials": trials, "results": results}


def main() -> None:
    parser = argparse.ArgumentParser(description="Run golden-scenario evals for the analyst agent")
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--scenario", action="append", help="Run only this scenario (repeatable)")
    args = parser.parse_args()

    if not settings.ANTHROPIC_API_KEY:
        raise SystemExit("ANTHROPIC_API_KEY is not set (.env)")

    scenarios = load_scenarios(args.scenario)
    if not scenarios:
        raise SystemExit("No scenarios found in evals/scenarios/ (files starting with _ are skipped)")

    summary = []
    for scenario in scenarios:
        print(f"\n[{scenario.name}] {scenario.why}")
        summary.append(run_scenario(scenario, args.trials))

    print("\n" + "=" * 50)
    for r in summary:
        print(f"{r['passed']}/{r['trials']}  {r['name']}")
    total = sum(r["passed"] for r in summary)
    runs = sum(r["trials"] for r in summary)
    print(f"overall: {total}/{runs} trials passed")

    out_dir = os.path.join(settings.OUTPUT_DIR, "evals")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{datetime.now():%Y-%m-%d_%H%M%S}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"model": settings.LLM_MODEL, "scenarios": summary}, f, ensure_ascii=False, indent=2)
    print(f"saved -> {path}")


if __name__ == "__main__":
    main()
