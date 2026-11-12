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

import re
from decimal import Decimal

from pydantic import BaseModel, Field, model_validator


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


# -- Store setup (brief -> plan) -------------------------------------------
# Same split as the analysis side: the LLM writes copy (PlannerOutput), code
# supplies every fact (title, price, vendor, handle) from the brief and
# produces the StorePlan a person approves before anything is written.

_SLUG =re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class BriefProduct(BaseModel):
    """One product as the seller describes it - the source of all facts."""

    key: str = Field(description="Slug, unique in the brief; becomes the Shopify handle")
    title: str = Field(min_length=1)
    one_liner: str = Field(min_length=1, description="What the product is, in the seller's words")
    price: Decimal = Field(gt=0)
    vendor: str = Field(min_length=1)
    bundle_of: list[str] = Field(default_factory=list, description="Keys of the products in this set")


class StoreBrief(BaseModel):
    """The seller's input file (store_brief.yaml)."""

    store_tag: str = Field(description="Tag put on everything this setup creates")
    collection_key: str = Field(description="Slug; becomes the collection handle")
    collection_title: str = Field(min_length=1)
    products: list[BriefProduct] = Field(min_length=1)

    @model_validator(mode="after")
    def _check(self) -> "StoreBrief":
        for slug in [self.store_tag, self.collection_key, *(p.key for p in self.products)]:
            if not _SLUG.match(slug):
                raise ValueError(f"{slug!r} must be lowercase letters, digits and hyphens")
        keys = [p.key for p in self.products]
        dupes = sorted({k for k in keys if keys.count(k) > 1})
        if dupes:
            raise ValueError(f"duplicate product keys: {', '.join(dupes)}")
        singles = {p.key for p in self.products if not p.bundle_of}
        for p in self.products:
            unknown = [k for k in p.bundle_of if k not in singles]
            if unknown:
                raise ValueError(
                    f"{p.key}: bundle_of must name non-bundle products in this brief, got {unknown}"
                )
        return self


class PlannedCopy(BaseModel):
    """What the LLM writes for one product. No facts - only copy."""

    key: str = Field(description="Product key, copied exactly from the brief")
    description_html: str = Field(description="Product description as simple HTML (p, ul, li, strong, em)")
    product_type: str = Field(description="Short product category, e.g. 'Cleansing Oil'")
    tags: list[str] = Field(description="3-6 short lowercase search tags")


class PlannerOutput(BaseModel):
    """Planner task output (LLM side) - turned into StorePlan by store_plan.py."""

    collection_description_html: str = Field(description="One short paragraph introducing the collection")
    products: list[PlannedCopy]


class PlannedProduct(BaseModel):
    """One product exactly as the executor will write it."""

    key: str
    handle: str
    title: str
    vendor: str
    price: Decimal
    product_type: str
    description_html: str
    tags: list[str]
    needs_review: bool = Field(default=False, description="True blocks apply until a person fixes the plan")
    review_reason: str = ""


class PlannedCollection(BaseModel):
    handle: str
    title: str
    description_html: str
    tag: str = Field(description="Products carrying this tag are in the collection")


class StorePlan(BaseModel):
    """The approved-before-apply contract between planner and executor."""

    date: str
    store_tag: str
    collection: PlannedCollection
    products: list[PlannedProduct]
