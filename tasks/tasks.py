"""
Task definitions for each agent.

Why this design?
- Agent is "who"; Task is "what to do" - kept separate so the analyst can be
  reused for different tasks later.
- output_pydantic enforces the I/O contract (AnalystOutput -> AnalysisReport
  via enrich.py), so Phase 2's
  deterministic executor / Streamlit UI can consume this without re-parsing free text.
- The date is injected by code (not guessed by the LLM) - same principle as
  daily-briefing-agent's write task.
"""

from datetime import date

from crewai import Agent, Task

from config import settings
from models.schemas import AnalystOutput, PlannerOutput, StoreBrief


def build_planning_task(agent: Agent, brief: StoreBrief) -> Task:
    """Store-setup copy task. The brief is the only source the model may use."""
    lines = []
    for p in brief.products:
        line = f"- key: {p.key} | 상품명: {p.title} | 설명: {p.one_liner}"
        if p.bundle_of:
            line += f" | 세트 구성(key): {', '.join(p.bundle_of)}"
        lines.append(line)
    catalog = "\n".join(lines)

    return Task(
        description=(
            f"아래 상품들의 Shopify 상품 페이지 문구를 영어로 작성하라 (미국 소비자 대상).\n\n"
            f"컬렉션: {brief.collection_title}\n"
            f"상품 목록:\n{catalog}\n\n"
            f"규칙:\n"
            f"1) 상품마다 products에 한 항목씩, key는 위 목록의 key를 그대로 복사한다. "
            f"목록에 없는 상품을 추가하지 않는다.\n"
            f"2) description_html은 p, ul, li, strong, em 태그만 쓴 간단한 HTML로, "
            f"소개 문단 1개와 특징 불릿 3개 이내로 작성한다.\n"
            f"3) 위 '설명'에 있는 내용만 근거로 쓴다. 성분 함량, 용량, 수상 내역, 판매량, "
            f"임상 결과처럼 주어지지 않은 사실을 지어내지 않는다.\n"
            f"4) 화장품 문구이므로 치료·예방·의학적 효능을 주장하지 않는다. "
            f"cure, heal, treat, prevent, acne, clinically proven 같은 단어는 쓰지 않는다 "
            f"(의학적 주장으로 읽히면 그 상품은 사람 검토로 넘어간다). 사용감과 겉보기 효과만 표현한다.\n"
            f"5) 가격, 상품명, 브랜드는 시스템이 채우므로 문구에 가격을 적지 않는다.\n"
            f"6) product_type은 짧은 카테고리명(예: 'Cleansing Oil'), tags는 소문자 검색어 3~6개.\n"
            f"7) 세트 상품은 구성품을 사용 순서대로 소개한다.\n"
            f"8) collection_description_html은 컬렉션을 소개하는 짧은 문단 1개."
        ),
        expected_output=(
            "collection_description_html과 products(key, description_html, product_type, tags) "
            "필드를 가진 구조화된 결과."
        ),
        agent=agent,
        output_pydantic=PlannerOutput,
    )


def build_analysis_task(agent: Agent) -> Task:
    today = date.today().isoformat()
    return Task(
        description=(
            f"Shopify 스토어의 운영 상태를 점검하라.\n"
            f"1) get_products로 카탈로그 전체(최대 {settings.PRODUCTS_LIMIT}개)를 조회한다.\n"
            f"2) get_orders로 최근 주문(최대 {settings.ORDERS_LIMIT}건)을 조회한다.\n"
            f"3) 두 데이터를 비교해 다음 두 유형의 상품을 찾는다:\n"
            f"   - 재고 수량이 {settings.LOW_STOCK_THRESHOLD}개 이하면서 최근 주문에 등장한 상품 (품절 위험)\n"
            f"   - 재고는 충분한데 최근 주문에 전혀 등장하지 않는 상품 (저성과)\n"
            f"4) 각 플래그 상품마다 picks에 title(상품명), variant_title('variant:' 뒤 이름), reason을 "
            f"get_products 출력 그대로 기록한다. variant ID와 재고 수량은 시스템이 채우므로 적지 않는다.\n"
            f"5) 보고서 markdown은 아래 형식을 따른다 (date 필드는 정확히 '{today}' 사용):\n\n"
            f"# Shopify 운영 보고서 ({today})\n"
            f"## 품절 위험\n- [상품명] 재고 N개, 최근 주문 있음\n"
            f"## 저성과 재고\n- [상품명] 재고 N개, 최근 주문 없음\n"
        ),
        expected_output=(
            "date, picks, markdown 필드를 가진 구조화된 분석 보고서. "
            "picks는 가장 긴급한 항목이 먼저 오도록 정렬한다."
        ),
        agent=agent,
        output_pydantic=AnalystOutput,
    )
