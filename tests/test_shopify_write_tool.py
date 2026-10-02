"""
Unit tests for the Shopify write layer (Phase 2A).

Like test_shopify_tool.py, requests.post is mocked: we verify the GraphQL we
send and how responses (including userErrors) are interpreted, not Shopify.
"""

import time
from unittest.mock import MagicMock, patch

import pytest

import tools.shopify_tool as shopify_tool
import tools.shopify_write_tool as write_tool
from models.store_plan import CollectionDraft, PageDraft, ProductDraft


@pytest.fixture(autouse=True)
def _seed_token_cache():
    shopify_tool._token_cache.update({"access_token": "test-token", "expires_at": time.time() + 3600})
    yield
    shopify_tool._token_cache.update({"access_token": None, "expires_at": 0.0})


def _resp(data: dict) -> MagicMock:
    r = MagicMock()
    r.raise_for_status.return_value = None
    r.json.return_value = {"data": data}
    return r


def _sent(mock_post) -> dict:
    return mock_post.call_args.kwargs["json"]


@patch("tools.shopify_tool.requests.post")
def test_upsert_product_uses_handle_identifier_and_price(mock_post):
    mock_post.return_value = _resp(
        {"productSet": {"product": {"id": "gid://shopify/Product/9", "handle": "p"}, "userErrors": []}}
    )
    draft = ProductDraft(handle="p", title="P", description_html="<p>x</p>", price="24")

    product_id = write_tool.upsert_product(draft, ["gid://shopify/Collection/1"])

    assert product_id == "gid://shopify/Product/9"
    sent = _sent(mock_post)
    assert "productSet" in sent["query"]
    assert sent["variables"]["identifier"] == {"handle": "p"}
    product_input = sent["variables"]["input"]
    assert product_input["variants"][0]["price"] == "24.00"
    assert product_input["collections"] == ["gid://shopify/Collection/1"]
    assert product_input["status"] == "ACTIVE"
    assert "seo" not in product_input  # omitted when the draft has no SEO fields


@patch("tools.shopify_tool.requests.post")
def test_user_errors_raise(mock_post):
    mock_post.return_value = _resp(
        {"collectionCreate": {"collection": None,
                              "userErrors": [{"field": ["handle"], "message": "has already been taken"}]}}
    )
    with pytest.raises(write_tool.ShopifyUserError, match="handle: has already been taken"):
        write_tool.create_collection(CollectionDraft(handle="c", title="C"))


@patch("tools.shopify_tool.requests.post")
def test_create_collection_sends_new_input_shape(mock_post):
    mock_post.return_value = _resp(
        {"collectionCreate": {"collection": {"id": "gid://shopify/Collection/5", "handle": "c"}, "userErrors": []}}
    )
    assert write_tool.create_collection(CollectionDraft(handle="c", title="C")) == "gid://shopify/Collection/5"
    assert _sent(mock_post)["variables"]["collection"]["handle"] == "c"


@patch("tools.shopify_tool.requests.post")
def test_find_collection_returns_none_when_missing(mock_post):
    mock_post.return_value = _resp({"collectionByIdentifier": None})
    assert write_tool.find_collection_by_handle("nope") is None


@patch("tools.shopify_tool.requests.post")
def test_find_page_requires_exact_handle_match(mock_post):
    # Shopify's page search is fuzzy: "about" can return "about-us".
    mock_post.return_value = _resp({"pages": {"nodes": [{"id": "gid://shopify/Page/1", "handle": "about-us"}]}})
    assert write_tool.find_page_by_handle("about") is None


@patch("tools.shopify_tool.requests.post")
def test_create_page_publishes(mock_post):
    mock_post.return_value = _resp(
        {"pageCreate": {"page": {"id": "gid://shopify/Page/3", "handle": "faq"}, "userErrors": []}}
    )
    write_tool.create_page(PageDraft(handle="faq", title="FAQ", body_html="<p>?</p>"))
    page = _sent(mock_post)["variables"]["page"]
    assert page["isPublished"] is True
    assert page["body"] == "<p>?</p>"


@patch("tools.shopify_tool.requests.post")
def test_online_store_publication_found_by_name(mock_post):
    mock_post.return_value = _resp(
        {"publications": {"nodes": [
            {"id": "gid://shopify/Publication/1", "name": "Point of Sale"},
            {"id": "gid://shopify/Publication/2", "name": "Online Store"},
        ]}}
    )
    assert write_tool.get_online_store_publication_id() == "gid://shopify/Publication/2"


@patch("tools.shopify_tool.requests.post")
def test_publication_id_override_skips_lookup(mock_post, monkeypatch):
    monkeypatch.setattr(write_tool.settings, "SHOPIFY_ONLINE_STORE_PUBLICATION_ID", "gid://shopify/Publication/7")
    assert write_tool.get_online_store_publication_id() == "gid://shopify/Publication/7"
    mock_post.assert_not_called()


@patch("tools.shopify_tool.requests.post")
def test_missing_online_store_publication_raises(mock_post):
    mock_post.return_value = _resp({"publications": {"nodes": []}})
    with pytest.raises(RuntimeError, match="Online Store publication not found"):
        write_tool.get_online_store_publication_id()
