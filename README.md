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
- **Store setup (new, not yet run against a live store):** turn a short
  product brief into draft products and a collection, behind a typed
  approval. See [Store setup](#store-setup)

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
tools/       Shopify Admin API: client, read tools for agents, store-setup writes
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

## Store setup

Sets up a store from a few lines per product. Two commands, with a person in
between:

```
python setup_store.py plan                                  # calls the model, writes nothing
python setup_store.py apply outputs/store_plan_<date>.json  # no model, writes to the store
```

1. **Brief** (`store_brief.yaml`): product titles, one-line descriptions,
   prices (at most two decimals) and vendors. This file is the source of
   every fact.
2. **Plan**: the planner agent writes only the copy (description, product
   type, tags). `store_plan.py` merges it with the brief's facts, reduces the
   HTML to a small allowlist, and marks a product or the collection
   `needs_review` if copy is missing, was cut off, or uses a medical-sounding
   word (cure, heal, treat, prevent, acne, clinically proven, ...) in any
   model-written field. The result is saved as
   `outputs/store_plan_<date>.json` and `.md`. `plan` will not replace an
   existing plan file for the same day unless you pass `--overwrite`.
3. **Approval**: read the `.md`, edit the `.json` if you want changes. The
   edited file is checked again with the same rules: HTML outside the
   allowlist, a claim word, a bad handle, a bad price, or a product that has
   lost the store tag all stop `apply` before it asks you anything. A claim
   word is cleared by rewording the copy, not by setting `needs_review` to
   false. When the plan is clean, `apply` prints it and writes only after you
   type `apply`. There is no flag to skip this.
4. **Apply** (`executor.py`, code, not an agent): each product is looked up
   by its handle, then created as a **draft** or updated. Then a collection
   is created that includes everything tagged with the brief's `store_tag`.
   The collection is unpublished. Every step is appended to
   `outputs/setup_audit_<date>.jsonl`. `apply` does not call the model and
   does not need `ANTHROPIC_API_KEY`.

What running `apply` again does:

- A product this setup created (it carries `store_tag`) is updated. Its
  **status is left alone**, so a product you have published stays published.
  Its title, description, vendor, type, tags, options, variants and price are
  set back to the plan, so changes made to those in the Shopify admin are
  lost.
- A product with the same handle that does **not** carry `store_tag` was not
  made by this setup. It is never written; the run stops with an error.
- An existing collection is accepted, and left as it is, only if its single
  rule is "tag equals `store_tag`". Any other collection with that handle
  stops the run, because the new products would not be in it.
- The run stops at the first error. Steps already marked `ok` in the audit
  log were written.

Before the first `apply`, add the `write_products` scope to the app (Dev
Dashboard → Versions → Release) and approve the update on the store.

Limits for now: one variant per product, no images, no inventory quantities,
and a set is a plain product (not a Shopify bundle). The collection mutation
uses the `ruleSet` form, which API version 2026-07 deprecates in favour of
`sources`; migrate when `SHOPIFY_API_VERSION` is raised.

New files: `store_brief.yaml`, `store_plan.py`, `executor.py`,
`setup_store.py`, `agents/planner.py`, `tools/shopify_client.py` (auth and
request helper, moved out of `shopify_tool.py`), `tools/shopify_write.py`.

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
