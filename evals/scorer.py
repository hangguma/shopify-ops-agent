"""
Scorer - deterministic, code-only grading of one AnalysisReport.

Why this design?
- Checks run on flagged_products (structured), never on the markdown, so
  grading needs no LLM judge and gives the same verdict every time.
- Two kinds of checks:
  - expectations: per-scenario must_flag / must_not_flag (the golden answer)
  - invariants: true for every scenario - a flagged variant must exist in the
    store, and its reported quantity must match the store. These catch
    fabrication, which a must_flag list alone would miss.
- Flag type (stockout risk vs slow mover) is not graded yet: FlaggedProduct
  has no category field. Add one only if results show type confusion.
"""

from dataclasses import dataclass, field

from evals.dataset import Scenario
from models.schemas import AnalysisReport


@dataclass
class Score:
    failures: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.failures


def score(scenario: Scenario, report: AnalysisReport) -> Score:
    s = Score()
    flagged = {f.variant_id: f for f in report.flagged_products}
    truth = scenario.variant_qty()

    for vid in scenario.expect.must_flag:
        if vid not in flagged:
            s.failures.append(f"missed: {vid}")
    for vid in scenario.expect.must_not_flag:
        if vid in flagged:
            s.failures.append(f"false flag: {vid}")

    for vid, f in flagged.items():
        if vid not in truth:
            s.failures.append(f"unknown variant id: {vid} ({f.title})")
        elif f.inventory_quantity != truth[vid]:
            s.failures.append(f"wrong qty: {vid} reported {f.inventory_quantity}, store has {truth[vid]}")

    return s
