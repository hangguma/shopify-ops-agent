"""
Tests for the setup_store.py CLI and the planning task prompt.

The approval gate is the point of `apply`, so the tests pin it down: no
confirmation typed -> nothing written, and a plan that would be refused is
refused before anyone is asked to approve it.
"""

import json
import logging
import subprocess
import sys
from datetime import date
from decimal import Decimal
from unittest.mock import patch

import pytest

import setup_store
from config import settings
from models.schemas import PlannedCollection, PlannedProduct, StoreBrief, StorePlan
from store_plan import plan_paths, save_plan


def _plan(day="2026-11-12", **product_overrides) -> StorePlan:
    product = dict(key="oil", handle="oil", title="Oil", vendor="V", price=Decimal("20"),
                   product_type="Skincare", description_html="<p>x</p>", tags=["demo-tag"])
    product.update(product_overrides)
    return StorePlan(
        date=day, store_tag="demo-tag",
        collection=PlannedCollection(handle="glow", title="Glow", description_html="<p>c</p>", tag="demo-tag"),
        products=[PlannedProduct(**product)],
    )


@pytest.fixture(autouse=True)
def _env(tmp_path):
    with patch.object(settings, "OUTPUT_DIR", str(tmp_path)), \
         patch.object(settings, "ANTHROPIC_API_KEY", "sk-ant-test"), \
         patch.object(settings, "SHOPIFY_STORE_DOMAIN", "test-store.myshopify.com"), \
         patch.object(settings, "SHOPIFY_CLIENT_ID", "id"), \
         patch.object(settings, "SHOPIFY_CLIENT_SECRET", "secret"), \
         patch.object(setup_store, "setup_logging", return_value=logging.getLogger("test_setup_store")):
        yield


@pytest.fixture
def plan_file():
    return save_plan(_plan())[0]


@pytest.fixture
def brief_file(tmp_path):
    path = tmp_path / "brief.yaml"
    path.write_text(
        "store_tag: demo-tag\ncollection_key: glow\ncollection_title: Glow\n"
        "products:\n  - {key: oil, title: Oil, one_liner: Melts makeup., price: 20.00, vendor: V}\n",
        encoding="utf-8",
    )
    return str(path)


# -- apply --------------------------------------------------------------------

def test_apply_without_typed_confirmation_writes_nothing(plan_file):
    with patch("builtins.input", return_value="y"), \
         patch.object(setup_store, "apply_plan") as apply:
        code = setup_store.main(["apply", plan_file])

    assert code == 1
    apply.assert_not_called()


def test_apply_with_confirmation_runs_executor(plan_file):
    ok = [{"action": "product", "handle": "oil", "status": "ok", "detail": "created, status DRAFT"}]
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


@pytest.mark.parametrize("overrides", [
    {"needs_review": True, "review_reason": "no copy"},
    {"description_html": "<p>Heals acne.</p>"},
    {"tags": ["glow"]},
])
def test_unapplicable_plan_is_refused_before_the_prompt(overrides, caplog):
    path = save_plan(_plan(**overrides))[0]
    with patch("builtins.input") as ask, patch.object(setup_store, "apply_plan") as apply, \
         caplog.at_level(logging.ERROR):
        code = setup_store.main(["apply", path])

    assert code == 1
    ask.assert_not_called()      # never asked to approve a plan that will be refused
    apply.assert_not_called()
    assert "cannot be applied yet" in caplog.text


def test_apply_does_not_need_the_anthropic_key(plan_file):
    with patch.object(settings, "ANTHROPIC_API_KEY", ""), \
         patch("builtins.input", return_value="apply"), \
         patch.object(setup_store, "apply_plan", return_value=[]):
        assert setup_store.main(["apply", plan_file]) == 0


def test_apply_still_needs_the_shopify_settings(plan_file):
    with patch.object(settings, "SHOPIFY_CLIENT_SECRET", ""):
        with pytest.raises(ValueError, match="SHOPIFY_CLIENT_SECRET"):
            setup_store.main(["apply", plan_file])


def test_apply_path_does_not_import_crewai():
    # executor -> tools.shopify_write -> tools.shopify_client: no agent framework.
    code = "import sys, setup_store, executor; sys.exit(1 if 'crewai' in sys.modules else 0)"
    assert subprocess.run([sys.executable, "-c", code]).returncode == 0


def test_apply_reports_a_broken_plan_file_without_a_traceback(tmp_path, caplog):
    path = tmp_path / "plan.json"
    path.write_text(json.dumps({"date": "2026-11-12"}), encoding="utf-8")
    with caplog.at_level(logging.ERROR):
        assert setup_store.main(["apply", str(path)]) == 1
    assert "is not valid" in caplog.text


# -- plan ---------------------------------------------------------------------

def test_plan_refuses_to_overwrite_todays_plan_before_calling_the_model(brief_file, caplog):
    today = date.today().isoformat()
    json_path = save_plan(_plan(day=today, description_html="<p>Edited by a person.</p>"))[0]

    with patch("crew.run_planning") as run, caplog.at_level(logging.ERROR):
        code = setup_store.main(["plan", "--brief", brief_file])

    assert code == 1
    run.assert_not_called()
    assert "Edited by a person." in open(json_path, encoding="utf-8").read()
    assert "--overwrite" in caplog.text


def test_plan_overwrite_flag_replaces_todays_plan(brief_file):
    today = date.today().isoformat()
    save_plan(_plan(day=today, description_html="<p>Edited by a person.</p>"))

    with patch("crew.run_planning", return_value=_plan(day=today)) as run:
        code = setup_store.main(["plan", "--brief", brief_file, "--overwrite"])

    assert code == 0
    assert run.call_args.kwargs["today"] == today
    assert "Edited by a person." not in open(plan_paths(today)[0], encoding="utf-8").read()


def test_invalid_brief_is_reported_without_a_traceback(tmp_path, caplog):
    path = tmp_path / "brief.yaml"
    path.write_text(
        "store_tag: demo-tag\ncollection_key: glow\ncollection_title: Glow\nproducts:\n"
        "  - {key: oil, title: Oil, one_liner: a, price: 20, vendor: V}\n"
        "  - {key: oil, title: Oil 2, one_liner: b, price: 21, vendor: V}\n",
        encoding="utf-8",
    )
    with patch("crew.run_planning") as run, caplog.at_level(logging.ERROR):
        code = setup_store.main(["plan", "--brief", str(path)])

    assert code == 1
    run.assert_not_called()
    assert "duplicate product keys: oil" in caplog.text


@pytest.mark.parametrize("error, expect_key_hint", [
    (RuntimeError("AuthenticationError: invalid x-api-key"), True),
    (RuntimeError("RateLimitError: 429 too many requests"), False),
    (RuntimeError("Crew returned no structured PlannerOutput"), False),
])
def test_api_key_hint_only_for_authentication_errors(brief_file, caplog, error, expect_key_hint):
    with patch("crew.run_planning", side_effect=error), caplog.at_level(logging.ERROR):
        code = setup_store.main(["plan", "--brief", brief_file])

    assert code == 1
    assert "Planning failed" in caplog.text
    assert ("ANTHROPIC_API_KEY" in caplog.text) is expect_key_hint


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


def test_apply_says_nothing_was_written_when_the_preflight_check_fails(plan_file, caplog):
    entries = [
        {"action": "product", "handle": "oil", "status": "skipped", "phase": "preflight", "detail": "not written"},
        {"action": "collection", "handle": "glow", "status": "error", "phase": "preflight", "detail": "already exists"},
    ]
    with patch("builtins.input", return_value="apply"), \
         patch.object(setup_store, "apply_plan", return_value=entries), caplog.at_level(logging.ERROR):
        assert setup_store.main(["apply", plan_file]) == 1
    assert "Nothing was written" in caplog.text
