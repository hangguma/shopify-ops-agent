"""
Scorer - deterministic, code-only grading of one AnalysisReport.

Why this design?
- Checks run on flagged_products (structured), never on the markdown, so
  grading needs no LLM judge and gives the same verdict every time.
- Two separate scores, because one root cause must not show up as two:
  - judgment: did the agent pick the right products? Flags are resolved to
    store variants by exact ID first, then by product title, so a correct
    pick with a garbled ID still counts as a correct pick.
  - fidelity: are the IDs and quantities it wrote exactly what the store
    returned? Downstream writes (Phase 2C) will act on these IDs.
  The baseline run (2026-10-05) showed why: 8/15 trials failed only on
  `<UNKNOWN>` IDs, which a single score reported as missed products.
- Flag type (stockout risk vs slow mover) is not graded yet: FlaggedProduct
  has no category field. Add one only if results show type confusion.
"""

from dataclasses import dataclass, field

from evals.dataset import Scenario
from models.schemas import AnalysisReport, FlaggedProduct


@dataclass
class Score:
    judgment_failures: list[str] = field(default_factory=list)
    fidelity_failures: list[str] = field(default_factory=list)

    @property
    def judgment_passed(self) -> bool:
        return not self.judgment_failures

    @property
    def fidelity_passed(self) -> bool:
        return not self.fidelity_failures

    @property
    def passed(self) -> bool:
        return self.judgment_passed and self.fidelity_passed

    @property
    def failures(self) -> list[str]:
        return self.judgment_failures + self.fidelity_failures


def _resolve(scenario: Scenario, flag: FlaggedProduct) -> str | None:
    """Map a flag to a store variant ID: exact ID, else unambiguous title match."""
    variants = {v.id: (p, v) for p in scenario.products for v in p.variants}
    if flag.variant_id in variants:
        return flag.variant_id

    title = flag.title.strip().lower()
    candidates = [p for p in scenario.products if title.startswith(p.title.lower())]
    # Prefer the longest product title so "Tee" never shadows "Tee Dress".
    candidates.sort(key=lambda p: len(p.title), reverse=True)
    if not candidates:
        return None
    product = candidates[0]
    if len(product.variants) == 1:
        return product.variants[0].id
    named = [v for v in product.variants if v.title.lower() in title]
    return named[0].id if len(named) == 1 else None


def score(scenario: Scenario, report: AnalysisReport) -> Score:
    s = Score()
    truth = scenario.variant_qty()
    picked: set[str] = set()

    for f in report.flagged_products:
        resolved = _resolve(scenario, f)
        if resolved is None:
            s.judgment_failures.append(f"flagged something not in the store: {f.title}")
            s.fidelity_failures.append(f"unknown variant id: {f.variant_id} ({f.title})")
            continue
        picked.add(resolved)
        if f.variant_id != resolved:
            s.fidelity_failures.append(f"wrong variant id: {f.variant_id!r} for {resolved} ({f.title})")
        if f.inventory_quantity != truth[resolved]:
            s.fidelity_failures.append(
                f"wrong qty: {resolved} reported {f.inventory_quantity}, store has {truth[resolved]}"
            )

    for vid in scenario.expect.must_flag:
        if vid not in picked:
            s.judgment_failures.append(f"missed: {vid}")
    for vid in scenario.expect.must_not_flag:
        if vid in picked:
            s.judgment_failures.append(f"false flag: {vid}")

    return s
