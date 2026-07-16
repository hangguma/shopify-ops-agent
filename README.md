# Shopify Ops Agent

An ecommerce operations agent built on Claude Sonnet 5. SFCC has no free
setup path, so this uses a free Shopify Partner development store instead.
Goes beyond a simple RAG search bot — a multi-step, tool-use agent that
analyzes (and eventually acts on) real store operations data.

Background and portfolio strategy live in life-os:
[`raw/venture/shopify-ops-agent.md`](../../life-os/raw/venture/shopify-ops-agent.md).

## Phases

- **Phase 1 (current)** — Read-only analyst agent: pull inventory/orders →
  flag at-risk and underperforming products → markdown report
- **Phase 2** — Executor agent (write actions) + approval flow + audit log +
  Streamlit dashboard
- **Phase 3 (stretch)** — Scheduled monitoring + alerts

## Setup

1. Sign up as a Shopify Partner, then in the
   [Dev Dashboard](https://dev.shopify.com/dashboard) → Stores →
   Create store (development store; enable generated test data so
   products/orders are pre-seeded)
2. Dev Dashboard → Create app → Start from Dev Dashboard → Versions tab →
   set access scopes `read_products`, `read_orders`, `read_inventory` →
   Release → Home → Install app on your dev store
3. App → Settings → copy the Client ID and Client secret (the app
   exchanges these for a short-lived access token at runtime — there is
   no static admin token in the current Shopify flow)
4. Install dependencies:
   ```
   python -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt
   ```
5. Configure environment variables:
   ```
   cp .env.example .env
   # fill in ANTHROPIC_API_KEY, SHOPIFY_STORE_DOMAIN,
   # SHOPIFY_CLIENT_ID, SHOPIFY_CLIENT_SECRET
   ```
6. Run:
   ```
   python main.py
   ```
   Output prints to console and saves to `outputs/ops_report_<date>.md`.

## Structure

```
config/      settings + logging (settings.py, logging_setup.py)
models/      I/O contracts between agents (Pydantic schemas)
tools/       Shopify Admin API wrapped as CrewAI tools (currently read-only)
agents/      agent definitions (currently analyst only)
tasks/       task definitions assigned to agents
crew.py      assembles agents + tasks into a Crew
main.py      CLI entry point (replaced by app.py / Streamlit in Phase 2)
```

Phase 2 adds write tools (e.g. `update_price`) to `tools/` and an
`executor.py` to `agents/`. The existing read-only analysis logic is reused
as-is.

## Testing

- `pytest -v` runs 16 unit tests (mocked, no live store needed) — see
  [docs/AUTOMATED_TESTS.md](docs/AUTOMATED_TESTS.md) for what each one checks.
- [docs/USER_TEST_CASES.md](docs/USER_TEST_CASES.md) is the manual UAT
  checklist for verifying the real Shopify + Claude API path end to end.
