"""
Tests for executor.py and the store-setup write helpers (tools/shopify_write.py).

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
import tools.shopify_client as shopify_client
from executor import PlanNotApprovable, apply_plan, product_input
from models.schemas import PlannedCollection, PlannedProduct, StorePlan
from tools.shopify_write import ShopifyUserError, StoreConflict, ensure_collection, upsert_product

POST = "tools.shopify_client.requests.post"


@pytest.fixture(autouse=True)
def _seed_token_cache():
    shopify_client._token_cache.update({"access_token": "test-token", "expires_at": time.time() + 3600})
    yield
    shopify_client._token_cache.update({"access_token": None, "expires_at": 0.0})


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


def _lookup(product: dict | None) -> MagicMock:
    return _resp({"data": {"productByHandle": product}})


def _set_ok(handle: str, status: str = "DRAFT") -> MagicMock:
    return _resp({"data": {"productSet": {
        "product": {"id": f"gid://shopify/Product/{handle}", "handle": handle, "title": handle, "status": status},
        "userErrors": [],
    }}})


def _sent(mock_post, call: int) -> dict:
    return mock_post.call_args_list[call].kwargs["json"]["variables"]


# -- product_input ----------------------------------------------------------

def test_product_input_has_one_default_variant_and_no_status():
    inp = product_input(_product("oil", price=Decimal("19.9")))

    assert "status" not in inp
    assert inp["handle"] == "oil"
    assert inp["variants"] == [
        {"optionValues": [{"optionName": "Title", "name": "Default Title"}], "price": "19.90"}
    ]
    assert inp["productOptions"] == [{"name": "Title", "values": [{"name": "Default Title"}]}]


# -- upsert_product ---------------------------------------------------------

@patch(POST)
def test_new_product_is_created_as_draft(mock_post):
    mock_post.side_effect = [_lookup(None), _set_ok("oil")]

    product, created = upsert_product(product_input(_product("oil")), "demo-tag")

    assert created and product["id"] == "gid://shopify/Product/oil"
    assert _sent(mock_post, 0) == {"handle": "oil"}
    sent = _sent(mock_post, 1)
    assert sent["identifier"] is None
    assert sent["input"]["status"] == "DRAFT"
    assert sent["input"]["handle"] == "oil"


@patch(POST)
def test_rerun_does_not_send_a_published_product_back_to_draft(mock_post):
    existing = {"id": "gid://shopify/Product/77", "handle": "oil", "status": "ACTIVE", "tags": ["Demo-Tag", "glow"]}
    mock_post.side_effect = [_lookup(existing), _set_ok("oil", status="ACTIVE")]

    product, created = upsert_product(product_input(_product("oil")), "demo-tag")

    assert not created and product["status"] == "ACTIVE"
    sent = _sent(mock_post, 1)
    assert sent["identifier"] == {"id": "gid://shopify/Product/77"}
    assert "status" not in sent["input"]


@patch(POST)
def test_product_not_made_by_this_setup_is_never_written(mock_post):
    foreign = {"id": "gid://shopify/Product/5", "handle": "oil", "status": "ACTIVE", "tags": ["snowboard"]}
    mock_post.side_effect = [_lookup(foreign)]

    with pytest.raises(StoreConflict, match="not tagged 'demo-tag'"):
        upsert_product(product_input(_product("oil")), "demo-tag")

    assert mock_post.call_count == 1  # lookup only, no mutation


@patch(POST)
def test_upsert_product_raises_on_user_errors(mock_post):
    mock_post.side_effect = [_lookup(None), _resp({"data": {"productSet": {
        "product": None,
        "userErrors": [{"field": ["input", "title"], "message": "Title can't be blank"}],
    }}})]

    with pytest.raises(ShopifyUserError, match="Title can't be blank"):
        upsert_product(product_input(_product("oil")), "demo-tag")


# -- ensure_collection --------------------------------------------------------

def _collection(rules: list[dict] | None) -> dict:
    rule_set = None if rules is None else {"appliedDisjunctively": False, "rules": rules}
    return {"data": {"collectionByHandle": {"id": "gid://c/1", "handle": "glow", "title": "Glow", "ruleSet": rule_set}}}


TAG_RULE = {"column": "TAG", "relation": "EQUALS", "condition": "demo-tag"}


@patch(POST)
def test_existing_tag_collection_is_not_created_again(mock_post):
    mock_post.return_value = _resp(_collection([TAG_RULE]))

    collection, created = ensure_collection("glow", "Glow", "<p>c</p>", "demo-tag")

    assert (collection["id"], created) == ("gid://c/1", False)
    assert mock_post.call_count == 1  # lookup only, no mutation


@pytest.mark.parametrize("rules", [
    None,                                                                    # manual collection
    [{"column": "TAG", "relation": "EQUALS", "condition": "other-tag"}],     # different tag
    [TAG_RULE, {"column": "VENDOR", "relation": "EQUALS", "condition": "X"}],  # extra rule
])
@patch(POST)
def test_existing_collection_with_other_rules_is_a_conflict(mock_post, rules):
    mock_post.return_value = _resp(_collection(rules))

    with pytest.raises(StoreConflict, match="would not be in it"):
        ensure_collection("glow", "Glow", "<p>c</p>", "demo-tag")

    assert mock_post.call_count == 1


@patch(POST)
def test_missing_collection_is_created_with_tag_rule(mock_post):
    mock_post.side_effect = [
        _resp({"data": {"collectionByHandle": None}}),
        _resp({"data": {"collectionCreate": {
            "collection": {"id": "gid://c/2", "handle": "glow", "title": "Glow"}, "userErrors": []}}}),
    ]

    collection, created = ensure_collection("glow", "Glow", "<p>c</p>", "demo-tag")

    assert (collection["id"], created) == ("gid://c/2", True)
    sent = _sent(mock_post, 1)["input"]
    assert sent["handle"] == "glow"
    assert sent["ruleSet"]["rules"] == [TAG_RULE]


@patch(POST)
def test_collection_create_raises_on_user_errors(mock_post):
    mock_post.side_effect = [
        _resp({"data": {"collectionByHandle": None}}),
        _resp({"data": {"collectionCreate": {
            "collection": None, "userErrors": [{"field": ["title"], "message": "is too long"}]}}}),
    ]

    with pytest.raises(ShopifyUserError, match="is too long"):
        ensure_collection("glow", "Glow", "<p>c</p>", "demo-tag")


# -- apply_plan ---------------------------------------------------------------

def _assert_nothing_written(plan, tmp_path, match):
    with patch.object(executor, "upsert_product") as up, patch.object(executor, "ensure_collection") as ec:
        with pytest.raises(PlanNotApprovable, match=match):
            apply_plan(plan, audit_path=str(tmp_path / "audit.jsonl"))
    up.assert_not_called()
    ec.assert_not_called()
    assert not (tmp_path / "audit.jsonl").exists()


def test_plan_with_needs_review_is_refused_before_any_write(tmp_path):
    plan = _plan(_product("oil"), _product("balm", needs_review=True, review_reason="no copy"))
    _assert_nothing_written(plan, tmp_path, "balm: marked needs_review")


def test_hand_edited_plan_is_checked_again_before_any_write(tmp_path):
    _assert_nothing_written(
        _plan(_product("oil", description_html="<p>ok</p><script>alert(1)</script>")),
        tmp_path, "HTML that is not allowed")
    _assert_nothing_written(
        _plan(_product("oil", description_html="<p>Heals acne.</p>")), tmp_path, "medical-sounding claim")
    _assert_nothing_written(
        _plan(_product("oil", tags=["glow"])), tmp_path, "tags must include 'demo-tag'")


def test_apply_writes_products_then_collection_and_logs_each_step(tmp_path):
    audit = tmp_path / "audit.jsonl"

    def fake_upsert(inp, store_tag):
        assert store_tag == "demo-tag"
        created = inp["handle"] == "oil"
        return {"id": f"gid://p/{inp['handle']}", "status": "DRAFT" if created else "ACTIVE"}, created

    with patch.object(executor, "upsert_product", side_effect=fake_upsert) as up, \
         patch.object(executor, "ensure_collection", return_value=({"id": "gid://c/1"}, True)) as ec:
        entries = apply_plan(_plan(), audit_path=str(audit))

    assert [(e["action"], e["handle"], e["status"]) for e in entries] == [
        ("product", "oil", "ok"), ("product", "balm", "ok"), ("collection", "glow", "ok"),
    ]
    assert entries[0]["detail"] == "created, status DRAFT"
    assert entries[1]["detail"] == "updated, status ACTIVE"
    assert up.call_count == 2
    ec.assert_called_once_with("glow", "Glow", "<p>c</p>", "demo-tag")

    logged = [json.loads(line) for line in audit.read_text(encoding="utf-8").splitlines()]
    assert logged == entries
    assert logged[2]["detail"] == "created"


def test_apply_stops_at_first_failure_and_marks_the_rest_skipped(tmp_path):
    def conflict_on_balm(inp, store_tag):
        if inp["handle"] == "balm":
            raise StoreConflict("A product with handle 'balm' already exists and is not tagged 'demo-tag'")
        return {"id": "gid://p/1", "status": "DRAFT"}, True

    with patch.object(executor, "upsert_product", side_effect=conflict_on_balm), \
         patch.object(executor, "ensure_collection") as ec:
        entries = apply_plan(_plan(_product("oil"), _product("balm"), _product("mask")),
                             audit_path=str(tmp_path / "audit.jsonl"))

    assert [(e["handle"], e["status"]) for e in entries] == [
        ("oil", "ok"), ("balm", "error"), ("mask", "skipped"), ("glow", "skipped"),
    ]
    assert "not tagged" in entries[1]["detail"]
    ec.assert_not_called()
