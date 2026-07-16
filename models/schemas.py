"""
Data models passed between agents (I/O contracts).

Why this design?
- Phase 1 has a single agent, but output_pydantic still enforces a fixed
  contract so Phase 2 (executor agent / Streamlit UI) can consume this
  report without re-parsing free text.
"""

from pydantic import BaseModel, Field


class FlaggedProduct(BaseModel):
    """A product the analyst flagged as needing attention."""

    title: str = Field(description="Product title")
    variant_id: str = Field(description="Shopify variant GID")
    reason: str = Field(description="Why this product was flagged (e.g. low stock, low sales)")
    inventory_quantity: int = Field(description="Current inventory quantity")


class AnalysisReport(BaseModel):
    """Analyst task output - the full operations report."""

    date: str = Field(description="Generation date (YYYY-MM-DD)")
    flagged_products: list[FlaggedProduct] = Field(
        description="Products flagged for review, most urgent first"
    )
    markdown: str = Field(description="Full report body in Markdown")
