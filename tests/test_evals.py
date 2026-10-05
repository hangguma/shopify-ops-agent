"""
Tests for the eval harness itself (no LLM calls).

The harness has to be trustworthy before its pass rates mean anything:
fixtures must reach the real tools in the right shape, and the scorer must
catch misses, false flags and fabricated data.
"""

from unittest.mock import patch

from evals.dataset import Scenario
from evals.scorer import score
from evals.solver import _fake_graphql
from models.schemas import AnalysisReport, FlaggedProduct
from tools.shopify_tool import ShopifyOrdersTool, ShopifyProductsTool

LOW = "gid://shopify/ProductVariant/1"
SLOW = "gid://shopify/ProductVariant/2"
HEALTHY = "gid://shopify/ProductVariant/3"


def make_scenario() -> Scenario:
    return Scenario.model_validate({
        "name": "harness-test",
        "why": "harness self-test",
        "products": [
            {"title": "Low", "variants": [{"id": LOW, "price": "10.00", "qty": 2}]},
            {"title": "Slow", "variants": [{"id": SLOW, "price": "20.00", "qty": 40}]},
            {"title": "Healthy", "variants": [{"id": HEALTHY, "price": "5.00", "qty": 30}]},
        ],
        "orders": [
            {"name": "#1", "created_at": "2026-10-01T00:00:00Z",
             "line_items": [{"title": "Low", "quantity": 1}]},
            {"name": "#2", "created_at": "2026-10-02T00:00:00Z",
             "line_items": [{"title": "Healthy", "quantity": 2}, {"title": "Low", "quantity": 1}]},
        ],
        "expect": {"must_flag": [LOW, SLOW], "must_not_flag": [HEALTHY]},
    })


def report(*flags: tuple[str, int]) -> AnalysisReport:
    return AnalysisReport(
        date="2026-10-05",
        flagged_products=[
            FlaggedProduct(title="x", variant_id=vid, reason="r", inventory_quantity=qty) for vid, qty in flags
        ],
        markdown="",
    )


def test_fixture_reaches_real_tools_in_production_format():
    s = make_scenario()
    with patch("tools.shopify_tool._graphql_request", side_effect=_fake_graphql(s)):
        products = ShopifyProductsTool()._run(limit=50)
        orders = ShopifyOrdersTool()._run(limit=20)

    assert f"qty: 2 | id: {LOW}" in products
    assert "total inventory: 40" in products
    # newest first, total computed from fixture prices (2*5 + 1*10)
    assert orders.index("#2") < orders.index("#1")
    assert "total: 20.00 USD" in orders
    assert "item: Healthy x2" in orders


def test_fixture_respects_limit():
    s = make_scenario()
    assert len(s.products_response(first=2)["products"]["edges"]) == 2
    assert len(s.orders_response(first=1)["orders"]["edges"]) == 1


def test_correct_report_passes():
    assert score(make_scenario(), report((LOW, 2), (SLOW, 40))).passed


def test_missed_and_false_flags_fail():
    result = score(make_scenario(), report((LOW, 2), (HEALTHY, 30)))
    assert f"missed: {SLOW}" in result.failures
    assert f"false flag: {HEALTHY}" in result.failures


def test_fabricated_variant_and_wrong_qty_fail():
    result = score(make_scenario(), report((LOW, 3), (SLOW, 40), ("gid://shopify/ProductVariant/999", 1)))
    assert any(f.startswith("wrong qty") for f in result.failures)
    assert any(f.startswith("unknown variant id") for f in result.failures)


def test_example_files_are_not_loaded():
    from evals.dataset import load_scenarios
    assert all(s.name != "format-example" for s in load_scenarios())


def test_right_pick_with_garbled_id_is_a_fidelity_failure_only():
    r = AnalysisReport(date="", markdown="", flagged_products=[
        FlaggedProduct(title="Low (Default Title)", variant_id="<UNKNOWN>", reason="r", inventory_quantity=2),
        FlaggedProduct(title="Slow", variant_id=SLOW, reason="r", inventory_quantity=40),
    ])
    result = score(make_scenario(), r)
    assert result.judgment_passed
    assert not result.fidelity_passed
    assert any(f.startswith("wrong variant id") for f in result.fidelity_failures)


def test_flag_outside_the_store_fails_both():
    r = AnalysisReport(date="", markdown="", flagged_products=[
        FlaggedProduct(title="Ghost Product", variant_id="<UNKNOWN>", reason="r", inventory_quantity=1),
    ])
    result = score(make_scenario(), r)
    assert not result.judgment_passed and not result.fidelity_passed
