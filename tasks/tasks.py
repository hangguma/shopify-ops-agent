"""
Task definitions for each agent.

Why this design?
- Agent is "who"; Task is "what to do" - kept separate so the analyst can be
  reused for different tasks later.
- output_pydantic enforces the I/O contract (AnalysisReport), so Phase 2's
  deterministic executor / Streamlit UI can consume this without re-parsing free text.
- The date is injected by code (not guessed by the LLM) - same principle as
  daily-briefing-agent's write task.
"""

from datetime import date

from crewai import Agent, Task

from config import settings
from models.schemas import AnalysisReport


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
            f"4) 각 플래그 상품에 variant_id, reason, inventory_quantity를 기록한다.\n"
            f"5) 보고서 markdown은 아래 형식을 따른다 (date 필드는 정확히 '{today}' 사용):\n\n"
            f"# Shopify 운영 보고서 ({today})\n"
            f"## 품절 위험\n- [상품명] 재고 N개, 최근 주문 있음\n"
            f"## 저성과 재고\n- [상품명] 재고 N개, 최근 주문 없음\n"
        ),
        expected_output=(
            "date, flagged_products, markdown 필드를 가진 구조화된 분석 보고서. "
            "flagged_products는 가장 긴급한 항목이 먼저 오도록 정렬한다."
        ),
        agent=agent,
        output_pydantic=AnalysisReport,
    )
