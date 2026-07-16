"""
Unit tests for the Shopify Admin API tools.

Mocks requests.post instead of calling the real Shopify API.
Why?
- Tests that depend on a live store are slow, flaky, and need real credentials.
- We verify OUR parsing/error-handling logic, not whether Shopify is up.
- The token exchange goes through the same requests.post as GraphQL calls,
  so the autouse fixture pre-seeds the token cache: tool tests then only
  exercise the GraphQL request. Token-exchange tests clear the cache first.
"""

import time
from unittest.mock import MagicMock, patch

import pytest

import tools.shopify_tool as shopify_tool
from tools.shopify_tool import ShopifyOrdersTool, ShopifyProductsTool


@pytest.fixture(autouse=True)
def _seed_token_cache():
    shopify_tool._token_cache.update(
        {"access_token": "test-token", "expires_at": time.time() + 3600}
    )
    yield
    shopify_tool._token_cache.update({"access_token": None, "expires_at": 0.0})


def _clear_token_cache():
    shopify_tool._token_cache.update({"access_token": None, "expires_at": 0.0})


def _mock_response(json_body: dict) -> MagicMock:
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = json_body
    return resp


def _fake_products_body(n: int) -> dict:
    edges = [
        {
            "node": {
                "id": f"gid://shopify/Product/{i}",
                "title": f"Widget {i}",
                "status": "ACTIVE",
                "totalInventory": 3,
                "variants": {
                    "edges": [
                        {
                            "node": {
                                "id": f"gid://shopify/ProductVariant/{i}",
                                "title": "Default",
                                "price": "19.99",
                                "inventoryQuantity": 3,
                            }
                        }
                    ]
                },
            }
        }
        for i in range(n)
    ]
    return {"data": {"products": {"edges": edges}}}


def _fake_orders_body(n: int) -> dict:
    edges = [
        {
            "node": {
                "name": f"#100{i}",
                "createdAt": "2026-06-30T00:00:00Z",
                "displayFulfillmentStatus": "UNFULFILLED",
                "totalPriceSet": {"shopMoney": {"amount": "39.98", "currencyCode": "USD"}},
                "lineItems": {"edges": [{"node": {"title": "Widget", "quantity": 2}}]},
            }
        }
        for i in range(n)
    ]
    return {"data": {"orders": {"edges": edges}}}


# -- ShopifyProductsTool -----------------------------------------------

@patch("tools.shopify_tool.requests.post")
def test_products_tool_formats_catalog(mock_post):
    mock_post.return_value = _mock_response(_fake_products_body(2))

    out = ShopifyProductsTool()._run(limit=20)

    assert "Widget 0" in out
    assert "Widget 1" in out
    assert "price: 19.99" in out
    assert "qty: 3" in out


@patch("tools.shopify_tool.requests.post")
def test_products_tool_empty_catalog(mock_post):
    mock_post.return_value = _mock_response({"data": {"products": {"edges": []}}})

    out = ShopifyProductsTool()._run(limit=20)

    assert out == "No products found in this store."


@patch("tools.shopify_tool.requests.post")
def test_products_tool_passes_limit_as_graphql_variable(mock_post):
    mock_post.return_value = _mock_response(_fake_products_body(1))

    ShopifyProductsTool()._run(limit=5)

    sent = mock_post.call_args.kwargs["json"]
    assert sent["variables"]["first"] == 5


# -- ShopifyOrdersTool ---------------------------------------------------

@patch("tools.shopify_tool.requests.post")
def test_orders_tool_formats_orders(mock_post):
    mock_post.return_value = _mock_response(_fake_orders_body(1))

    out = ShopifyOrdersTool()._run(limit=20)

    assert "#1000" in out
    assert "39.98 USD" in out
    assert "Widget x2" in out


@patch("tools.shopify_tool.requests.post")
def test_orders_tool_empty_orders(mock_post):
    mock_post.return_value = _mock_response({"data": {"orders": {"edges": []}}})

    out = ShopifyOrdersTool()._run(limit=20)

    assert out == "No orders found in this store."


# -- Token exchange (_get_access_token) ----------------------------------

@patch("tools.shopify_tool.requests.post")
def test_token_exchanged_via_client_credentials(mock_post):
    _clear_token_cache()
    mock_post.return_value = _mock_response(
        {"access_token": "fresh-token", "expires_in": 86400}
    )

    token = shopify_tool._get_access_token()

    assert token == "fresh-token"
    sent = mock_post.call_args.kwargs["data"]
    assert sent["grant_type"] == "client_credentials"
    assert "/admin/oauth/access_token" in mock_post.call_args.args[0]


@patch("tools.shopify_tool.requests.post")
def test_token_cached_until_expiry(mock_post):
    _clear_token_cache()
    mock_post.return_value = _mock_response(
        {"access_token": "fresh-token", "expires_in": 86400}
    )

    shopify_tool._get_access_token()
    shopify_tool._get_access_token()

    assert mock_post.call_count == 1  # second call served from cache


@patch("tools.shopify_tool.requests.post")
def test_expired_token_is_reexchanged(mock_post):
    mock_post.return_value = _mock_response(
        {"access_token": "fresh-token", "expires_in": 86400}
    )
    shopify_tool._token_cache.update(
        {"access_token": "stale-token", "expires_at": time.time() - 1}
    )

    token = shopify_tool._get_access_token()

    assert token == "fresh-token"
    assert mock_post.call_count == 1


@patch("tools.shopify_tool.requests.post")
def test_token_exchange_failure_raises(mock_post):
    _clear_token_cache()
    mock_post.return_value = _mock_response({"error": "invalid_client"})

    with pytest.raises(RuntimeError, match="token exchange failed"):
        shopify_tool._get_access_token()


# -- Shared error handling (_graphql_request) ----------------------------

@patch("tools.shopify_tool.requests.post")
def test_graphql_errors_raise_runtime_error(mock_post):
    mock_post.return_value = _mock_response({"errors": [{"message": "Invalid API key"}]})

    with pytest.raises(RuntimeError, match="Invalid API key"):
        ShopifyProductsTool()._run(limit=20)


@patch("tools.shopify_tool.requests.post")
def test_http_error_propagates(mock_post):
    resp = MagicMock()
    resp.raise_for_status.side_effect = Exception("503 Service Unavailable")
    mock_post.return_value = resp

    with pytest.raises(Exception, match="503"):
        ShopifyOrdersTool()._run(limit=20)
