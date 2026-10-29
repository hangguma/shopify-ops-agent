# Shopify Ops Agent

An ecommerce operations agent built on Claude Sonnet 5. It reads a Shopify
store's catalog and orders, then flags products at risk of selling out and
products that aren't moving. It's a multi-step tool-use agent, and it's
read-only for now: writes come later, behind a human approval step.

SFCC has no free setup path, so this runs on a free Shopify Partner
development store instead.

## Phases

- **Phase 1 (current):** read-only analyst agent. Pull inventory and orders,
  flag at-risk and underperforming products, write a markdown report
- **Phase 2:** executor agent (write actions) with an approval flow, audit log
  and Streamlit dashboard. Designed, not built
- **Phase 3 (stretch):** scheduled monitoring and alerts

## Setup

1. Sign up as a Shopify Partner, then in the
   [Dev Dashboard](https://dev.shopify.com/dashboard) → Stores →
   Create store (development store; enable generated test data so
   products/orders are pre-seeded)
2. Dev Dashboard → Create app → Start from Dev Dashboard → Versions tab →
   set access scopes `read_products`, `read_orders`, `read_inventory` →
   Release → Home → Install app on your dev store
3. App → Settings → copy the Client ID and Client secret. The app
   exchanges these for a short-lived access token at runtime; the current
   Shopify flow has no static admin token
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
enrich.py    fills variant IDs and quantities from store data (see Evals)
evals/       golden-scenario evals for the analyst
crew.py      assembles agents + tasks into a Crew
main.py      CLI entry point (replaced by app.py / Streamlit in Phase 2)
```

Phase 2 adds write tools (e.g. `update_price`) to `tools/` and an
`executor.py` to `agents/`. The existing read-only analysis logic is reused
as-is.

## Evals

The evals test what the agent decides, which the unit tests can't. Each scenario
in `evals/scenarios/` is a fixed store snapshot (products, variants, orders)
plus the variants the agent must flag and must not flag. The scenario also
says how the agent would get it wrong, for example reading an inventory of 0
as missing data.

The solver runs the real crew with only the Shopify API call patched, so any
failure comes from the agent itself. The scorer is plain code (no LLM
judge) and grades two things separately:

- **Judgment:** did it flag the right variants?
- **Fidelity:** are the variant IDs and quantities exactly what the store says?

```
python -m evals.run                     # 5 scenarios, 3 trials each
python -m evals.run --rescore outputs/evals/<file>.json   # re-grade, no LLM
```

It calls the real model, so it costs money and stays out of pytest and CI.

### What it found

Claude Sonnet 5, 5 scenarios × 3 trials:

| | Before | After |
|---|---|---|
| Judgment | 14/15 | 13/15 |
| Fidelity | 7/15 | 15/15 |

The agent usually picked the right products but wrote the variant ID as
`<UNKNOWN>` or as the variant name. The tool logs show it had the right data,
so it was mis-copying, not missing it. Scoring the two axes separately is what
made that visible; the first version of the scorer counted a wrong ID as a
missed product too.

The fix: the model now returns only its picks (product, variant name, reason),
and `enrich.py` looks up the ID and quantity in the store data. If a pick
doesn't match exactly one variant, it's kept with an empty ID and
`needs_review: true`, so a person sees it instead of a later step acting on a
guess. The judgment change (14 → 13) is within noise at this sample size.

### What it didn't catch

The first run against the live dev store sent all 17 flags to
`needs_review` and filled no IDs. The fixtures were cleaner than real product
data (names with `|` and parentheses, products with 4 or 5 variants), and the
orders tool only fetches line-item titles, so sales can only be matched at the
product level. Next: fetch the variant on each order line, and let code do
the numeric checks (stock thresholds) while the model handles priority and
context.

## Testing

- `pytest -v` runs the unit tests (mocked, no live store needed). See
  [docs/AUTOMATED_TESTS.md](docs/AUTOMATED_TESTS.md) for what they check.
- [docs/USER_TEST_CASES.md](docs/USER_TEST_CASES.md) is the manual UAT
  checklist for verifying the real Shopify + Claude API path end to end.
