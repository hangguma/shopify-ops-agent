"""
Builder Agent - turns a brand brief into a StorePlan proposal.

Single responsibility: propose. It has read access to the existing catalog
(so it does not re-plan products that already exist) and NO write tools.
Everything it produces waits for human approval, then goes through the
deterministic Executor.
"""

from crewai import Agent, LLM

from config import settings
from tools.shopify_tool import ShopifyProductsTool


def build_builder() -> Agent:
    """Create and return the Builder agent."""
    return Agent(
        role="Shopify 스토어 설계자",
        goal=(
            "브랜드 브리프를 읽고, Shopify Online Store를 바로 열 수 있도록 "
            "컬렉션·상품·페이지·메인 메뉴로 구성된 StorePlan을 제안한다."
        ),
        backstory=(
            "당신은 D2C 브랜드 스토어를 여러 번 런칭해 본 이커머스 머천다이저입니다. "
            "브리프에 없는 사실(성분 효능, 인증, 수상 이력, 후기)은 지어내지 않고, "
            "모르는 정보는 담백한 일반 설명으로 처리합니다. 고객이 3클릭 안에 "
            "원하는 상품에 도달하도록 컬렉션과 메뉴를 단순하게 설계합니다."
        ),
        tools=[ShopifyProductsTool()],
        llm=LLM(model=settings.LLM_MODEL, max_tokens=16384),
        verbose=True,
        max_iter=4,
        allow_delegation=False,
    )
