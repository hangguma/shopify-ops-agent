"""
Enrichment - turns the LLM's picks into an AnalysisReport with code-sourced facts.

Why this design?
- The LLM decides which variants need attention and why. Variant ID and
  inventory quantity are looked up here from the store data, never copied
  by the LLM - same principle as Phase 2C's current_value lookup.
- A pick that does not match exactly one store variant is kept, not dropped:
  variant_id is left empty and needs_review is set, so a person sees it
  rather than a downstream step silently acting on a guess.
- The catalog is fetched again after the crew run, so a live store could in
  theory change in between. Phase 1 is read-only, so the cost of that gap is
  a stale number in a report, not a wrong write.
"""

import logging

from models.schemas import AnalysisReport, AnalystOutput, AnalystPick, FlaggedProduct

log = logging.getLogger(__name__)


def _norm(s: str) -> str:
    return " ".join(s.split()).casefold()


def resolve(pick: AnalystPick, products: list[dict]) -> dict | None:
    """Return the single store variant node the pick refers to, or None."""
    nodes = [e["node"] for e in products]
    title = _norm(pick.title)

    matches = [p for p in nodes if _norm(p["title"]) == title]
    if not matches:
        # LLM sometimes appends the variant, e.g. "Tee (M / Black)": take the
        # longest product title that prefixes it.
        prefixed = sorted((p for p in nodes if title.startswith(_norm(p["title"]))),
                          key=lambda p: len(p["title"]), reverse=True)
        matches = prefixed[:1]
    if len(matches) != 1:
        return None

    variants = [e["node"] for e in matches[0]["variants"]["edges"]]
    if len(variants) == 1:
        return variants[0]
    named = [v for v in variants if _norm(v["title"]) == _norm(pick.variant_title)]
    if not named:
        named = [v for v in variants if _norm(v["title"]) in title]
    return named[0] if len(named) == 1 else None


def enrich(output: AnalystOutput, products: list[dict]) -> AnalysisReport:
    flagged = []
    for pick in output.picks:
        variant = resolve(pick, products)
        if variant is None:
            log.warning("Could not resolve pick to one variant: %r / %r", pick.title, pick.variant_title)
            flagged.append(FlaggedProduct(
                title=pick.title, variant_id="", reason=pick.reason,
                inventory_quantity=None, needs_review=True,
            ))
            continue
        flagged.append(FlaggedProduct(
            title=pick.title, variant_id=variant["id"], reason=pick.reason,
            inventory_quantity=variant["inventoryQuantity"],
        ))
    return AnalysisReport(date=output.date, flagged_products=flagged, markdown=output.markdown)
