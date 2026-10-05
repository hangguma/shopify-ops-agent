"""
Tests for enrich.py - code, not the LLM, supplies variant IDs and quantities.
"""

from enrich import enrich, resolve
from models.schemas import AnalystOutput, AnalystPick


def product(title, *variants):
    return {"node": {"title": title, "variants": {"edges": [
        {"node": {"id": vid, "title": vt, "inventoryQuantity": qty}} for vid, vt, qty in variants
    ]}}}


CATALOG = [
    product("Everyday Tee", ("gid://v/1", "M / White", 30)),
    product("Linen Shirt", ("gid://v/2", "S / Natural", 0), ("gid://v/3", "M / Natural", 4)),
    product("Tote", ("gid://v/4", "Default Title", -2)),
    product("Tote", ("gid://v/5", "Default Title", 7)),
]


def pick(title, variant_title="", reason="r"):
    return AnalystPick(title=title, variant_title=variant_title, reason=reason)


def test_single_variant_product_resolves_by_title():
    assert resolve(pick("Everyday Tee"), CATALOG)["id"] == "gid://v/1"


def test_multi_variant_product_resolves_by_variant_title():
    assert resolve(pick("Linen Shirt", "M / Natural"), CATALOG)["id"] == "gid://v/3"


def test_variant_appended_to_title_still_resolves():
    assert resolve(pick("Linen Shirt (S / Natural)"), CATALOG)["id"] == "gid://v/2"


def test_ambiguous_or_unknown_picks_do_not_resolve():
    assert resolve(pick("Linen Shirt", "L / Natural"), CATALOG) is None   # no such variant
    assert resolve(pick("Tote"), CATALOG) is None                         # two products share the title
    assert resolve(pick("Ghost"), CATALOG) is None


def test_enrich_fills_facts_from_store_and_keeps_unresolved_for_review():
    out = AnalystOutput(date="2026-10-05", markdown="# r", picks=[
        pick("Linen Shirt", "M / Natural", reason="low stock"),
        pick("Tote", reason="oversold"),
    ])
    report = enrich(out, CATALOG)

    ok, unresolved = report.flagged_products
    assert (ok.variant_id, ok.inventory_quantity, ok.needs_review) == ("gid://v/3", 4, False)
    assert ok.reason == "low stock"
    assert (unresolved.variant_id, unresolved.inventory_quantity, unresolved.needs_review) == ("", None, True)
    assert report.date == "2026-10-05" and report.markdown == "# r"
