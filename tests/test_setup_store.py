"""
Tests for the setup_store.py CLI and the planning task prompt.

The approval gate is the point of `apply`, so the tests pin it down: no
confirmation typed -> nothing written.
"""

from decimal import Decimal
from unittest.mock import patch

import pytest

import setup_store
from config import settings
from models.schemas import PlannedCollection, PlannedProduct, StoreBrief, StorePlan
from store_plan import save_plan


@pytest.fixture
def plan_file(tmp_path):
    plan = StorePlan(
        date="2026-11-12", store_tag="demo-tag",
        collection=PlannedCollection(handle="glow", title="Glow", description_html="<p>c</p>", tag="demo-tag"),
        products=[PlannedProduct(key="oil", handle="oil", title="Oil", vendor="V", price=Decimal("20"),
                                 product_type="Skincare", description_html="<p>x</p>", tags=["demo-tag"])],
    )
    with patch.object(settings, "OUTPUT_DIR", str(tmp_path)):
        json_path, _ = save_plan(plan)
    return json_path


@pytest.fixture(autouse=True)
def _env(tmp_path):
    with patch.object(settings, "validate"), \
         patch.object(settings, "OUTPUT_DIR", str(tmp_path)), \
         patch.object(settings, "SHOPIFY_STORE_DOMAIN", "test-store.myshopify.com"), \
         patch.object(setup_store, "setup_logging"):
        yield


def test_apply_without_typed_confirmation_writes_nothing(plan_file):
    with patch("builtins.input", return_value="y"), \
         patch.object(setup_store, "apply_plan") as apply:
        code = setup_store.main(["apply", plan_file])

    assert code == 1
    apply.assert_not_called()


def test_apply_with_confirmation_runs_executor(plan_file):
    ok = [{"action": "product", "handle": "oil", "status": "ok", "detail": "status DRAFT"}]
    with patch("builtins.input", return_value="apply"), \
         patch.object(setup_store, "apply_plan", return_value=ok) as apply:
        code = setup_store.main(["apply", plan_file])

    assert code == 0
    assert apply.call_args.args[0].products[0].handle == "oil"


def test_apply_returns_nonzero_when_a_step_failed(plan_file):
    entries = [{"action": "product", "handle": "oil", "status": "error", "detail": "boom"}]
    with patch("builtins.input", return_value="apply"), \
         patch.object(setup_store, "apply_plan", return_value=entries):
        assert setup_store.main(["apply", plan_file]) == 1


def test_planning_task_lists_every_key_and_no_prices():
    from crewai import Agent

    from tasks.tasks import build_planning_task

    brief = StoreBrief.model_validate({
        "store_tag": "demo-tag", "collection_key": "glow", "collection_title": "Glow",
        "products": [
            {"key": "oil", "title": "Cleansing Oil", "one_liner": "Melts makeup.", "price": "20.55", "vendor": "A"},
            {"key": "set", "title": "Set", "one_liner": "A set.", "price": "40.55", "vendor": "S",
             "bundle_of": ["oil"]},
        ],
    })
    agent = Agent(role="r", goal="g", backstory="b", llm="anthropic/claude-sonnet-5")
    task = build_planning_task(agent, brief)

    assert "key: oil" in task.description and "key: set" in task.description
    assert "세트 구성(key): oil" in task.description
    assert "20.55" not in task.description and "40.55" not in task.description
