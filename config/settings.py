"""
Global project settings.

Why this design?
- Hardcoding API keys leaks them when pushed to git. Keys are kept in a .env
  file and read via os.getenv.
- Mirrors daily-briefing-agent/config/settings.py for consistency across
  projects (same load pattern, same validate() contract).
"""

import os

from dotenv import load_dotenv

load_dotenv()

# -- API keys -------------------------------------------------
ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")

# -- Shopify Admin API ------------------------------------------
# Dev Dashboard apps don't expose a static admin token (the old shpat_
# flow was retired). We hold client credentials and exchange them for a
# short-lived access token at runtime (see tools/shopify_tool.py).
SHOPIFY_STORE_DOMAIN: str = os.getenv("SHOPIFY_STORE_DOMAIN", "")
SHOPIFY_CLIENT_ID: str = os.getenv("SHOPIFY_CLIENT_ID", "")
SHOPIFY_CLIENT_SECRET: str = os.getenv("SHOPIFY_CLIENT_SECRET", "")
SHOPIFY_API_VERSION: str = os.getenv("SHOPIFY_API_VERSION", "2026-04")

# -- LLM config -----------------------------------------------
# CrewAI uses LiteLLM under the hood, hence the "anthropic/<model>" format.
LLM_MODEL: str = os.getenv("LLM_MODEL", "anthropic/claude-sonnet-5")

# -- Pipeline parameters --------------------------------------
PRODUCTS_LIMIT: int = 50   # products fetched per analysis run (store has ~47)
ORDERS_LIMIT: int = 20     # recent orders fetched per analysis run
LOW_STOCK_THRESHOLD: int = 5  # inventory_quantity at/below this = flagged

# -- Output config --------------------------------------------
OUTPUT_DIR: str = "outputs"


def validate() -> None:
    """Check required API keys/config are set before running."""
    missing = []
    if not ANTHROPIC_API_KEY:
        missing.append("ANTHROPIC_API_KEY")
    if not SHOPIFY_STORE_DOMAIN:
        missing.append("SHOPIFY_STORE_DOMAIN")
    if not SHOPIFY_CLIENT_ID:
        missing.append("SHOPIFY_CLIENT_ID")
    if not SHOPIFY_CLIENT_SECRET:
        missing.append("SHOPIFY_CLIENT_SECRET")
    if missing:
        raise ValueError(
            f"Missing environment variables: {', '.join(missing)}\n"
            f"Copy .env.example to .env and fill in your keys."
        )
