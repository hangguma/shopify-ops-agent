"""
Tests for executor.py and the store-setup write helpers.

Shopify is mocked at requests.post, like test_shopify_tool.py, so these
check what WE send and how we react - not whether Shopify accepts it. The
first run against the real dev store is a manual check (see README).
"""

import json
import time
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

import executor
import tools.shopify_tool as shopify_tool
from executor import PlanNotApprovable, apply_plan, product_input
from models.schemas import PlannedCollection, PlannedProduct, StorePlan
from tools.shopify_tool import ShopifyUserError, ensure_collection, upsert_product


@pytest.fixture(autouse=True)
def _seed_token_cache():
    shopify_tool._token_cache.update({"access_token": "test-token", "expires_at": time.time() + 3600})
    yield
    shopify_tool._token_cache.update({"access_token": None, "expires_at": 0.0})


def _resp(body: dict) -> MagicMock:
    r = MagicMock()
    r.raise_for_status.return_value = None
    r.json.return_value = body
    return r


def _product(key="oil", **kw) -> PlannedProduct:
    base = dict(key=key, handle=key, title=key.title(), vendor="V", price=Decimal("20.00"),
                product_type="Skincare", description_html="<p>x</p>", tags=["demo-tag"])
    base.update(kw)
    return PlannedProduct(**base)


def _plan(*products) -> StorePlan:
    return StorePlan(
        date="2026-11-12", store_tag="demo-tag",
        collection=PlannedCollection(handle="glow", title="Glow", description_html="<p>c</p>", tag="demo-tag"),
        products=list(products) or [_product("oil"), _product("balm")],
    )


def _product_set_ok(handle: str) -> dict:
    return {"data": {"productSet": {
        "product": {"id": f"gid://shopify/Product/{handle}", "handle": handle, "title": handle, "status": "DRAFT"},
        "userErrors": [],
    }}}


# -- product_input ----------------------------------------------------------

def test_product_input_is_always_a_draft_with_one_default_variant():
    inp = product_input(_product("oil", price=Decimal("19.90")))

    assert inp["status"] == "DRAFT"
    assert inp["handle"] == "oil"
    assert inp["variants"] == [
        {"optionValues": [{"optionName": "Title", "name": "Default Title"}], "price": 19.9}
    ]
    assert inp["productOptions"] == [{"name": "Title", "values": [{"name": "Default Title"}]}]


# -- upsert_product ---------------------------------------------------------

@patch("tools.shopify_tool.requests.post")
def test_upsert_product_identifies_by_handle(mock_post):
    mock_post.return_value = _resp(_product_set_ok("oil"))

    result = upsert_product(product_input(_product("oil")))

    sent = mock_post.call_args.kwargs["json"]
    assert sent["variables"]["identifier"] == {"handle": "oil"}
    assert sent["variables"]["input"]["handle"] == "oil"
    assert "productSet" in sent["query"]
    assert result["id"] == "gid://shopify/Product/oil"


@patch("tools.shopify_tool.requests.post")
def test_upsert_product_raises_on_user_errors(mock_post):
    mock_post.return_value = _resp({"data": {"productSet": {
        "product": None,
        "userErrors": [{"field": ["input", "title"], "message": "Title can't be blank"}],
    }}})

    with pytest.raises(ShopifyUserError, match="Title can't be blank"):
        upsert_product(product_input(_product("oil")))


# -- ensure_collection --------------------------------------------------------

@patch("tools.shopify_tool.requests.post")
def test_existing_collection_is_not_created_again(mock_post):
    mock_post.return_value = _resp({"data": {"collectionByHandle": {"id": "gid://c/1", "handle": "glow", "title": "Glow"}}})

    collection, created = ensure_collection("glow", "Glow", "<p>c</p>", "demo-tag")

    assert (collection["id"], created) == ("gid://c/1", False)
    assert mock_post.call_count == 1  # lookup only, no mutation


@patch("tools.shopify_tool.requests.post")
def test_missing_collection_is_created_with_tag_rule(mock_post):
    mock_post.side_effect = [
        _resp({"data": {"collectionByHandle": None}}),
        _resp({"data": {"collectionCreate": {
            "collection": {"id": "gid://c/2", "handle": "glow", "title": "Glow"}, "userErrors": []}}}),
    ]

    collection, created = ensure_collection("glow", "Glow", "<p>c</p>", "demo-tag")

    assert (collection["id"], created) == ("gid://c/2", True)
    sent = mock_post.call_args.kwargs["json"]["variables"]["input"]
    assert sent["handle"] == "glow"
    assert sent["ruleSet"]["rules"] == [{"column": "TAG", "relation": "EQUALS", "condition": "demo-tag"}]


@patch("tools.shopify_tool.requests.post")
def test_collection_create_raises_on_user_errors(mock_post):
    mock_post.side_effect = [
        _resp({"data": {"collectionByHandle": None}}),
        _resp({"data": {"collectionCreate": {
            "collection": None, "userErrors": [{"field": ["title"], "message": "is too long"}]}}}),
    ]

    with pytest.raises(ShopifyUserError, match="is too long"):
        ensure_collection("glow", "Glow", "<p>c</p>", "demo-tag")


# -- apply_plan ---------------------------------------------------------------

def test_plan_with_needs_review_is_refused_before_any_write(tmp_path):
    plan = _plan(_product("oil"), _product("balm", needs_review=True, review_reason="no copy"))

    with patch.object(executor, "upsert_product") as up, patch.object(executor, "ensure_collection") as ec:
        with pytest.raises(PlanNotApprovable, match="balm"):
            apply_plan(plan, audit_path=str(tmp_path / "audit.jsonl"))

    up.assert_not_called()
    ec.assert_not_called()
    assert not (tmp_path / "audit.jsonl").exists()


def test_apply_writes_products_then_collection_and_logs_each_step(tmp_path):
    audit = tmp_path / "audit.jsonl"
    with patch.object(executor, "upsert_product",
                      side_effect=lambda inp: {"id": f"gid://p/{inp['handle']}", "status": "DRAFT"}) as up, \
         patch.object(executor, "ensure_collection", return_value=({"id": "gid://c/1"}, True)) as ec:
        entries = apply_plan(_plan(), audit_path=str(audit))

    assert [(e["action"], e["handle"], e["status"]) for e in entries] == [
        ("product", "oil", "ok"), ("product", "balm", "ok"), ("collection", "glow", "ok"),
    ]
    assert up.call_count == 2
    ec.assert_called_once_with("glow", "Glow", "<p>c</p>", "demo-tag")

    logged = [json.loads(line) for line in audit.read_text(encoding="utf-8").splitlines()]
    assert logged == entries
    assert logged[2]["detail"] == "created"


def test_apply_stops_at_first_failure_and_marks_the_rest_skipped(tmp_path):
    def fail_on_balm(inp):
        if inp["handle"] == "balm":
            raise ShopifyUserError("productSet(balm) rejected: price: is invalid")
        return {"id": "gid://p/1", "status": "DRAFT"}

    with patch.object(executor, "upsert_product", side_effect=fail_on_balm), \
         patch.object(executor, "ensure_collection") as ec:
        entries = apply_plan(_plan(_product("oil"), _product("balm"), _product("mask")),
                             audit_path=str(tmp_path / "audit.jsonl"))

    assert [(e["handle"], e["status"]) for e in entries] == [
        ("oil", "ok"), ("balm", "error"), ("mask", "skipped"), ("glow", "skipped"),
    ]
    assert "is invalid" in entries[1]["detail"]
    ec.assert_not_called()
