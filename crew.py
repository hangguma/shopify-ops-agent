"""
Crew assembly - the core of the agent system.

Why separate this from main.py?
- crew.py is "the agent system itself"; main.py is "how to run it".
- Phase 2 (Streamlit UI with an approval step) imports build_crew as-is and
  inserts the human-approval gate between this report and a deterministic executor (executor.py, code, not an LLM crew).
"""

from crewai import Crew, Process

from agents.analyst import build_analyst
from agents.planner import build_planner
from config import settings
from enrich import enrich
from models.schemas import AnalysisReport, AnalystOutput, PlannerOutput, StoreBrief, StorePlan
from store_plan import build_plan
from tasks.tasks import build_analysis_task, build_planning_task
from tools.shopify_tool import fetch_products


def build_crew() -> Crew:
    """Assemble and return the Phase 1 (read-only) operations crew."""
    analyst = build_analyst()
    analysis_task = build_analysis_task(analyst)

    return Crew(
        agents=[analyst],
        tasks=[analysis_task],
        process=Process.sequential,
        verbose=True,
    )


def run_analysis(verbose: bool = True) -> AnalysisReport:
    """Run the crew, then attach code-sourced variant IDs and quantities."""
    crew = build_crew()
    if not verbose:
        crew.verbose = False
        for agent in crew.agents:
            agent.verbose = False

    result = crew.kickoff()
    output = getattr(result, "pydantic", None)
    if not isinstance(output, AnalystOutput):
        raise RuntimeError("Crew returned no structured AnalystOutput")
    return enrich(output, fetch_products(settings.PRODUCTS_LIMIT))


def run_planning(brief: StoreBrief, verbose: bool = True, today: str | None = None) -> StorePlan:
    """Run the planner (copy only, no tools), then merge its copy with brief facts.

    This never touches the store: the plan is applied separately by
    executor.py after a person approves it.
    """
    planner = build_planner(verbose=verbose)
    crew = Crew(
        agents=[planner],
        tasks=[build_planning_task(planner, brief)],
        process=Process.sequential,
        verbose=verbose,
    )
    result = crew.kickoff()
    output = getattr(result, "pydantic", None)
    if not isinstance(output, PlannerOutput):
        raise RuntimeError("Crew returned no structured PlannerOutput")
    return build_plan(brief, output, today=today)
