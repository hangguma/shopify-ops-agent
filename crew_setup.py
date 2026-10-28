"""
Store setup crew assembly (Phase 2A).

Separate from crew.py on purpose: crew.py is the Phase 1 read-only analysis
crew and stays unchanged. This crew only PROPOSES a StorePlan; writes happen
later in executor/store_executor.py, after human review.
"""

from crewai import Crew, Process

from agents.builder import build_builder
from tasks.store_setup import build_store_plan_task


def build_setup_crew(brief: str) -> Crew:
    """Assemble the crew that turns a brand brief into a StorePlan."""
    builder = build_builder()
    plan_task = build_store_plan_task(builder, brief)

    return Crew(
        agents=[builder],
        tasks=[plan_task],
        process=Process.sequential,
        verbose=True,
    )
