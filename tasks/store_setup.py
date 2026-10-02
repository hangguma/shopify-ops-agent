"""
Store setup task - Builder turns a brand brief into a StorePlan.

Kept separate from tasks/tasks.py so the Phase 1 analysis task is untouched.
The output contract (StorePlan) is what the review CLI and the Executor read.
"""

from crewai import Agent, Task

from models.store_plan import StorePlan


def build_store_plan_task(agent: Agent, brief: str) -> Task:
    return Task(
        description=(
            "아래 브랜드 브리프를 바탕으로 Shopify Online Store 셋업 계획(StorePlan)을 작성하라.\n\n"
            "=== 브랜드 브리프 ===\n"
            f"{brief.strip()}\n"
            "=== 브리프 끝 ===\n\n"
            "작성 규칙:\n"
            "1) get_products로 현재 카탈로그를 한 번 확인하고, 이미 있는 상품과 같은 상품은 계획하지 않는다.\n"
            "2) collections: 2~4개. 고객이 고르는 기준(용도, 고민, 라인)으로 나눈다.\n"
            "3) products: 브리프에 나온 상품을 우선하고, 브리프가 상품을 정하지 않았으면 4~8개를 제안한다. "
            "모든 상품은 단일 옵션이며, price는 '24.00' 같은 숫자 문자열, "
            "collection_handles는 이 계획의 컬렉션 handle만 쓴다.\n"
            "4) description_html은 <p>와 <ul><li>만 쓰는 짧은 HTML. 브리프에 없는 효능·인증·수치는 쓰지 않는다.\n"
            "5) pages: about, faq, shipping-returns, contact 네 개. 배송·반품 정책은 브리프에 있으면 그대로, "
            "없으면 '[운영자가 확정 필요]'라고 표시한 자리표시 문구를 쓴다.\n"
            "6) menu: handle 'main-menu'. Home(FRONTPAGE), 각 컬렉션(COLLECTION), Shop all(CATALOG), "
            "About·FAQ(PAGE) 순서. COLLECTION/PAGE 항목은 target_handle에 이 계획의 handle을 쓴다.\n"
            "7) handle은 영어 소문자와 하이픈만 쓴다. 모든 approved 값은 false로 둔다 (승인은 사람이 한다).\n"
            "8) 고객에게 보이는 문구(title, description, page body)는 브리프가 지정한 언어로 쓴다. "
            "지정이 없으면 영어로 쓴다."
        ),
        expected_output=(
            "brand_name, brief_summary, collections, products, pages, menu 필드를 가진 StorePlan. "
            "모든 approved는 false."
        ),
        agent=agent,
        output_pydantic=StorePlan,
    )
