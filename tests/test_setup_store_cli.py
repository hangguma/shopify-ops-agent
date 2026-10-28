"""
Unit tests for the setup_store.py CLI (Phase 2A).

Covers the offline parts: plan file round trip, the review step, and that
`apply` defaults to a dry run. The Builder (LLM) step is not unit tested;
it is covered by the manual checklist in docs/USER_TEST_CASES.md.
"""

from unittest.mock import patch

import setup_store
from models.store_plan import StorePlan


def test_save_and_load_plan_round_trip(plan, tmp_path):
    path = setup_store.save_plan(plan, str(tmp_path / "plan.json"))
    assert setup_store.load_plan(path) == plan


def test_review_approve_all(unapproved_plan):
    reviewed = setup_store.review_plan(unapproved_plan, approve_all=True)
    counts = reviewed.approval_counts()
    assert all(approved == total for approved, total in counts.values())


def test_review_interactive_only_yes_approves(unapproved_plan):
    answers = iter(["y", "n", "yes", "", "Y", "no", "n"])  # 2 collections, 2 products, 2 pages, menu
    reviewed = setup_store.review_plan(unapproved_plan, ask=lambda _: next(answers))

    assert [c.approved for c in reviewed.collections] == [True, False]
    assert [p.approved for p in reviewed.products] == [True, False]
    assert [p.approved for p in reviewed.pages] == [True, False]
    assert reviewed.menu.approved is False


def test_review_command_runs_offline_without_keys(unapproved_plan, tmp_path, monkeypatch):
    for key in ("ANTHROPIC_API_KEY", "SHOPIFY_STORE_DOMAIN", "SHOPIFY_CLIENT_ID", "SHOPIFY_CLIENT_SECRET"):
        monkeypatch.setattr(setup_store.settings, key, "")
    path = setup_store.save_plan(unapproved_plan, str(tmp_path / "plan.json"))

    assert setup_store.main(["review", path, "--approve-all"]) == 0
    assert setup_store.load_plan(path).menu.approved is True


def test_apply_defaults_to_dry_run(plan, tmp_path):
    path = setup_store.save_plan(plan, str(tmp_path / "plan.json"))
    args = setup_store.build_parser().parse_args(["apply", path])

    with patch("setup_store.StoreExecutor") as executor_cls:
        executor_cls.return_value.run.return_value = []
        executor_cls.return_value.summary.return_value = {}
        setup_store.cmd_apply(args)

    assert executor_cls.call_args.kwargs["dry_run"] is True
    assert isinstance(executor_cls.call_args.args[0], StorePlan)


def test_describe_plan_lists_problems(plan):
    plan.products[0].collection_handles.append("ghost")
    text = setup_store.describe_plan(plan)
    assert "Problems (fix before apply)" in text
    assert "ghost" in text
