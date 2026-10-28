"""
StorePlan - the contract between the Builder agent, the human approval step,
and the Executor.

Why this design?
- The Builder (LLM) only PROPOSES a plan; it never writes to Shopify. The
  Executor applies the plan with plain, deterministic code. That keeps every
  write auditable and reproducible, and means a bad LLM answer can at worst
  produce a bad *proposal* that a human rejects.
- Every item carries `approved` (default False). Nothing reaches Shopify
  unless a human flipped it to True during review.
- Items are keyed by `handle` (Shopify's URL slug). Handles make the setup
  idempotent: re-running the same plan finds existing resources instead of
  creating duplicates.
- Cross-references (a product's collection, a menu link's target) are checked
  by `reference_problems()` rather than a model validator, so an LLM plan with
  a dangling reference can still be saved, shown and fixed by hand instead of
  failing the whole run.
"""

import re
from decimal import Decimal, InvalidOperation
from typing import Literal

from pydantic import BaseModel, Field, field_validator

_HANDLE_RE = re.compile(r"[^a-z0-9]+")


def slugify(value: str) -> str:
    """Normalise a string into a Shopify-style handle (lowercase, hyphens)."""
    slug = _HANDLE_RE.sub("-", value.strip().lower()).strip("-")
    if not slug:
        raise ValueError("handle cannot be empty")
    return slug


class PlanItem(BaseModel):
    """Base for anything a human approves before it is applied."""

    approved: bool = Field(
        default=False,
        description="Set by a human during review. Builder must leave this false.",
    )


class _HandleMixin(BaseModel):
    handle: str = Field(description="URL slug, lowercase words joined by hyphens")

    @field_validator("handle")
    @classmethod
    def _normalise_handle(cls, v: str) -> str:
        return slugify(v)


class CollectionDraft(PlanItem, _HandleMixin):
    """A manual collection (products are attached via the product's collections)."""

    title: str
    description_html: str = Field(default="", description="Short HTML description")


class ProductDraft(PlanItem, _HandleMixin):
    """A single-variant product. Multi-variant products are out of scope for 2A."""

    title: str
    description_html: str = Field(description="Product description as simple HTML")
    product_type: str = ""
    vendor: str = ""
    tags: list[str] = Field(default_factory=list)
    price: str = Field(description="Price as a decimal string, e.g. '24.00'")
    collection_handles: list[str] = Field(
        default_factory=list, description="Handles of collections in this plan"
    )
    seo_title: str = ""
    seo_description: str = ""

    @field_validator("price")
    @classmethod
    def _valid_price(cls, v: str) -> str:
        try:
            amount = Decimal(str(v).strip().lstrip("$"))
        except InvalidOperation as e:
            raise ValueError(f"price must be a number, got {v!r}") from e
        if amount <= 0:
            raise ValueError("price must be greater than 0")
        return f"{amount.quantize(Decimal('0.01'))}"

    @field_validator("collection_handles")
    @classmethod
    def _normalise_collection_handles(cls, v: list[str]) -> list[str]:
        return [slugify(h) for h in v]


class PageDraft(PlanItem, _HandleMixin):
    """An Online Store page such as About, FAQ or Shipping."""

    title: str
    body_html: str


MenuItemType = Literal["FRONTPAGE", "CATALOG", "SEARCH", "COLLECTION", "PAGE"]

# Types that link to a fixed storefront URL instead of a resource in the plan.
FIXED_URLS: dict[str, str] = {"FRONTPAGE": "/", "CATALOG": "/collections/all", "SEARCH": "/search"}


class MenuItemDraft(BaseModel):
    title: str
    type: MenuItemType
    target_handle: str | None = Field(
        default=None,
        description="Collection or page handle in this plan; required for COLLECTION and PAGE",
    )

    @field_validator("target_handle")
    @classmethod
    def _normalise_target(cls, v: str | None) -> str | None:
        return slugify(v) if v else None


class MenuDraft(PlanItem):
    """Replaces the items of an existing menu (the theme's main menu by default)."""

    handle: str = "main-menu"
    title: str = "Main menu"
    items: list[MenuItemDraft] = Field(default_factory=list)


class StorePlan(BaseModel):
    """Builder task output - everything needed to set up the Online Store."""

    brand_name: str
    brief_summary: str = Field(description="One or two sentences restating the brand brief")
    collections: list[CollectionDraft] = Field(default_factory=list)
    products: list[ProductDraft] = Field(default_factory=list)
    pages: list[PageDraft] = Field(default_factory=list)
    menu: MenuDraft | None = None

    def reference_problems(self) -> list[str]:
        """Return human-readable problems; an empty list means the plan is consistent."""
        problems: list[str] = []

        for label, items in (
            ("collection", self.collections),
            ("product", self.products),
            ("page", self.pages),
        ):
            seen: set[str] = set()
            for item in items:
                if item.handle in seen:
                    problems.append(f"duplicate {label} handle '{item.handle}'")
                seen.add(item.handle)

        collection_handles = {c.handle for c in self.collections}
        page_handles = {p.handle for p in self.pages}

        for product in self.products:
            for h in product.collection_handles:
                if h not in collection_handles:
                    problems.append(
                        f"product '{product.handle}' references unknown collection '{h}'"
                    )

        if self.menu:
            for item in self.menu.items:
                if item.type in ("COLLECTION", "PAGE") and not item.target_handle:
                    problems.append(f"menu item '{item.title}' ({item.type}) has no target_handle")
                elif item.type == "COLLECTION" and item.target_handle not in collection_handles:
                    problems.append(
                        f"menu item '{item.title}' links to unknown collection '{item.target_handle}'"
                    )
                elif item.type == "PAGE" and item.target_handle not in page_handles:
                    problems.append(
                        f"menu item '{item.title}' links to unknown page '{item.target_handle}'"
                    )

        return problems

    def approval_counts(self) -> dict[str, tuple[int, int]]:
        """(approved, total) per section - used by the CLI summary."""
        counts = {
            "collections": (sum(c.approved for c in self.collections), len(self.collections)),
            "products": (sum(p.approved for p in self.products), len(self.products)),
            "pages": (sum(p.approved for p in self.pages), len(self.pages)),
        }
        counts["menu"] = (int(bool(self.menu and self.menu.approved)), int(self.menu is not None))
        return counts
