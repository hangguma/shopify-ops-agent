"""
Planner Agent - writes the copy for a store setup plan.

Single responsibility: turn the seller's brief into product-page copy.
Input: StoreBrief  ->  Output: PlannerOutput (copy only)

It has no tools on purpose. It cannot read or write the store; store_plan.py
merges its copy with the brief's facts, and executor.py (code, not an LLM
agent) applies the plan after a person approves it.
"""

from crewai import Agent, LLM

from config import settings


def build_planner(verbose: bool = True) -> Agent:
    """Create and return the Planner agent."""
    return Agent(
        role="이커머스 상품 페이지 카피라이터",
        goal=(
            "판매자가 준 상품 설명만을 근거로, 미국 소비자가 읽을 Shopify 상품 "
            "페이지 문구(설명, 카테고리, 태그)를 영어로 작성한다."
        ),
        backstory=(
            "당신은 K-뷰티 상품 페이지를 써 온 카피라이터입니다. 주어지지 않은 "
            "사실은 쓰지 않고, 화장품 문구에 의학적 효능을 주장하지 않습니다. "
            "짧고 구체적인 문장을 선호합니다."
        ),
        tools=[],
        # Same reason as the analyst: the provider default (4096) can truncate
        # inside the thinking block and return an empty response.
        llm=LLM(model=settings.LLM_MODEL, max_tokens=16384),
        verbose=verbose,
        max_iter=2,
        allow_delegation=False,
    )
