# Automated Tests — Shopify Ops Agent

`pytest`로 돌아가는 61개 단위 테스트 (Phase 1: 20개, Phase 2A: 41개)의 목록과 검증 범위입니다.
모두 외부 API(Shopify, Anthropic)를 **mock 처리**하므로 실제 키나 네트워크 없이
실행됩니다 — 검증 대상은 우리가 짠 파싱·에러처리·계약 로직이지, Shopify가
살아있는지가 아닙니다.

```
pytest -v
# 61 passed
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

---

## Phase 2A — 스토어 셋업 (41개)

Phase 2A 테스트도 같은 원칙입니다. Shopify 호출은 `requests.post` mock 또는 가짜 API 객체로
대체하고, LLM(Builder) 실행은 단위 테스트하지 않습니다 (수동 TC-10에서 검증).

### tests/test_store_plan.py — StorePlan 계약 (14개, parametrize 포함)

| ID | 테스트 | 무엇을 검증하는가 |
|---|---|---|
| T-20 | `test_handles_are_normalised` | "Hydration 10-Pack!" 같은 입력이 `hydration-10-pack` handle로 정규화되는가 (컬렉션 참조 포함) |
| T-21 | `test_empty_handle_rejected` | 기호만 있는 handle을 거부하는가 |
| T-22 | `test_price_normalised_to_two_decimals` | `24` → `24.00`, `$7.5` → `7.50`으로 맞춰지는가 |
| T-23 | `test_invalid_price_rejected` (×3) | 숫자가 아니거나 0 이하인 가격을 거부하는가 |
| T-24 | `test_items_default_to_unapproved` | 모든 항목의 기본값이 미승인인가 (승인은 사람만) |
| T-25 | `test_consistent_plan_has_no_problems` | 정상 계획에서 `reference_problems()`가 빈 리스트인가 |
| T-26 | `test_unknown_collection_reference_reported` | 계획에 없는 컬렉션을 참조하는 상품을 잡아내는가 |
| T-27 | `test_duplicate_handles_reported` | 같은 섹션 안의 중복 handle을 잡아내는가 |
| T-28 | `test_menu_item_without_target_reported` | COLLECTION/PAGE 메뉴 항목에 대상이 없으면 잡아내는가 |
| T-29 | `test_menu_link_to_unknown_page_reported` | 계획에 없는 페이지로 가는 메뉴 링크를 잡아내는가 |
| T-30 | `test_plan_round_trips_through_json` | JSON 저장 → 로드 후 동일한 계획인가 |
| T-31 | `test_approval_counts` | 섹션별 (승인, 전체) 집계가 맞는가 |

### tests/test_shopify_write_tool.py — 쓰기 레이어 (9개)

| ID | 테스트 | 무엇을 검증하는가 |
|---|---|---|
| T-32 | `test_upsert_product_uses_handle_identifier_and_price` | `productSet`에 handle identifier·가격·컬렉션 ID·ACTIVE 상태가 실리고, SEO가 비면 생략되는가 |
| T-33 | `test_user_errors_raise` | 200 응답 안의 `userErrors`를 `ShopifyUserError`로 바꿔 실패를 성공으로 오인하지 않는가 |
| T-34 | `test_create_collection_sends_new_input_shape` | deprecated `input`이 아니라 `collection` 인자로 보내는가 |
| T-35 | `test_find_collection_returns_none_when_missing` | 없는 handle이면 `None`을 돌려주는가 |
| T-36 | `test_find_page_requires_exact_handle_match` | 퍼지 검색 결과(`about-us`)를 `about`으로 오인하지 않는가 |
| T-37 | `test_create_page_publishes` | 페이지를 공개 상태로 만들고 본문을 그대로 보내는가 |
| T-38 | `test_online_store_publication_found_by_name` | 여러 채널 중 "Online Store" publication을 찾아내는가 |
| T-39 | `test_publication_id_override_skips_lookup` | 환경변수로 ID를 주면 조회 없이 쓰는가 |
| T-40 | `test_missing_online_store_publication_raises` | Online Store 채널이 없으면 안내 메시지와 함께 실패하는가 |

### tests/test_store_executor.py — Executor 보장 사항 (12개)

| ID | 테스트 | 무엇을 검증하는가 |
|---|---|---|
| T-41 | `test_dry_run_makes_no_writes` | dry run에서는 읽기만 하고 쓰기 호출이 0회인가 |
| T-42 | `test_execute_applies_in_dependency_order` | 컬렉션 → 상품 → 페이지 → 메뉴 순서로 적용되는가 |
| T-43 | `test_products_receive_ids_of_collections_created_this_run` | 같은 실행에서 만든 컬렉션 ID가 상품에 연결되는가 |
| T-44 | `test_collections_and_products_are_published` | 컬렉션과 상품이 Online Store 채널에 게시되는가 |
| T-45 | `test_unapproved_items_are_skipped` | 미승인 항목은 쓰기 없이 `skipped`로만 기록되는가 |
| T-46 | `test_existing_handles_are_not_recreated` | 이미 있는 컬렉션·페이지는 다시 만들지 않고 기존 ID를 재사용하는가 (멱등성) |
| T-47 | `test_menu_items_resolve_to_resource_ids` | 메뉴 항목이 고정 URL 또는 리소스 ID로 정확히 변환되는가 |
| T-48 | `test_one_failure_does_not_stop_the_run` | 한 항목 실패가 기록되고, 이후 섹션은 계속 실행되는가 |
| T-49 | `test_plan_with_problems_is_refused_before_any_call` | 문제가 있는 계획은 API 호출 전에 거부되는가 |
| T-50 | `test_every_action_is_written_to_the_audit_log` | 모든 액션이 JSONL 감사 로그에 한 줄씩 남는가 |
| T-51 | `test_summary_counts_outcomes` | 결과 요약 집계가 맞는가 |
| T-52 | `test_dry_run_reports_menu_links_not_yet_in_store` | dry run에서 아직 스토어에 없는 메뉴 대상을 알려주는가 |

### tests/test_setup_store_cli.py — CLI (6개)

| ID | 테스트 | 무엇을 검증하는가 |
|---|---|---|
| T-53 | `test_save_and_load_plan_round_trip` | 계획 파일 저장·로드가 손실 없이 되는가 |
| T-54 | `test_review_approve_all` | `--approve-all`이 모든 항목을 승인하는가 |
| T-55 | `test_review_interactive_only_yes_approves` | 대화형 리뷰에서 y/yes만 승인으로 인정하는가 (빈 입력은 거부) |
| T-56 | `test_review_command_runs_offline_without_keys` | `review`는 API 키 없이도 실행되는가 |
| T-57 | `test_apply_defaults_to_dry_run` | `apply`가 `--execute` 없이는 dry run으로 도는가 |
| T-58 | `test_describe_plan_lists_problems` | 계획 요약에 고쳐야 할 문제가 표시되는가 |

---

## 자동 vs 수동 테스트 역할 분담

| | 자동 (pytest, 이 문서) | 수동 ([USER_TEST_CASES.md](USER_TEST_CASES.md)) |
|---|---|---|
| 검증 대상 | 내부 로직 (파싱·계약·에러처리) | 실제 Shopify/Anthropic API 실행 경로·결과 품질 |
| API 호출 | 없음 (모킹) | 있음 (실제 키 + 실제 스토어) |
| 실행 주체 | CI/개발자 | 사람 (보고서 내용을 눈으로 판단) |
| 속도 | 빠름 (1초 내외) | 느림 (분 단위, 네트워크·모델 응답 영향) |
