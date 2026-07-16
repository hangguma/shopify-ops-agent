"""
Tests for settings validation and main.py helper functions.
"""

from unittest.mock import patch

import pytest

from config import settings


def test_validate_errors_when_no_keys():
    with patch.object(settings, "ANTHROPIC_API_KEY", ""), \
         patch.object(settings, "SHOPIFY_STORE_DOMAIN", ""), \
         patch.object(settings, "SHOPIFY_CLIENT_ID", ""), \
         patch.object(settings, "SHOPIFY_CLIENT_SECRET", ""):
        with pytest.raises(ValueError) as exc:
            settings.validate()
        assert "ANTHROPIC_API_KEY" in str(exc.value)
        assert "SHOPIFY_STORE_DOMAIN" in str(exc.value)
        assert "SHOPIFY_CLIENT_ID" in str(exc.value)
        assert "SHOPIFY_CLIENT_SECRET" in str(exc.value)


def test_validate_passes_with_keys():
    with patch.object(settings, "ANTHROPIC_API_KEY", "sk-ant-xxx"), \
         patch.object(settings, "SHOPIFY_STORE_DOMAIN", "test-store.myshopify.com"), \
         patch.object(settings, "SHOPIFY_CLIENT_ID", "client-id-xxx"), \
         patch.object(settings, "SHOPIFY_CLIENT_SECRET", "client-secret-xxx"):
        # Must not raise
        settings.validate()


def test_validate_errors_when_partial_keys():
    # Only the Shopify client secret is missing -> only that key in the message
    with patch.object(settings, "ANTHROPIC_API_KEY", "sk-ant-xxx"), \
         patch.object(settings, "SHOPIFY_STORE_DOMAIN", "test-store.myshopify.com"), \
         patch.object(settings, "SHOPIFY_CLIENT_ID", "client-id-xxx"), \
         patch.object(settings, "SHOPIFY_CLIENT_SECRET", ""):
        with pytest.raises(ValueError) as exc:
            settings.validate()
        assert "SHOPIFY_CLIENT_SECRET" in str(exc.value)
        assert "ANTHROPIC_API_KEY" not in str(exc.value)


def test_save_report_creates_file(tmp_path):
    # main.save_report should create a .md file and write its content
    import main

    with patch.object(settings, "OUTPUT_DIR", str(tmp_path)):
        path = main.save_report("# Test ops report")

    assert path.endswith(".md")
    with open(path, encoding="utf-8") as f:
        assert "Test ops report" in f.read()
