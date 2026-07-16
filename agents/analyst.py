"""
Analyst Agent - reads Shopify catalog + order data and flags issues.

Single responsibility: read-only analysis, no writes.
Input: (nothing - pulls live from Shopify)  ->  Output: AnalysisReport

Phase 2 will add an Executor agent (separate file) that takes this agent's
flagged_products as input and applies approved changes via write tools.
"""

from crewai import Agent, LLM

from config import settings
from tools.shopify_tool import ShopifyOrdersTool, ShopifyProductsTool


def build_analyst() -> Agent:
    """Create and return the Analyst agent."""
    return Agent(
        role="Shopify 운영 분석가",
        goal=(
            "get_products와 get_orders 도구를 사용해 재고가 부족하거나 "
            "매출 기여가 낮은 상품을 찾아내고, 운영팀이 검토할 수 있는 "
            "보고서를 작성한다."
        ),
        backstory=(
            "당신은 이커머스 운영 데이터를 분석하는 전문가입니다. 추측하지 않고 "
            "오직 도구가 반환한 실제 데이터만 근거로 판단합니다. 재고 수량, "
            "최근 판매량을 교차 비교해 '재고는 있는데 안 팔리는 상품'과 "
            "'잘 팔리는데 재고가 부족한 상품'을 구분해서 짚어줍니다."
        ),
        tools=[ShopifyProductsTool(), ShopifyOrdersTool()],
        # max_tokens must cover thinking + the full report: the provider
        # default (4096) intermittently truncates inside the thinking block,
        # yielding an empty text response that CrewAI rejects.
        llm=LLM(model=settings.LLM_MODEL, max_tokens=16384),
        verbose=True,
        max_iter=4,
        allow_delegation=False,
    )
