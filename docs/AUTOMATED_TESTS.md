# Automated Tests — Shopify Ops Agent

`pytest`로 돌아가는 20개 단위 테스트의 목록과 검증 범위입니다.
모두 외부 API(Shopify, Anthropic)를 **mock 처리**하므로 실제 키나 네트워크 없이
실행됩니다 — 검증 대상은 우리가 짠 파싱·에러처리·계약 로직이지, Shopify가
살아있는지가 아닙니다.

```
pytest -v
# 20 passed
```

---

## tests/test_shopify_tool.py — Shopify Admin API 도구 (11개)

| ID | 테스트 | 무엇을 검증하는가 |
|---|---|---|
| T-01 | `test_products_tool_formats_catalog` | 정상 GraphQL 응답을 받으면 상품명·가격·재고수량이 사람이 읽을 수 있는 텍스트로 포맷팅되는가 |
| T-02 | `test_products_tool_empty_catalog` | 상품이 0개일 때 크래시 없이 "No products found in this store." 반환하는가 |
| T-03 | `test_products_tool_passes_limit_as_graphql_variable` | `limit` 인자가 실제 GraphQL 요청의 `variables.first`로 정확히 전달되는가 |
| T-04 | `test_orders_tool_formats_orders` | 주문번호·금액·라인아이템이 올바르게 포맷팅되는가 |
| T-05 | `test_orders_tool_empty_orders` | 주문이 0건일 때 크래시 없이 "No orders found in this store." 반환하는가 |
| T-06 | `test_graphql_errors_raise_runtime_error` | Shopify가 GraphQL `errors`를 반환하면(예: 잘못된 API 키) `RuntimeError`로 변환해 에이전트가 인지할 수 있게 하는가 |
| T-07 | `test_http_error_propagates` | HTTP 레벨 에러(503 등)가 삼켜지지 않고 그대로 올라오는가 |
| T-07a | `test_token_exchanged_via_client_credentials` | client credentials grant로 `/admin/oauth/access_token`에 올바른 파라미터를 보내 토큰을 받아오는가 |
| T-07b | `test_token_cached_until_expiry` | 유효한 토큰이 캐시에 있으면 재발급 요청 없이 재사용하는가 |
| T-07c | `test_expired_token_is_reexchanged` | 만료된 토큰은 버리고 새로 교환하는가 |
| T-07d | `test_token_exchange_failure_raises` | 토큰 교환 실패 응답(`access_token` 없음)이 `RuntimeError`로 드러나는가 |

## tests/test_schemas.py — I/O 계약 (Pydantic 스키마) (5개)

| ID | 테스트 | 무엇을 검증하는가 |
|---|---|---|
| T-08 | `test_flagged_product_creates_ok` | 필수 필드를 모두 채우면 `FlaggedProduct`가 정상 생성되는가 |
| T-09 | `test_flagged_product_missing_required_field_errors` | 필수 필드(`variant_id`) 누락 시 `ValidationError`로 막히는가 |
| T-10 | `test_flagged_product_inventory_quantity_must_be_int` | `inventory_quantity`에 숫자가 아닌 값이 들어오면 거부되는가 |
| T-11 | `test_analysis_report_creates_ok` | `AnalysisReport`가 `date`/`flagged_products`/`markdown`을 모두 담아 정상 생성되는가 |
| T-12 | `test_analysis_report_allows_empty_flagged_products` | 매장에 이상이 없을 때(플래그 0건) 빈 리스트가 에러 없이 허용되는가 |

## tests/test_config_and_main.py — 설정 검증 + main.py (4개)

| ID | 테스트 | 무엇을 검증하는가 |
|---|---|---|
| T-13 | `test_validate_errors_when_no_keys` | `ANTHROPIC_API_KEY`/`SHOPIFY_STORE_DOMAIN`/`SHOPIFY_CLIENT_ID`/`SHOPIFY_CLIENT_SECRET`이 모두 없을 때 에러 메시지에 네 키 이름이 다 포함되는가 |
| T-14 | `test_validate_passes_with_keys` | 네 키가 모두 있으면 예외 없이 통과하는가 |
| T-15 | `test_validate_errors_when_partial_keys` | 일부 키만 없을 때 메시지에 누락된 키만 정확히 언급되는가 (있는 키는 언급 안 함) |
| T-16 | `test_save_report_creates_file` | `main.save_report()`가 실제로 `.md` 파일을 만들고 내용을 정확히 쓰는가 |

## 스토어 세팅 (store setup) 테스트

위 20개와 별도로 추가된 테스트 파일입니다. 마찬가지로 전부 mock이라 실제
스토어에는 아무것도 쓰지 않습니다.

| 파일 | 무엇을 검증하는가 |
|---|---|
| `tests/test_store_plan.py` | brief 검증(중복 key, slug 형식, 세트 구성, 가격 소수 둘째 자리까지), 모델이 쓴 HTML 정리(`<embed>` 뒤 내용 보존, 닫히지 않은 `<script>` 감지), 태그 정규화, 사실 값(상품명·가격·브랜드)은 brief에서만 오는지, 문구 누락·의학적 주장(설명·카테고리·태그·컬렉션 설명 전부) 시 `needs_review` 처리, 흔한 화장품 표현(prevent, treat, acne 단독)은 막지 않고 경고만 하는지, `<br/>`·`&nbsp;` 같은 무해한 수정은 통과하고 금지된 태그·속성은 이름을 짚어 거부하는지, 사람이 고친 plan 파일 재검증(`plan_problems`), 같은 날 plan 파일 덮어쓰기 방지 |
| `tests/test_executor.py` | 새 상품은 DRAFT로 생성되는지, 다시 실행해도 게시된 상품이 초안으로 돌아가지 않는지, 이 세팅이 만들지 않은 상품·컬렉션(스토어 태그/태그 규칙 없음)은 건드리지 않는지, 충돌은 쓰기 전 사전 조회에서 전부 찾아 아무것도 쓰지 않고 끝나는지, 조회 필드가 스키마에 없으면 '없음'이 아니라 에러로 멈추는지, Shopify `userErrors`가 에러로 드러나는지, 문제 있는 plan은 쓰기 전에 전체 거부되는지, 첫 실패에서 멈추고 나머지를 skipped로 기록하는지, audit 로그 |
| `tests/test_setup_store.py` | `apply`가 `apply`를 직접 입력하지 않으면 아무것도 쓰지 않는지, 거부될 plan은 승인 질문 전에 거부되는지, `apply`가 `ANTHROPIC_API_KEY`와 crewai 없이 도는지, 같은 날 `plan` 재실행이 모델 호출 전에 막히는지, brief 오류·계획 실패 메시지, 모델에게 주는 프롬프트에 가격이 들어가지 않는지 |

---

## 자동 vs 수동 테스트 역할 분담

| | 자동 (pytest, 이 문서) | 수동 ([USER_TEST_CASES.md](USER_TEST_CASES.md)) |
|---|---|---|
| 검증 대상 | 내부 로직 (파싱·계약·에러처리) | 실제 Shopify/Anthropic API 실행 경로·결과 품질 |
| API 호출 | 없음 (모킹) | 있음 (실제 키 + 실제 스토어) |
| 실행 주체 | CI/개발자 | 사람 (보고서 내용을 눈으로 판단) |
| 속도 | 빠름 (1초 내외) | 느림 (분 단위, 네트워크·모델 응답 영향) |
