"""
Unit tests for the StorePlan contract (Phase 2A).

No API calls. They check the plan rejects bad data at the boundary and that
reference_problems() catches inconsistencies before the Executor runs.
"""

import pytest
from pydantic import ValidationError

from models.store_plan import MenuItemDraft, ProductDraft, StorePlan, slugify


def _product(**overrides) -> ProductDraft:
    fields = dict(handle="p", title="P", description_html="<p>x</p>", price="10")
    fields.update(overrides)
    return ProductDraft(**fields)


def test_handles_are_normalised():
    p = _product(handle="Hydration 10-Pack!", collection_handles=["Sheet Masks"])
    assert p.handle == "hydration-10-pack"
    assert p.collection_handles == ["sheet-masks"]


def test_empty_handle_rejected():
    with pytest.raises(ValueError):
        slugify("  !!  ")


def test_price_normalised_to_two_decimals():
    assert _product(price="24").price == "24.00"
    assert _product(price="$7.5").price == "7.50"


@pytest.mark.parametrize("bad", ["free", "0", "-3"])
def test_invalid_price_rejected(bad):
    with pytest.raises(ValidationError):
        _product(price=bad)


def test_items_default_to_unapproved():
    assert _product().approved is False


def test_consistent_plan_has_no_problems(plan):
    assert plan.reference_problems() == []


def test_unknown_collection_reference_reported(plan):
    plan.products[0].collection_handles.append("does-not-exist")
    problems = plan.reference_problems()
    assert any("unknown collection 'does-not-exist'" in p for p in problems)


def test_duplicate_handles_reported(plan):
    plan.pages.append(plan.pages[0].model_copy())
    assert any("duplicate page handle 'about'" in p for p in plan.reference_problems())


def test_menu_item_without_target_reported(plan):
    plan.menu.items.append(MenuItemDraft(title="Broken", type="PAGE"))
    assert any("has no target_handle" in p for p in plan.reference_problems())


def test_menu_link_to_unknown_page_reported(plan):
    plan.menu.items.append(MenuItemDraft(title="Ghost", type="PAGE", target_handle="ghost"))
    assert any("unknown page 'ghost'" in p for p in plan.reference_problems())


def test_plan_round_trips_through_json(plan):
    again = StorePlan.model_validate_json(plan.model_dump_json())
    assert again == plan


def test_approval_counts(plan, unapproved_plan):
    assert plan.approval_counts()["products"] == (2, 2)
    assert unapproved_plan.approval_counts()["menu"] == (0, 1)
