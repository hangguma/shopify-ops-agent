"""
Shopify Admin GraphQL API wrapped as CrewAI tools (Phase 1: read-only).

Why this design?
- One shared `_graphql_request` helper, two tools on top of it: products
  (catalog + inventory) and orders (recent sales). Inventory levels are
  folded into the products tool (variant.inventoryQuantity) rather than a
  third tool, since Shopify already returns it in the same query - a
  separate "get_inventory" call would just repeat this one.
- Phase 2 (write tool: update_inventory; price/discount cut for cost of error) will live in this
  same module, sharing _graphql_request.
- Auth and `_graphql_request` live in tools/shopify_client.py, shared with
  the store-setup writes in tools/shopify_write.py. Those writes are plain
  functions, not CrewAI tools: no agent is ever handed a write tool.
"""

from crewai.tools import BaseTool
from pydantic import BaseModel, Field

from tools.shopify_client import _graphql_request

PRODUCTS_QUERY = """
query ($first: Int!) {
  products(first: $first) {
    edges {
      node {
        id
        title
        status
        totalInventory
        variants(first: 5) {
          edges {
            node {
              id
              title
              price
              inventoryQuantity
            }
          }
        }
      }
    }
  }
}
"""


def fetch_products(limit: int) -> list[dict]:
    """Product edges (with variants) - shared by the tool and enrich.py."""
    return _graphql_request(PRODUCTS_QUERY, {"first": limit})["products"]["edges"]


class ShopifyProductsInput(BaseModel):
    limit: int = Field(default=20, description="Max number of products to fetch")


class ShopifyProductsTool(BaseTool):
    name: str = "get_products"
    description: str = (
        "Fetches the product catalog from the connected Shopify store, including "
        "each variant's price and current inventory quantity. Use this to find "
        "low-stock or slow-moving products."
    )
    args_schema: type[BaseModel] = ShopifyProductsInput

    def _run(self, limit: int = 20) -> str:
        products = fetch_products(limit)
        if not products:
            return "No products found in this store."

        lines = []
        for edge in products:
            p = edge["node"]
            lines.append(f"\n- {p['title']} (status: {p['status']}, total inventory: {p['totalInventory']})")
            for v_edge in p["variants"]["edges"]:
                v = v_edge["node"]
                lines.append(
                    f"    variant: {v['title']} | price: {v['price']} | "
                    f"qty: {v['inventoryQuantity']} | id: {v['id']}"
                )
        return "\n".join(lines)


class ShopifyOrdersInput(BaseModel):
    limit: int = Field(default=20, description="Max number of recent orders to fetch")


class ShopifyOrdersTool(BaseTool):
    name: str = "get_orders"
    description: str = (
        "Fetches the most recent orders from the connected Shopify store, including "
        "total price and line items. Use this to see which products are actually selling."
    )
    args_schema: type[BaseModel] = ShopifyOrdersInput

    def _run(self, limit: int = 20) -> str:
        query = """
        query ($first: Int!) {
          orders(first: $first, sortKey: CREATED_AT, reverse: true) {
            edges {
              node {
                name
                createdAt
                displayFulfillmentStatus
                totalPriceSet { shopMoney { amount currencyCode } }
                lineItems(first: 5) {
                  edges { node { title quantity } }
                }
              }
            }
          }
        }
        """
        data = _graphql_request(query, {"first": limit})
        orders = data["orders"]["edges"]
        if not orders:
            return "No orders found in this store."

        lines = []
        for edge in orders:
            o = edge["node"]
            total = o["totalPriceSet"]["shopMoney"]
            lines.append(
                f"\n- {o['name']} ({o['createdAt']}, {o['displayFulfillmentStatus']}) "
                f"total: {total['amount']} {total['currencyCode']}"
            )
            for li_edge in o["lineItems"]["edges"]:
                li = li_edge["node"]
                lines.append(f"    item: {li['title']} x{li['quantity']}")
        return "\n".join(lines)
