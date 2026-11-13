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
from tools.shopify_write import (
    ShopifyUserError,
    StoreConflict,
    create_collection,
    find_collection,
    find_product,
    write_product,
)

POST = "tools.shopify_client.requests.post"
TAG_RULE = {"column": "TAG", "relation": "EQUALS", "condition": "demo-tag"}


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


def _set_ok(handle: str, status: str = "DRAFT") -> MagicMock:
    return _resp({"data": {"productSet": {
        "product": {"id": f"gid://shopify/Product/{handle}", "handle": handle, "title": handle, "status": status},
        "userErrors": [],
    }}})


def _sent(mock_post, call: int = 0) -> dict:
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


def test_product_input_sends_the_cleaned_description():
    inp = product_input(_product("oil", description_html="<P>It&rsquo;s nice<br/>really</P>"))
    assert inp["descriptionHtml"] == "<p>It’s nice<br>really</p>"


# -- find_product (read-only) -------------------------------------------------

@patch(POST)
def test_find_product_returns_none_when_handle_is_free(mock_post):
    mock_post.return_value = _resp({"data": {"productByHandle": None}})

    assert find_product("oil", "demo-tag") is None
    assert _sent(mock_post) == {"handle": "oil"}


@patch(POST)
def test_find_product_returns_a_product_this_setup_made(mock_post):
    ours = {"id": "gid://shopify/Product/77", "handle": "oil", "status": "ACTIVE", "tags": ["Demo-Tag", "glow"]}
    mock_post.return_value = _resp({"data": {"productByHandle": ours}})

    assert find_product("oil", "demo-tag") == ours


@patch(POST)
def test_find_product_refuses_a_product_this_setup_did_not_make(mock_post):
    foreign = {"id": "gid://shopify/Product/5", "handle": "oil", "status": "ACTIVE", "tags": ["snowboard"]}
    mock_post.return_value = _resp({"data": {"productByHandle": foreign}})

    with pytest.raises(StoreConflict, match="not tagged 'demo-tag'"):
        find_product("oil", "demo-tag")


@patch(POST)
def test_lookup_field_missing_from_schema_is_an_error_not_a_free_handle(mock_post):
    # If productByHandle did not exist in the pinned API version, Shopify
    # would answer with GraphQL errors. That must stop the run, not read as
    # "no such product" and send apply down the create path.
    mock_post.return_value = _resp({"errors": [{"message": "Field 'productByHandle' doesn't exist on type 'QueryRoot'"}]})

    with pytest.raises(RuntimeError, match="productByHandle"):
        find_product("oil", "demo-tag")


# -- write_product -------------------------------------------------------------

@patch(POST)
def test_new_product_is_created_as_draft(mock_post):
    mock_post.return_value = _set_ok("oil")

    product, created = write_product(product_input(_product("oil")), None)

    assert created and product["id"] == "gid://shopify/Product/oil"
    sent = _sent(mock_post)
    assert sent["identifier"] is None
    assert sent["input"]["status"] == "DRAFT"
    assert sent["input"]["handle"] == "oil"


@patch(POST)
def test_rerun_does_not_send_a_published_product_back_to_draft(mock_post):
    existing = {"id": "gid://shopify/Product/77", "handle": "oil", "status": "ACTIVE", "tags": ["demo-tag"]}
    mock_post.return_value = _set_ok("oil", status="ACTIVE")

    product, created = write_product(product_input(_product("oil")), existing)

    assert not created and product["status"] == "ACTIVE"
    sent = _sent(mock_post)
    assert sent["identifier"] == {"id": "gid://shopify/Product/77"}
    assert "status" not in sent["input"]


@patch(POST)
def test_write_product_raises_on_user_errors(mock_post):
    mock_post.return_value = _resp({"data": {"productSet": {
        "product": None,
        "userErrors": [{"field": ["input", "title"], "message": "Title can't be blank"}],
    }}})

    with pytest.raises(ShopifyUserError, match="Title can't be blank"):
        write_product(product_input(_product("oil")), None)


# -- find_collection / create_collection ---------------------------------------

def _collection(rules: list[dict] | None) -> dict:
    rule_set = None if rules is None else {"appliedDisjunctively": False, "rules": rules}
    return {"data": {"collectionByHandle": {"id": "gid://c/1", "handle": "glow", "title": "Glow", "ruleSet": rule_set}}}


@patch(POST)
def test_find_collection_accepts_the_tag_collection(mock_post):
    mock_post.return_value = _resp(_collection([TAG_RULE]))
    assert find_collection("glow", "demo-tag")["id"] == "gid://c/1"


@patch(POST)
def test_find_collection_returns_none_when_handle_is_free(mock_post):
    mock_post.return_value = _resp({"data": {"collectionByHandle": None}})
    assert find_collection("glow", "demo-tag") is None


@pytest.mark.parametrize("rules", [
    None,                                                                    # manual collection
    [{"column": "TAG", "relation": "EQUALS", "condition": "other-tag"}],     # different tag
    [TAG_RULE, {"column": "VENDOR", "relation": "EQUALS", "condition": "X"}],  # extra rule
])
@patch(POST)
def test_existing_collection_with_other_rules_is_a_conflict(mock_post, rules):
    mock_post.return_value = _resp(_collection(rules))

    with pytest.raises(StoreConflict, match="would not be in it"):
        find_collection("glow", "demo-tag")


@patch(POST)
def test_collection_is_created_with_tag_rule(mock_post):
    mock_post.return_value = _resp({"data": {"collectionCreate": {
        "collection": {"id": "gid://c/2", "handle": "glow", "title": "Glow"}, "userErrors": []}}})

    collection = create_collection("glow", "Glow", "<p>c</p>", "demo-tag")

    assert collection["id"] == "gid://c/2"
    sent = _sent(mock_post)["input"]
    assert sent["handle"] == "glow"
    assert sent["ruleSet"]["rules"] == [TAG_RULE]


@patch(POST)
def test_collection_create_raises_on_user_errors(mock_post):
    mock_post.return_value = _resp({"data": {"collectionCreate": {
        "collection": None, "userErrors": [{"field": ["title"], "message": "is too long"}]}}})

    with pytest.raises(ShopifyUserError, match="is too long"):
        create_collection("glow", "Glow", "<p>c</p>", "demo-tag")


# -- apply_plan ---------------------------------------------------------------

class FakeStore:
    """Stands in for the four shopify_write functions and records every write."""

    def __init__(self, products=None, collection=None, product_conflicts=(), collection_conflict=False,
                 fail_write_on=None):
        self.products = dict(products or {})          # handle -> existing product
        self.collection = collection
        self.product_conflicts = set(product_conflicts)
        self.collection_conflict = collection_conflict
        self.fail_write_on = fail_write_on
        self.lookups: list[str] = []
        self.writes: list[str] = []

    def find_product(self, handle, store_tag):
        self.lookups.append(handle)
        if handle in self.product_conflicts:
            raise StoreConflict(f"A product with handle '{handle}' already exists and is not tagged '{store_tag}'")
        return self.products.get(handle)

    def find_collection(self, handle, tag):
        self.lookups.append(handle)
        if self.collection_conflict:
            raise StoreConflict(f"A collection with handle '{handle}' already exists but is not the tag collection")
        return self.collection

    def write_product(self, inp, existing):
        if inp["handle"] == self.fail_write_on:
            raise ShopifyUserError(f"productSet({inp['handle']}) rejected: price: is invalid")
        self.writes.append(inp["handle"])
        status = existing["status"] if existing else "DRAFT"
        return {"id": f"gid://p/{inp['handle']}", "status": status}, existing is None

    def create_collection(self, handle, title, description_html, tag):
        self.writes.append(handle)
        return {"id": "gid://c/new"}

    def patched(self):
        return patch.multiple(
            executor, find_product=self.find_product, find_collection=self.find_collection,
            write_product=self.write_product, create_collection=self.create_collection,
        )


def _apply(store: FakeStore, plan: StorePlan, tmp_path):
    with store.patched():
        return apply_plan(plan, audit_path=str(tmp_path / "audit.jsonl"))


def _assert_plan_refused(plan, tmp_path, match):
    store = FakeStore()
    with store.patched(), pytest.raises(PlanNotApprovable, match=match):
        apply_plan(plan, audit_path=str(tmp_path / "audit.jsonl"))
    assert store.lookups == [] and store.writes == []
    assert not (tmp_path / "audit.jsonl").exists()


def test_plan_with_needs_review_is_refused_before_any_lookup_or_write(tmp_path):
    plan = _plan(_product("oil"), _product("balm", needs_review=True, review_reason="no copy"))
    _assert_plan_refused(plan, tmp_path, "balm: marked needs_review")


def test_hand_edited_plan_is_checked_again_before_any_write(tmp_path):
    _assert_plan_refused(
        _plan(_product("oil", description_html="<p>ok</p><script>alert(1)</script>")),
        tmp_path, "uses <script>, which is not allowed")
    _assert_plan_refused(
        _plan(_product("oil", description_html="<p>Heals acne.</p>")), tmp_path, "makes a medical claim")
    _assert_plan_refused(
        _plan(_product("oil", tags=["glow"])), tmp_path, "tags must include 'demo-tag'")


def test_apply_writes_products_then_collection_and_logs_each_step(tmp_path):
    store = FakeStore(products={"balm": {"id": "gid://p/balm", "status": "ACTIVE", "tags": ["demo-tag"]}})

    entries = _apply(store, _plan(), tmp_path)

    assert [(e["action"], e["handle"], e["status"]) for e in entries] == [
        ("product", "oil", "ok"), ("product", "balm", "ok"), ("collection", "glow", "ok"),
    ]
    assert entries[0]["detail"] == "created, status DRAFT"
    assert entries[1]["detail"] == "updated, status ACTIVE"
    assert entries[2]["detail"] == "created"
    assert store.lookups == ["oil", "balm", "glow"]     # every lookup happens...
    assert store.writes == ["oil", "balm", "glow"]      # ...before the writes

    logged = [json.loads(line) for line in (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()]
    assert logged == entries


def test_existing_tag_collection_is_left_as_is(tmp_path):
    store = FakeStore(collection={"id": "gid://c/1"})

    entries = _apply(store, _plan(), tmp_path)

    assert entries[-1]["detail"] == "already existed, left as is"
    assert store.writes == ["oil", "balm"]


def test_collection_conflict_is_found_before_any_product_is_written(tmp_path):
    # The case from the review: the collection handle is taken by a manual
    # collection. Nothing may be written, including the products before it.
    store = FakeStore(collection_conflict=True)

    entries = _apply(store, _plan(), tmp_path)

    assert store.writes == []
    assert [(e["handle"], e["status"], e["phase"]) for e in entries] == [
        ("oil", "skipped", "preflight"), ("balm", "skipped", "preflight"), ("glow", "error", "preflight"),
    ]
    assert "already exists" in entries[2]["detail"]


def test_product_conflict_on_a_later_product_writes_nothing(tmp_path):
    store = FakeStore(product_conflicts={"mask"})

    entries = _apply(store, _plan(_product("oil"), _product("balm"), _product("mask")), tmp_path)

    assert store.writes == []
    assert store.lookups == ["oil", "balm", "mask", "glow"]   # all checked, so every conflict is reported at once
    assert [(e["handle"], e["status"]) for e in entries] == [
        ("oil", "skipped"), ("balm", "skipped"), ("mask", "error"), ("glow", "skipped"),
    ]


def test_failed_lookup_blocks_the_run_like_a_conflict(tmp_path):
    store = FakeStore()
    store.find_collection = MagicMock(side_effect=RuntimeError("Shopify GraphQL error: Field doesn't exist"))

    entries = _apply(store, _plan(), tmp_path)

    assert store.writes == []
    assert entries[-1]["status"] == "error" and "GraphQL error" in entries[-1]["detail"]


def test_write_failure_stops_the_run_and_marks_the_rest_skipped(tmp_path):
    store = FakeStore(fail_write_on="balm")

    entries = _apply(store, _plan(_product("oil"), _product("balm"), _product("mask")), tmp_path)

    assert [(e["handle"], e["status"]) for e in entries] == [
        ("oil", "ok"), ("balm", "error"), ("mask", "skipped"), ("glow", "skipped"),
    ]
    assert "is invalid" in entries[1]["detail"]
    assert store.writes == ["oil"]
