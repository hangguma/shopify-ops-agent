"""
Crew assembly - the core of the agent system.

Why separate this from main.py?
- crew.py is "the agent system itself"; main.py is "how to run it".
- Phase 2 (Streamlit UI with an approval step) imports build_crew as-is and
  inserts the human-approval gate between this report and a deterministic executor (executor.py, code, not an LLM crew).
"""

from crewai import Crew, Process

from agents.analyst import build_analyst
from tasks.tasks import build_analysis_task


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
