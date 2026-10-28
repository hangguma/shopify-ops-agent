"""
Eval runner - Dataset -> Solver -> Scorer, repeated per scenario.

Usage:
    python -m evals.run                       # all scenarios, 3 trials each
    python -m evals.run --trials 5
    python -m evals.run --scenario <name> --scenario <name>
    python -m evals.run --rescore outputs/evals/<file>.json   # re-grade saved runs, no LLM

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
from models.schemas import AnalysisReport


def _trial(i: int, report: AnalysisReport | None, scenario, error: str | None = None) -> dict:
    if report is None:
        return {"trial": i, "passed": False, "judgment_passed": False, "fidelity_passed": False,
                "failures": [f"error: {error}"], "flagged": []}
    s = score(scenario, report)
    return {
        "trial": i,
        "passed": s.passed,
        "judgment_passed": s.judgment_passed,
        "fidelity_passed": s.fidelity_passed,
        "failures": s.failures,
        "flagged": [f.model_dump() for f in report.flagged_products],
    }


def _summarize(scenario, results: list[dict]) -> dict:
    for r in results:
        j = "J:ok " if r["judgment_passed"] else "J:FAIL"
        f = "F:ok " if r["fidelity_passed"] else "F:FAIL"
        print(f"  trial {r['trial']}: {j} {f} {'; '.join(r['failures'])}")
    return {
        "name": scenario.name,
        "why": scenario.why,
        "trials": len(results),
        "passed": sum(r["passed"] for r in results),
        "judgment_passed": sum(r["judgment_passed"] for r in results),
        "fidelity_passed": sum(r["fidelity_passed"] for r in results),
        "results": results,
    }


def run_scenario(scenario, trials: int) -> dict:
    results = []
    for i in range(1, trials + 1):
        try:
            results.append(_trial(i, solve(scenario), scenario))
        except Exception as e:  # noqa: BLE001 - a crashed run is a failed trial, not a crashed eval
            results.append(_trial(i, None, scenario, error=str(e)))
    return _summarize(scenario, results)


def rescore(path: str) -> list[dict]:
    """Re-grade a saved run with the current scorer (no LLM calls)."""
    saved = json.load(open(path, encoding="utf-8"))
    by_name = {s.name: s for s in load_scenarios()}
    summary = []
    for entry in saved["scenarios"]:
        scenario = by_name[entry["name"]]
        print(f"\n[{scenario.name}] {scenario.why}")
        results = []
        for r in entry["results"]:
            if r["failures"] and r["failures"][0].startswith("error:"):
                results.append(_trial(r["trial"], None, scenario, error=r["failures"][0][7:]))
                continue
            report = AnalysisReport(date="", flagged_products=r["flagged"], markdown="")
            results.append(_trial(r["trial"], report, scenario))
        summary.append(_summarize(scenario, results))
    return summary


def print_table(summary: list[dict]) -> None:
    print("\n" + "=" * 50)
    print("judgment  fidelity  both   scenario")
    for r in summary:
        n = r["trials"]
        print(f"  {r['judgment_passed']}/{n}      {r['fidelity_passed']}/{n}      {r['passed']}/{n}   {r['name']}")
    runs = sum(r["trials"] for r in summary)
    for key in ("judgment_passed", "fidelity_passed", "passed"):
        print(f"overall {key}: {sum(r[key] for r in summary)}/{runs}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run golden-scenario evals for the analyst agent")
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--scenario", action="append", help="Run only this scenario (repeatable)")
    parser.add_argument("--rescore", metavar="JSON", help="Re-grade a saved run instead of calling the LLM")
    args = parser.parse_args()

    if args.rescore:
        print_table(rescore(args.rescore))
        return

    if not settings.ANTHROPIC_API_KEY:
        raise SystemExit("ANTHROPIC_API_KEY is not set (.env)")

    scenarios = load_scenarios(args.scenario)
    if not scenarios:
        raise SystemExit("No scenarios found in evals/scenarios/ (files starting with _ are skipped)")

    summary = []
    for scenario in scenarios:
        print(f"\n[{scenario.name}] {scenario.why}")
        summary.append(run_scenario(scenario, args.trials))

    print_table(summary)

    out_dir = os.path.join(settings.OUTPUT_DIR, "evals")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{datetime.now():%Y-%m-%d_%H%M%S}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"model": settings.LLM_MODEL, "scenarios": summary}, f, ensure_ascii=False, indent=2)
    print(f"saved -> {path}")


if __name__ == "__main__":
    main()
