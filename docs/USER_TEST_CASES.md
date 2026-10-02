# 🧾 User Test Cases — Shopify Ops Agent

사람이 직접 실행하며 검증하는 **수동 인수 테스트(UAT, User Acceptance Test)** 입니다.
자동 단위 테스트(`pytest`, [AUTOMATED_TESTS.md](AUTOMATED_TESTS.md))가 다루지 못하는
**실제 Shopify 스토어 + 실제 Claude API 실행 경로**를 사람이 확인합니다.

## 사용법
- 각 케이스를 위에서 아래로 순서대로 실행하세요.
- `결과` 칸에 ✅(통과) / ❌(실패)를 기록하세요.
- ❌가 나오면 `비고`에 증상을 적고, 해당 단계를 다시 점검하세요.
- TC-02/TC-03은 Shopify Partner 개발자 스토어에서 직접 상품/주문을 만들어야
  합니다 (운영 중인 실 스토어가 아니라 무료 dev 스토어 기준).

---

## 사전 준비 (Setup)

| ID | 항목 | 명령 / 확인 | 기대 결과 | 결과 |
|---|---|---|---|---|
| S-1 | 가상환경 생성 | `python -m venv .venv && source .venv/bin/activate` | 프롬프트에 `(.venv)` 표시 | ✅ 7/15 |
| S-2 | 의존성 설치 | `pip install -r requirements.txt` | 오류 없이 설치 완료 | ✅ 7/15 (crewai 1.15.2 업그레이드 포함) |
| S-3 | 키 파일 생성 | `cp .env.example .env` 후 4개 키 입력 (`ANTHROPIC_API_KEY`, `SHOPIFY_STORE_DOMAIN`, `SHOPIFY_CLIENT_ID`, `SHOPIFY_CLIENT_SECRET`) | `.env`에 네 값 모두 채워짐 | ✅ 7/15 (`settings.validate()` 통과) |
| S-4 | Shopify 스토어 데이터 | Partner dev 스토어 admin에서 상품 5개 이상 등록 | 상품 목록에 5개 이상 표시 | ✅ 7/15 (상품 47·주문 20건, API count로 확인) |
| S-5 | 자동 테스트 통과 | `pytest -v` | `20 passed` (Phase 2A 이후 `61 passed`) | ✅ 7/15 (`20 passed in 0.75s`) |

---

## TC-01 · 정상 동작 (Happy Path)

> **목적:** 실제 스토어 데이터로 보고서가 끝까지 생성되는가

| 단계 | 동작 | 기대 결과 |
|---|---|---|
| 1 | `python main.py` 실행 | "Starting Shopify ops analysis" 로그 출력 |
| 2 | 실행 과정 관찰 | analyst가 `get_products`, `get_orders` 도구를 순서대로 호출하는 로그(verbose) 진행 |
| 3 | 완료 대기 | "Report complete -> outputs/ops_report_YYYY-MM-DD.md" 출력 |
| 4 | 파일 확인 | `outputs/`에 오늘 날짜 `.md` 파일 생성됨 |

**결과:** ✅ (2026-07-15)  **비고:** `outputs/ops_report_2026-07-15.md` 생성 확인. 단, 최초 실행들은 간헐 실패 — 원인은 CrewAI Anthropic provider의 `max_tokens=4096` 기본값을 thinking이 소진해 text 없이 잘리는 문제. `LLM(max_tokens=16384)` 적용 후 통과 (하단 E2E 검증 기록 E-6 참조)

---

## TC-02 · 품절 위험 탐지

> **목적:** 재고가 적은데 최근 주문이 있는 상품을 정확히 플래깅하는가
> **준비:** dev 스토어에서 상품 A의 재고를 5개 이하로 설정하고, 상품 A로 테스트 주문 1건 생성

| 단계 | 동작 | 기대 결과 |
|---|---|---|
| 1 | `python main.py` 실행 | 정상 완료 |
| 2 | 생성된 보고서의 "품절 위험" 섹션 확인 | 상품 A가 재고 수량과 함께 표시됨 |
| 3 | 재고 무관 다른 상품 확인 | 조건 미충족 상품은 이 섹션에 없음 |

**결과:** ✅ (2026-07-15)  **비고:** 수동 준비 대신 Simple Sample Data 시딩 데이터로 검증. 재고 3~5개 + 최근 주문 있는 상품 3건(ASICS GEL-LYTE V, VANS SK8-HI DECON, SUPRA VAIDER)이 재고 수량·수요/재고 비율과 함께 플래깅됨

---

## TC-03 · 저성과 재고 탐지

> **목적:** 재고는 충분한데 최근 주문이 전혀 없는 상품을 정확히 플래깅하는가
> **준비:** dev 스토어에서 상품 B의 재고를 충분히(예: 50개) 설정하고, 최근 주문에는 등장시키지 않음

| 단계 | 동작 | 기대 결과 |
|---|---|---|
| 1 | `python main.py` 실행 | 정상 완료 |
| 2 | 생성된 보고서의 "저성과 재고" 섹션 확인 | 상품 B가 표시됨 |
| 3 | 최근에 팔린 다른 상품 확인 | 이 섹션에 없음 |

**결과:** ✅ (2026-07-15)  **비고:** 재고 충분 + 최근 주문 미등장 상품 8건 플래깅 (CONVERSE CHUCK TAYLOR II 외). 에이전트가 스노보드 데모 상품(Shopify generated test data)을 실판매 상품과 구분해 제외하는 판단도 수행 — 기대 이상

---

## TC-04 · 빈 스토어 (경계값)

> **목적:** 상품이나 주문이 0건일 때도 크래시 없이 동작하는가
> **참고:** 운영 스토어에서 직접 재현하기 어려우므로, 자동 테스트(T-02, T-05)로 이미
> 검증됨. 별도 빈 dev 스토어가 있다면 실제로 한 번 돌려서 교차 확인 권장.

| 단계 | 동작 | 기대 결과 |
|---|---|---|
| 1 | 상품/주문이 없는 스토어에서 `python main.py` 실행 | "No products found"/"No orders found"를 도구가 반환해도 에이전트가 멈추지 않고 보고서 생성 |

**결과:** ☐ (도구 레벨만 부분 검증)  **비고:** 7/14 주문 0건 상태에서 `get_orders` 도구가 "No orders found in this store."를 크래시 없이 반환하는 것 확인. 전체 에이전트 실행 경로는 미검증 (자동 테스트 T-02/T-05로 커버)

---

## TC-05 · 잘못된 Shopify 토큰 (에러 처리)

> **목적:** credential이 무효할 때 깔끔히 안내하는가 (스택트레이스 노출 X)

| 단계 | 동작 | 기대 결과 |
|---|---|---|
| 1 | `.env`의 `SHOPIFY_CLIENT_SECRET`을 가짜 값으로 변경 | — |
| 2 | `python main.py` 실행 | "Analysis failed" + credential/권한 점검 안내 로그 출력, 스택트레이스 없이 종료 |
| 3 | credential 원복 | 정상 키로 되돌림 |

**결과:** ☐  **비고:** ___________

---

## TC-06 · 환경변수 누락 (사전 검증)

> **목적:** 키가 아예 없을 때 API 호출 전에 막는가
> **수용 기준 매핑:** `settings.validate()`

| 단계 | 동작 | 기대 결과 |
|---|---|---|
| 1 | `.env`에서 `SHOPIFY_STORE_DOMAIN` 줄 삭제 또는 비움 | — |
| 2 | `python main.py` 실행 | "Missing environment variables: SHOPIFY_STORE_DOMAIN" 메시지 후 API 호출 없이 즉시 중단 |
| 3 | 값 원복 | — |

**결과:** ☐  **비고:** ___________

---

## TC-07 · 보고서 형식 확인

> **목적:** 생성된 마크다운이 사람이 바로 읽을 수 있는 형식인가

| 단계 | 동작 | 기대 결과 |
|---|---|---|
| 1 | `outputs/ops_report_<오늘날짜>.md` 열기 | 마크다운 형식 정상 |
| 2 | 날짜 확인 | 제목의 날짜가 **오늘 날짜**와 일치 (코드가 주입 — LLM 추측 아님) |
| 3 | 섹션 확인 | "품절 위험", "저성과 재고" 섹션 모두 존재 |
| 4 | 각 항목 확인 | 상품명 + 재고 수량이 함께 표시됨 |

**결과:** ✅ (2026-07-15)  **비고:** 제목 날짜 `2026-07-15` 일치, 두 섹션 존재, 전 항목에 상품명·variant·재고 수량 표기. 선정 기준을 설명하는 "참고" 각주까지 포함

---

## TC-08 · 성능 (실행 시간)

> **목적:** 전체 실행이 합리적인 시간 내 끝나는가

| 단계 | 동작 | 기대 결과 |
|---|---|---|
| 1 | `time python main.py` (Linux/Mac) | 정상 완료 |
| 2 | `real` 시간 확인 | 상품/주문 20개 기준 **60초 이내** (네트워크·모델 응답 속도 영향) |

> ⚠️ 초과 시 `config/settings.py`의 `PRODUCTS_LIMIT`/`ORDERS_LIMIT`을 줄여보세요.

**결과:** ☐  **비고:** ___________

---

## TC-09 · 비용 확인 (선택)

> **목적:** 1회 실행 비용이 예상 범위 내인가

| 단계 | 동작 | 기대 결과 |
|---|---|---|
| 1 | 실행 전 Anthropic Console 사용량 메모 | — |
| 2 | `python main.py` 1회 실행 | 정상 완료 |
| 3 | 실행 후 사용량 증가분 확인 | 소액(보통 $0.05 이하) — 도구 호출 2회 + 분석 1회 수준 |

> 초과 시 `agents/analyst.py`의 `max_iter`를 낮추거나 `PRODUCTS_LIMIT`/`ORDERS_LIMIT`을 줄이세요.

**결과:** ☐  **비고:** ___________

---

## Phase 2A · 스토어 셋업 (setup_store.py)

> 모든 케이스는 **dev 스토어**에서만 실행합니다. 실행 전 S-6을 먼저 끝내세요.

| ID | 항목 | 명령 / 확인 | 기대 결과 | 결과 |
|---|---|---|---|---|
| S-6 | 쓰기 scope 추가 | Dev Dashboard → 앱 → Versions → 새 버전에 `write_products`, `write_content`, `read_online_store_navigation`, `write_online_store_navigation`, `read_publications`, `write_publications` 추가 → Release → 재설치 | 토큰 응답 `scope`에 6개가 추가로 보임 | ☐ |

### TC-10 · 계획 생성 (Builder)

| 단계 | 동작 | 기대 결과 |
|---|---|---|
| 1 | `python setup_store.py plan --brief briefs/example_brief.md` | `outputs/plans/store_plan_<오늘>.json` 생성, 요약에 "Problems" 없음 |
| 2 | JSON 열어보기 | 컬렉션 2~4개, 브리프의 상품 5개, 페이지 4개(about/faq/shipping-returns/contact), 메인 메뉴 |
| 3 | 내용 확인 | 브리프에 없는 효능·인증·후기 문구가 없음. 배송·반품은 브리프 내용 그대로 |
| 4 | 승인 상태 | 모든 `approved`가 `false` |

**결과:** ☐  **비고:** ___________

### TC-11 · 리뷰 (승인 게이트)

| 단계 | 동작 | 기대 결과 |
|---|---|---|
| 1 | `python setup_store.py review <plan.json>` | 항목마다 `Approve ...? [y/N]` 질문 |
| 2 | 상품 1개만 `n`, 나머지 `y` | 요약에 products `4/5 approved` |
| 3 | JSON 재확인 | 거절한 상품만 `approved: false` |

**결과:** ☐  **비고:** ___________

### TC-12 · Dry run (쓰기 없음)

| 단계 | 동작 | 기대 결과 |
|---|---|---|
| 1 | `python setup_store.py apply <plan.json>` | `DRY RUN (no writes)` 표시, 결과가 `would_*`/`skipped`/`exists`만 |
| 2 | Shopify admin 확인 | 상품·컬렉션·페이지·메뉴 변화 없음 |
| 3 | `outputs/audit_log.jsonl` 확인 | 모든 줄이 `"dry_run": true` |

**결과:** ☐  **비고:** ___________

### TC-13 · 실제 적용 (Execute)

| 단계 | 동작 | 기대 결과 |
|---|---|---|
| 1 | `python setup_store.py apply <plan.json> --execute` | `created`/`upserted`/`published`/`updated`, `error` 0건 |
| 2 | admin → Products | 승인한 상품만 Active로 생성, 컬렉션 연결됨. TC-11에서 거절한 상품은 없음 |
| 3 | admin → Online Store → Pages / Navigation | 페이지 4개 공개, Main menu가 계획한 순서로 교체됨 |
| 4 | 스토어 프론트(비밀번호 입력 후) | 메뉴에서 컬렉션·페이지로 이동 가능, 상품이 컬렉션에 보임 |

**결과:** ☐  **비고:** ___________

### TC-14 · 재실행 (멱등성)

| 단계 | 동작 | 기대 결과 |
|---|---|---|
| 1 | 같은 plan으로 `apply --execute` 한 번 더 | 컬렉션·페이지는 `exists`, 상품은 `upserted` |
| 2 | admin 확인 | 상품·컬렉션·페이지가 중복 생성되지 않음 |

**결과:** ☐  **비고:** ___________

### TC-15 · scope 누락 (에러 경로)

| 단계 | 동작 | 기대 결과 |
|---|---|---|
| 1 | S-6 전 상태(읽기 scope만)에서 `apply --execute` | 항목별 `error`로 기록되고 실행은 끝까지 진행, 종료 코드 1 |
| 2 | 감사 로그 확인 | 실패 이유(권한 오류)가 `detail`에 남음 |

**결과:** ☐  **비고:** ___________

---

## 결과 요약 (최종 갱신: 2026-07-15)

| 케이스 | TC-01 | TC-02 | TC-03 | TC-04 | TC-05 | TC-06 | TC-07 | TC-08 | TC-09 |
|---|---|---|---|---|---|---|---|---|---|
| 결과 | ✅ | ✅ | ✅ | ☐ | ☐ | ☐ | ✅ | ☐ | ☐ |

- **전체 통과 시:** Phase 1(분석 에이전트) 인수 완료 → Phase 2(실행 에이전트 + 승인 플로우) 착수 가능
- **실패 케이스 발생 시:** 비고 기록 → 해당 모듈 수정 후 재실행
- 잔여 4건: TC-04(빈 스토어 전체 경로), TC-05/06(에러 처리 — credential 훼손이 필요해 마지막에 몰아서 권장), TC-08/09(성능·비용 측정)

---

## E2E 검증 기록 — 2026-07-15 (스토어 셋업 + 인증 파이프라인)

TC 목록에 없는 셋업 단계 검증. 모두 live 스토어(`studio-haru-mjj1gdny.myshopify.com`) 상대로 실행한 결과다.

| ID | 시나리오 | 결과 | 상세 |
|---|---|---|---|
| E-1 | client credentials grant 토큰 교환 | ✅ | `POST /admin/oauth/access_token` → `access_token` 발급, `expires_in: 86348` (24h) |
| E-2 | 부여 scope 확인 | ✅ | 토큰 응답 `scope: read_inventory,read_orders,read_products` — 릴리스한 3종과 일치 |
| E-3 | 상품 조회 (`get_products`) | ✅ | generated test data + 시딩 상품 정상 반환, multi-variant 파싱 정상 |
| E-4 | 잘못된 스토어 도메인 (에러 경로) | ✅ | `studio-haru.myshopify.com`(오타 도메인) → 토큰 endpoint 404. 도메인 정정 후 정상. 교훈: 스토어 표시 이름 ≠ myshopify 서브도메인 |
| E-5 | 주문 조회 protected customer data 관문 | ✅ | `read_orders` scope에도 Order 객체 조회 `ACCESS_DENIED` → **custom distribution 선택으로 해결** (custom app은 심사 없이 접근 포함). 주의: distribution 선택은 비가역 |
| E-6 | CrewAI 간헐 실패 근본 원인 진단 | ✅ | 증상: `Invalid response from LLM call - None or empty` 간헐 발생. 계측 결과 `stop_reason=max_tokens`, 응답 블록 `['thinking']`만 존재 — provider 기본 `max_tokens=4096`을 thinking이 소진해 text 없이 잘림. `LLM(max_tokens=16384)`로 해결, 이후 재발 없음 |
| E-7 | 주문 데이터 시딩 검증 | ✅ | Simple Sample Data(Clothes 테마) 실행 후 API count 폴링으로 상품 47·주문 20 도달 확인 |
