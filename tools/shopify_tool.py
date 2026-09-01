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
- Auth uses the client credentials grant (Dev Dashboard apps have no static
  admin token). Tokens expire after ~24h, so _get_access_token caches the
  token in-process and re-exchanges credentials shortly before expiry.
"""

import time

import requests
from crewai.tools import BaseTool
from pydantic import BaseModel, Field

from config import settings

# Refresh this many seconds before the token's reported expiry.
_TOKEN_REFRESH_MARGIN = 60

_token_cache: dict = {"access_token": None, "expires_at": 0.0}


def _get_access_token() -> str:
    """Return a valid Admin API access token, exchanging credentials if needed."""
    if _token_cache["access_token"] and time.time() < _token_cache["expires_at"]:
        return _token_cache["access_token"]

    resp = requests.post(
        f"https://{settings.SHOPIFY_STORE_DOMAIN}/admin/oauth/access_token",
        data={
            "grant_type": "client_credentials",
            "client_id": settings.SHOPIFY_CLIENT_ID,
            "client_secret": settings.SHOPIFY_CLIENT_SECRET,
        },
        timeout=30,
    )
    resp.raise_for_status()
    body = resp.json()
    if "access_token" not in body:
        raise RuntimeError(f"Shopify token exchange failed: {body}")

    _token_cache["access_token"] = body["access_token"]
    _token_cache["expires_at"] = (
        time.time() + body.get("expires_in", 86400) - _TOKEN_REFRESH_MARGIN
    )
    return _token_cache["access_token"]


def _graphql_request(query: str, variables: dict | None = None) -> dict:
    """POST a query to the Shopify Admin GraphQL API and return `data`."""
    url = (
        f"https://{settings.SHOPIFY_STORE_DOMAIN}/admin/api/"
        f"{settings.SHOPIFY_API_VERSION}/graphql.json"
    )
    headers = {
        "X-Shopify-Access-Token": _get_access_token(),
        "Content-Type": "application/json",
    }
    resp = requests.post(
        url, json={"query": query, "variables": variables or {}}, headers=headers, timeout=30
    )
    resp.raise_for_status()
    body = resp.json()
    if "errors" in body:
        raise RuntimeError(f"Shopify GraphQL error: {body['errors']}")
    return body["data"]


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
        query = """
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
        data = _graphql_request(query, {"first": limit})
        products = data["products"]["edges"]
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
