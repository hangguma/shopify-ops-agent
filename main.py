"""
Phase 1 entry point - runs the Shopify ops analysis from the command line.

Usage:
    python main.py

In Phase 2, app.py (Streamlit) calls build_crew instead of this file.
The agent logic (crew.py) is reused as-is.
"""

import os
from datetime import date

from config import settings
from config.logging_setup import setup_logging
from crew import run_analysis


def save_report(markdown: str) -> str:
    """Save the Markdown report to the outputs/ folder."""
    os.makedirs(settings.OUTPUT_DIR, exist_ok=True)
    today = date.today().isoformat()
    path = os.path.join(settings.OUTPUT_DIR, f"ops_report_{today}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(markdown)
    return path


def main() -> None:
    log = setup_logging(to_file=True)

    # Fail early if keys/config are missing
    settings.validate()

    log.info("Starting Shopify ops analysis - store: %s", settings.SHOPIFY_STORE_DOMAIN)

    try:
        report = run_analysis()
    except Exception as e:  # noqa: BLE001
        log.error("Analysis failed: %s", e)
        log.error("  - Check ANTHROPIC_API_KEY validity.")
        log.error("  - Check SHOPIFY_STORE_DOMAIN / SHOPIFY_CLIENT_ID / SHOPIFY_CLIENT_SECRET and API scopes.")
        return

    for f in report.flagged_products:
        if f.needs_review:
            log.warning("Needs review (no variant ID resolved): %s", f.title)

    markdown = report.markdown
    path = save_report(markdown)
    print("\n" + "=" * 50)
    print(markdown)
    print("=" * 50)
    log.info("Report complete -> %s", path)


if __name__ == "__main__":
    main()
