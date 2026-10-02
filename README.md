# Shopify Ops Agent

An ecommerce operations agent built on Claude Sonnet 5. SFCC has no free
setup path, so this uses a free Shopify Partner development store instead.
Goes beyond a simple RAG search bot — a multi-step, tool-use agent that
analyzes (and eventually acts on) real store operations data.

## Design docs

- [docs/REQUIREMENTS.md](docs/REQUIREMENTS.md) — goals, scope by phase, functional/non-functional requirements, risks, traceability to tests
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — layers, flows, design decisions (ADRs), target structure for 2B/2C/3
- [docs/TECH_SPEC.md](docs/TECH_SPEC.md) — modules, config and scopes, GraphQL operations, data contracts, executor algorithm, CLI, extension guide

## Phases

- **Phase 1 (done)** — Read-only analyst agent: pull inventory/orders →
  flag at-risk and underperforming products → markdown report
- **Phase 2A (current)** — Storefront setup: a Builder agent turns a brand
  brief into a `StorePlan` (collections, products, pages, main menu) → a human
  approves item by item → a deterministic Executor applies only approved items
  to the Online Store, with dry run by default and a JSONL audit log
- **Phase 2B** — Headless storefront (Hydrogen / Next.js on the Storefront API)
- **Phase 2C** — Inventory approval gate (design confirmed 2026-07-29):
  the analyst proposes `ProposedAction`s (current → proposed inventory, with
  reason and evidence), `current_value` is looked up by code, not the LLM; a
  local Streamlit queue approves/rejects each one; a deterministic executor
  calls a single write tool, `update_inventory`; every proposal and decision
  goes to a SQLite audit log with a log view. Price and discount writes are
  out of scope on purpose.
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

## Store setup (Phase 2A)

The LLM proposes; a human approves; plain code writes.

```
brand brief ──► Builder agent ──► StorePlan JSON ──► review (human) ──► Executor ──► Shopify
               (CrewAI, read-only)  outputs/plans/    approve per item   dry run first   + audit log
```

1. Add write scopes to the Dev Dashboard app (Versions → new version →
   Release → reinstall on the dev store):
   `write_products`, `write_content`, `read_online_store_navigation`,
   `write_online_store_navigation`, `read_publications`, `write_publications`
2. Write a brand brief (start from `briefs/example_brief.md`)
3. Run the three steps:
   ```
   python setup_store.py plan   --brief briefs/example_brief.md
   python setup_store.py review outputs/plans/store_plan_<date>.json      # y/N per item, or --approve-all
   python setup_store.py apply  outputs/plans/store_plan_<date>.json      # dry run: reads only
   python setup_store.py apply  outputs/plans/store_plan_<date>.json --execute
   ```

Design choices:
- **Builder has no write tools.** It can read the catalog to avoid
  duplicates, and its output is a `StorePlan` (Pydantic) with every
  `approved` flag forced to `false`.
- **The plan is a file.** You can read, diff and hand-edit it before review.
  `reference_problems()` refuses plans with dangling references (a product
  pointing at a collection that is not in the plan, a menu link to a missing
  page).
- **Executor is deterministic code, not an agent.** Order: collections →
  products → pages → menu. Collections and pages are looked up by handle
  before creating; products use `productSet`'s handle upsert. Re-running a
  plan creates no duplicates.
- **Dry run by default.** `apply` without `--execute` still reads the store,
  so it reports which handles already exist, but never writes.
- **Every action is logged** to `outputs/audit_log.jsonl` (created / exists /
  upserted / published / skipped / error). One failed item does not abort the
  run.

Scope of 2A: single-variant products, manual collections, Online Store pages,
replacing the items of the theme's `main-menu`. No images, no theme edits.

## Structure

```
config/      settings + logging (settings.py, logging_setup.py)
models/      I/O contracts between agents (Pydantic): schemas.py (Phase 1),
             store_plan.py (Phase 2A)
tools/       shopify_tool.py: read tools for agents (CrewAI)
             shopify_write_tool.py: write functions, called only by the Executor
agents/      analyst.py (Phase 1), builder.py (Phase 2A)
tasks/       tasks.py (Phase 1), store_setup.py (Phase 2A)
executor/    store_executor.py: applies approved StorePlan items
briefs/      brand briefs for the Builder
crew.py      Phase 1 analysis crew
crew_setup.py  Phase 2A store setup crew
main.py      Phase 1 CLI
setup_store.py Phase 2A CLI (plan / review / apply)
```

The read-only analysis path (`main.py`, `crew.py`, `agents/analyst.py`) is
unchanged by Phase 2A.

## Testing

- `pytest -v` runs 61 unit tests (mocked, no live store needed) — see
  [docs/AUTOMATED_TESTS.md](docs/AUTOMATED_TESTS.md) for what each one checks.
- [docs/USER_TEST_CASES.md](docs/USER_TEST_CASES.md) is the manual UAT
  checklist for verifying the real Shopify + Claude API path end to end.
