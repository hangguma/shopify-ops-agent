"""
Solver - runs the real Phase 1 crew against a scenario's fixed store snapshot.

Why this design?
- Only the network layer (_graphql_request) is replaced. Agent, task, prompt,
  tools and output schema are the production ones, so the eval measures what
  actually ships.
- One trial = one full crew run. The runner repeats trials because the same
  input can produce different reports.
"""

from unittest.mock import patch

from crew import build_crew
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
    crew = build_crew()
    crew.verbose = False
    for agent in crew.agents:
        agent.verbose = False

    with patch("tools.shopify_tool._graphql_request", side_effect=_fake_graphql(scenario)):
        result = crew.kickoff()

    report = getattr(result, "pydantic", None)
    if not isinstance(report, AnalysisReport):
        raise RuntimeError("Crew returned no structured AnalysisReport")
    return report
