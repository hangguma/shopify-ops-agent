"""
Dataset - golden scenarios for the Phase 1 analyst, loaded from YAML.

Why this design?
- A scenario is a fixed store snapshot (products + orders) plus what the
  analyst must and must not flag. Fixed input means any failure is the
  agent's judgment, not a change in live store data.
- Fixtures are written in a flat, human-friendly shape and converted to the
  exact GraphQL response shape here, so the tools' own formatting code runs
  unchanged and the LLM sees the same text it sees in production.
- Files starting with "_" are format examples and are never loaded.
"""

from decimal import Decimal
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

SCENARIOS_DIR = Path(__file__).parent / "scenarios"


class VariantFixture(BaseModel):
    id: str = Field(description="Variant GID, e.g. gid://shopify/ProductVariant/1001")
    title: str = "Default Title"
    price: str = "0.00"
    qty: int


class ProductFixture(BaseModel):
    title: str
    status: str = "ACTIVE"
    variants: list[VariantFixture]


class LineItemFixture(BaseModel):
    title: str = Field(description="Product title, as Shopify returns it on line items")
    quantity: int = 1


class OrderFixture(BaseModel):
    name: str
    created_at: str
    fulfillment_status: str = "FULFILLED"
    line_items: list[LineItemFixture]


class Expectation(BaseModel):
    must_flag: list[str] = Field(default_factory=list, description="Variant GIDs that must be flagged")
    must_not_flag: list[str] = Field(default_factory=list, description="Variant GIDs that must not be flagged")


class Scenario(BaseModel):
    name: str
    why: str = Field(description="What failure this scenario exists to catch")
    products: list[ProductFixture]
    orders: list[OrderFixture]
    expect: Expectation

    def variant_qty(self) -> dict[str, int]:
        """Ground-truth inventory per variant GID."""
        return {v.id: v.qty for p in self.products for v in p.variants}

    def products_response(self, first: int) -> dict:
        edges = []
        for p in self.products[:first]:
            edges.append({"node": {
                "id": f"gid://shopify/Product/{len(edges) + 1}",
                "title": p.title,
                "status": p.status,
                "totalInventory": sum(v.qty for v in p.variants),
                "variants": {"edges": [
                    {"node": {"id": v.id, "title": v.title, "price": v.price, "inventoryQuantity": v.qty}}
                    for v in p.variants
                ]},
            }})
        return {"products": {"edges": edges}}

    def orders_response(self, first: int) -> dict:
        price_by_title = {p.title: Decimal(p.variants[0].price) for p in self.products if p.variants}
        newest_first = sorted(self.orders, key=lambda o: o.created_at, reverse=True)
        edges = []
        for o in newest_first[:first]:
            total = sum(price_by_title.get(li.title, Decimal("0")) * li.quantity for li in o.line_items)
            edges.append({"node": {
                "name": o.name,
                "createdAt": o.created_at,
                "displayFulfillmentStatus": o.fulfillment_status,
                "totalPriceSet": {"shopMoney": {"amount": f"{total:.2f}", "currencyCode": "USD"}},
                "lineItems": {"edges": [
                    {"node": {"title": li.title, "quantity": li.quantity}} for li in o.line_items
                ]},
            }})
        return {"orders": {"edges": edges}}


def load_scenarios(names: list[str] | None = None) -> list[Scenario]:
    """Load every scenario YAML (or only `names`), skipping _-prefixed files."""
    scenarios = []
    for path in sorted(SCENARIOS_DIR.glob("*.yaml")):
        if path.name.startswith("_"):
            continue
        scenario = Scenario.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
        if names is None or scenario.name in names:
            scenarios.append(scenario)
    return scenarios
