"""
Shopify Admin API client - auth and the shared GraphQL request helper.

Why a separate module?
- Both the read tools (tools/shopify_tool.py, CrewAI) and the store-setup
  writes (tools/shopify_write.py, plain code) need the same token handling
  and request helper. Keeping it here means the write path does not import
  crewai: `setup_store.py apply` runs without loading the LLM stack.
- Auth uses the client credentials grant (Dev Dashboard apps have no static
  admin token). Tokens expire after ~24h, so _get_access_token caches the
  token in-process and re-exchanges credentials shortly before expiry.
"""

import time

import requests

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
