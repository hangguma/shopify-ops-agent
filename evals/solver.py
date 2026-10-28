"""
Solver - runs the real Phase 1 crew against a scenario's fixed store snapshot.

Why this design?
- Only the network layer (_graphql_request) is replaced. Agent, task, prompt,
  tools, output schema and enrichment are the production ones (run_analysis),
  so the eval measures what actually ships.
- One trial = one full crew run. The runner repeats trials because the same
  input can produce different reports.
"""

from unittest.mock import patch

from crew import run_analysis
from evals.dataset import Scenario
from models.schemas import AnalysisReport


def _fake_graphql(scenario: Scenario):
    def fake(query: str, variables: dict | None = None) -> dict:
        first = (variables or {}).get("first", 20)
        if "products(" in query:
            return scenario.products_response(first)
        if "orders(" in query:
            return scenario.orders_response(first)
        raise ValueError(f"Eval fixture has no response for query: {query[:80]}")
    return fake


def solve(scenario: Scenario) -> AnalysisReport:
    """Run the analyst crew once on the scenario and return its report."""
    with patch("tools.shopify_tool._graphql_request", side_effect=_fake_graphql(scenario)):
        return run_analysis(verbose=False)
