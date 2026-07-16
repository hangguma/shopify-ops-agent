"""
Unit tests for the data models (I/O contracts).

No API calls, so these run in any environment.
They verify the contracts actually REJECT invalid data.
"""

import pytest
from pydantic import ValidationError

from models.schemas import AnalysisReport, FlaggedProduct


def test_flagged_product_creates_ok():
    p = FlaggedProduct(
        title="Widget", variant_id="gid://shopify/ProductVariant/1",
        reason="low stock", inventory_quantity=2,
    )
    assert p.title == "Widget"
    assert p.inventory_quantity == 2


def test_flagged_product_missing_required_field_errors():
    with pytest.raises(ValidationError):
        FlaggedProduct(title="Widget", reason="low stock", inventory_quantity=2)


def test_flagged_product_inventory_quantity_must_be_int():
    with pytest.raises(ValidationError):
        FlaggedProduct(
            title="Widget", variant_id="gid://shopify/ProductVariant/1",
            reason="low stock", inventory_quantity="not-a-number",
        )


def test_analysis_report_creates_ok():
    report = AnalysisReport(
        date="2026-06-30",
        flagged_products=[
            FlaggedProduct(
                title="Widget", variant_id="gid://shopify/ProductVariant/1",
                reason="low stock", inventory_quantity=2,
            )
        ],
        markdown="# Report\nbody",
    )
    assert report.date == "2026-06-30"
    assert len(report.flagged_products) == 1
    assert "Report" in report.markdown


def test_analysis_report_allows_empty_flagged_products():
    # Nothing wrong with the store -> empty list, not an error
    report = AnalysisReport(date="2026-06-30", flagged_products=[], markdown="# Report\nAll good.")
    assert report.flagged_products == []
