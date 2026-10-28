"""
Shared fixtures for Phase 2A store setup tests.

A small but complete StorePlan, built in code so tests never depend on an
LLM run or a file on disk.
"""

import pytest

from models.store_plan import (
    CollectionDraft,
    MenuDraft,
    MenuItemDraft,
    PageDraft,
    ProductDraft,
    StorePlan,
)


def make_plan(approved: bool = True) -> StorePlan:
    return StorePlan(
        brand_name="Haneul Mask Co.",
        brief_summary="Curated Korean sheet masks for a weekly routine.",
        collections=[
            CollectionDraft(handle="sheet-masks", title="Sheet Masks", approved=approved),
            CollectionDraft(handle="overnight", title="Overnight", approved=approved),
        ],
        products=[
            ProductDraft(
                handle="hydration-10-pack", title="Hydration 10-pack",
                description_html="<p>Ten hydrating sheet masks.</p>", price="24",
                collection_handles=["sheet-masks"], approved=approved,
            ),
            ProductDraft(
                handle="sleeping-mask", title="Overnight Sleeping Mask",
                description_html="<p>75 ml overnight mask.</p>", price="28.00",
                collection_handles=["overnight"], approved=approved,
            ),
        ],
        pages=[
            PageDraft(handle="about", title="About", body_html="<p>About us.</p>", approved=approved),
            PageDraft(handle="faq", title="FAQ", body_html="<p>Questions.</p>", approved=approved),
        ],
        menu=MenuDraft(
            approved=approved,
            items=[
                MenuItemDraft(title="Home", type="FRONTPAGE"),
                MenuItemDraft(title="Sheet Masks", type="COLLECTION", target_handle="sheet-masks"),
                MenuItemDraft(title="Shop all", type="CATALOG"),
                MenuItemDraft(title="About", type="PAGE", target_handle="about"),
            ],
        ),
    )


@pytest.fixture
def plan() -> StorePlan:
    return make_plan(approved=True)


@pytest.fixture
def unapproved_plan() -> StorePlan:
    return make_plan(approved=False)
