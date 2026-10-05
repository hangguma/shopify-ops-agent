"""
Data models passed between agents (I/O contracts).

Why this design?
- Phase 1 has a single agent, but output_pydantic still enforces a fixed
  contract so Phase 2 (executor agent / Streamlit UI) can consume this
  report without re-parsing free text.
- The LLM only decides (AnalystPick: which product, why). Facts it would
  otherwise transcribe - variant ID, inventory quantity - are filled in by
  code from the store data (enrich.py). The golden-scenario eval
  (2026-10-05) showed the LLM writing variant_id as "<UNKNOWN>" or the
  variant title in 8 of 15 runs while picking the right product 14 of 15.
"""

from pydantic import BaseModel, Field


class AnalystPick(BaseModel):
    """What the LLM decides: which variant needs attention, and why."""

    title: str = Field(description="Product title, copied exactly as get_products shows it")
    variant_title: str = Field(description="Variant title exactly as shown after 'variant:' (e.g. 'M / Black')")
    reason: str = Field(description="Why this product was flagged (e.g. low stock, low sales)")


class AnalystOutput(BaseModel):
    """Analyst task output (LLM side) - turned into AnalysisReport by enrich.py."""

    date: str = Field(description="Generation date (YYYY-MM-DD)")
    picks: list[AnalystPick] = Field(description="Products flagged for review, most urgent first")
    markdown: str = Field(description="Full report body in Markdown")


class FlaggedProduct(BaseModel):
    """A product the analyst flagged as needing attention."""

    title: str = Field(description="Product title")
    variant_id: str = Field(description="Shopify variant GID; empty if it could not be resolved")
    reason: str = Field(description="Why this product was flagged (e.g. low stock, low sales)")
    inventory_quantity: int | None = Field(description="Current inventory quantity; None if unresolved")
    needs_review: bool = Field(
        default=False, description="True when code could not match the pick to exactly one store variant"
    )


class AnalysisReport(BaseModel):
    """Analyst task output - the full operations report."""

    date: str = Field(description="Generation date (YYYY-MM-DD)")
    flagged_products: list[FlaggedProduct] = Field(
        description="Products flagged for review, most urgent first"
    )
    markdown: str = Field(description="Full report body in Markdown")
