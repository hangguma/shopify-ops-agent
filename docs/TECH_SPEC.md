# Tech Spec — Shopify Ops Agent

| 항목 | 내용 |
|---|---|
| 문서 상태 | Phase 1·2A는 코드 기준 명세, 2B·2C는 설계 초안 |
| 관련 문서 | [REQUIREMENTS.md](REQUIREMENTS.md) · [ARCHITECTURE.md](ARCHITECTURE.md) · [AUTOMATED_TESTS.md](AUTOMATED_TESTS.md) |

---

## 1. 기술 스택

| 구분 | 선택 | 버전 | 비고 |
|---|---|---|---|
| 언어 | Python | 3.10+ (3.13에서 테스트) | `str \| None` 문법 사용 |
| 에이전트 프레임워크 | CrewAI | 1.15.2 | LiteLLM 경유로 Anthropic 호출 |
| LLM SDK | anthropic | 0.105.2 | CrewAI Anthropic provider 필수 의존 |
| LLM 모델 | `anthropic/claude-sonnet-5` | — | `LLM_MODEL` 환경변수로 변경 |
| 데이터 계약 | Pydantic | 2.12.5 | `output_pydantic`, JSON 직렬화 |
| HTTP | requests | ≥ 2.32 | Admin GraphQL, 토큰 교환 |
| 설정 | python-dotenv | 1.2.2 | `.env` 로드 |
| 테스트 | pytest | ≥ 8.0 | 전부 mock |
| Shopify API | Admin GraphQL | 2026-04 | `SHOPIFY_API_VERSION` |
| (2C) UI | Streamlit | 1.58.0 | requirements.txt에 주석 처리 |
| (2C) 관측 | AgentOps | 0.4.21 | requirements.txt에 주석 처리 |

---

## 2. 디렉터리와 모듈

```
shopify-ops-agent/
├── main.py                    # Phase 1 CLI
├── setup_store.py             # Phase 2A CLI: plan / review / apply
├── crew.py                    # 분석 크루
├── crew_setup.py              # 셋업 크루
├── agents/
│   ├── analyst.py             # Analyst (읽기 도구 2개)
│   └── builder.py             # Builder (읽기 도구 1개)
├── tasks/
│   ├── tasks.py               # 분석 태스크 → AnalysisReport
│   └── store_setup.py         # 셋업 태스크 → StorePlan
├── models/
│   ├── schemas.py             # FlaggedProduct, AnalysisReport
│   └── store_plan.py          # StorePlan과 Draft 모델
├── executor/
│   └── store_executor.py      # StoreExecutor, ActionRecord, PlanError
├── tools/
│   ├── shopify_tool.py        # 토큰, _graphql_request, 읽기 도구
│   └── shopify_write_tool.py  # 쓰기 함수 (도구 아님)
├── config/
│   ├── settings.py            # 환경변수, 파라미터, validate()
│   └── logging_setup.py       # 콘솔 + 파일 로그
├── briefs/example_brief.md
├── tests/                     # 61개 단위 테스트
└── outputs/                   # 보고서, 계획, 감사 로그, 실행 로그 (git 제외)
```

---

## 3. 설정

### 3.1 환경변수 (`.env`)

| 변수 | 필수 | 기본값 | 용도 |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | ✅ | — | LLM 호출 |
| `SHOPIFY_STORE_DOMAIN` | ✅ | — | `<store>.myshopify.com` (표시 이름 아님) |
| `SHOPIFY_CLIENT_ID` | ✅ | — | Dev Dashboard 앱 client id |
| `SHOPIFY_CLIENT_SECRET` | ✅ | — | Dev Dashboard 앱 client secret |
| `SHOPIFY_API_VERSION` | | `2026-04` | Admin API 버전 |
| `LLM_MODEL` | | `anthropic/claude-sonnet-5` | LiteLLM 모델 문자열 |
| `SHOPIFY_ONLINE_STORE_PUBLICATION_ID` | | — | 지정 시 Online Store publication 조회 생략 |

`settings.validate()`는 필수 4개 중 빠진 것을 모두 모아 `ValueError`로 알린다. `setup_store.py review`는 오프라인 명령이라 검증을 건너뛴다.

### 3.2 코드 상수 (`config/settings.py`)

| 상수 | 값 | 의미 |
|---|---|---|
| `PRODUCTS_LIMIT` | 50 | 분석 1회 조회 상품 수 |
| `ORDERS_LIMIT` | 20 | 분석 1회 조회 주문 수 |
| `LOW_STOCK_THRESHOLD` | 5 | 이하이면 재고 부족 |
| `OUTPUT_DIR` | `outputs` | 산출물 루트 |
| `STORE_PLAN_DIR` | `outputs/plans` | 계획 파일 |
| `AUDIT_LOG_PATH` | `outputs/audit_log.jsonl` | 감사 로그 |

### 3.3 Shopify 앱 scope

| Phase | scope | 쓰는 곳 |
|---|---|---|
| 1 | `read_products`, `read_inventory` | 상품·재고 조회 |
| 1 | `read_orders` | 주문 조회 (custom distribution 필요) |
| 2A | `write_products` | `productSet`, `collectionCreate` |
| 2A | `write_content` | `pageCreate` (`write_online_store_pages`로도 가능) |
| 2A | `read_online_store_navigation` | `menus` 조회 |
| 2A | `write_online_store_navigation` | `menuUpdate` |
| 2A | `read_publications` | `publications` 조회 |
| 2A | `write_publications` | `publishablePublish` |

scope를 바꾸면 Dev Dashboard에서 새 버전을 릴리스하고 dev 스토어에 다시 설치해야 한다.

---

## 4. Shopify 접근 레이어

### 4.1 인증 (`tools/shopify_tool.py`)

```
POST https://{SHOPIFY_STORE_DOMAIN}/admin/oauth/access_token
     grant_type=client_credentials, client_id, client_secret
→ { access_token, expires_in (≈86400), scope }
```

- `_token_cache = {"access_token", "expires_at"}`를 프로세스 메모리에 둔다.
- 만료 60초 전(`_TOKEN_REFRESH_MARGIN`)부터 재발급한다.
- 응답에 `access_token`이 없으면 `RuntimeError("Shopify token exchange failed: ...")`.

### 4.2 GraphQL 요청

`_graphql_request(query: str, variables: dict | None = None) -> dict`

| 상황 | 처리 |
|---|---|
| HTTP 오류 (4xx/5xx) | `raise_for_status()` 예외 그대로 전파 |
| 응답에 `errors` | `RuntimeError("Shopify GraphQL error: ...")` |
| 정상 | `body["data"]` 반환 |
| mutation의 `userErrors` | 이 함수는 판단하지 않음 → 쓰기 레이어가 처리 (4.4) |

타임아웃은 30초. 재시도는 하지 않는다 (재실행 멱등성으로 대체).

### 4.3 읽기 도구 (CrewAI `BaseTool`)

| 도구 이름 | 클래스 | 입력 | GraphQL | 반환 |
|---|---|---|---|---|
| `get_products` | `ShopifyProductsTool` | `limit: int = 20` | `products(first)` → id, title, status, totalInventory, variants(first: 5){id, title, price, inventoryQuantity} | 사람이 읽는 텍스트 목록, 0건이면 `"No products found in this store."` |
| `get_orders` | `ShopifyOrdersTool` | `limit: int = 20` | `orders(first, sortKey: CREATED_AT, reverse: true)` → name, createdAt, displayFulfillmentStatus, totalPriceSet, lineItems(first: 5) | 텍스트 목록, 0건이면 `"No orders found in this store."` |

도구는 LLM이 읽기 쉬운 텍스트를 반환한다 (JSON 아님). variant GID는 텍스트에 포함되어 보고서의 `variant_id`로 이어진다.

### 4.4 쓰기 함수 (`tools/shopify_write_tool.py`)

모든 mutation 응답의 `userErrors`가 비어 있지 않으면 `ShopifyUserError("<operation> failed: <field>: <message>; ...")`를 던진다.

| 함수 | GraphQL | 입력 | 반환 | 비고 |
|---|---|---|---|---|
| `find_collection_by_handle(handle)` | `collectionByIdentifier(identifier: {handle})` | handle | GID 또는 `None` | |
| `create_collection(draft)` | `collectionCreate(collection: CollectionCreateInput)` | `CollectionDraft` | GID | 생성 시 비공개 → 이후 `publish` 필요. deprecated `input` 인자 사용 안 함 |
| `upsert_product(draft, collection_ids)` | `productSet(input, identifier: {handle}, synchronous: true)` | `ProductDraft`, 컬렉션 GID 목록 | 상품 GID | 단일 variant("Title" / "Default Title"), `status: ACTIVE`, SEO는 값이 있을 때만. 목록 필드는 전체 교체 |
| `find_page_by_handle(handle)` | `pages(first: 1, query: "handle:<h>")` | handle | GID 또는 `None` | 검색이 퍼지라 handle 정확 일치만 인정 |
| `create_page(draft)` | `pageCreate(page: PageCreateInput)` | `PageDraft` | GID | `isPublished: true` |
| `find_menu_by_handle(handle)` | `menus(first: 25)` | handle | GID 또는 `None` | |
| `update_menu(menu_id, title, items)` | `menuUpdate(id, title, items)` | `MenuItemUpdateInput` dict 목록 | 메뉴 GID | 기존 항목 전체 교체. 기본 메뉴의 handle은 변경 불가 |
| `get_online_store_publication_id()` | `publications(first: 25)` | — | publication GID | `name == "Online Store"`로 찾음. 환경변수로 대체 가능. 없으면 `RuntimeError` |
| `publish(resource_id, publication_id)` | `publishablePublish(id, input: [{publicationId}])` | GID 2개 | `None` | 이미 게시된 경우에도 안전 |

---

## 5. 데이터 계약

### 5.1 Phase 1 (`models/schemas.py`)

**FlaggedProduct**

| 필드 | 타입 | 설명 |
|---|---|---|
| `title` | str | 상품명 |
| `variant_id` | str | variant GID |
| `reason` | str | 플래그 사유 |
| `inventory_quantity` | int | 현재 재고 |

**AnalysisReport**

| 필드 | 타입 | 설명 |
|---|---|---|
| `date` | str | `YYYY-MM-DD`, 코드가 주입 |
| `flagged_products` | list[FlaggedProduct] | 긴급한 순, 빈 리스트 허용 |
| `markdown` | str | 보고서 본문 |

### 5.2 Phase 2A (`models/store_plan.py`)

공통 규칙

- `slugify()`: 소문자화 → 영숫자 외 문자를 `-`로 → 양끝 `-` 제거. 결과가 비면 `ValueError`.
- `handle`, `collection_handles`, `target_handle`은 저장 전 자동으로 slug 정규화된다.
- `PlanItem.approved: bool = False` — 컬렉션·상품·페이지·메뉴가 상속.

**CollectionDraft** (PlanItem)

| 필드 | 타입 | 기본값 |
|---|---|---|
| `handle` | str | 필수 |
| `title` | str | 필수 |
| `description_html` | str | `""` |

**ProductDraft** (PlanItem)

| 필드 | 타입 | 기본값 | 검증 |
|---|---|---|---|
| `handle` | str | 필수 | slug |
| `title` | str | 필수 | |
| `description_html` | str | 필수 | |
| `product_type`, `vendor` | str | `""` | |
| `tags` | list[str] | `[]` | |
| `price` | str | 필수 | `$` 제거 후 Decimal, 0 초과, 소수 둘째 자리로 정규화 (`"24"` → `"24.00"`) |
| `collection_handles` | list[str] | `[]` | slug |
| `seo_title`, `seo_description` | str | `""` | |

**PageDraft** (PlanItem): `handle`, `title`, `body_html`

**MenuItemDraft**

| 필드 | 타입 | 설명 |
|---|---|---|
| `title` | str | 표시 이름 |
| `type` | `FRONTPAGE` \| `CATALOG` \| `SEARCH` \| `COLLECTION` \| `PAGE` | |
| `target_handle` | str \| None | COLLECTION·PAGE일 때 필수 |

고정 URL: `FRONTPAGE → /`, `CATALOG → /collections/all`, `SEARCH → /search`

**MenuDraft** (PlanItem): `handle = "main-menu"`, `title = "Main menu"`, `items: list[MenuItemDraft]`

**StorePlan**

| 필드 | 타입 |
|---|---|
| `brand_name` | str |
| `brief_summary` | str |
| `collections` | list[CollectionDraft] |
| `products` | list[ProductDraft] |
| `pages` | list[PageDraft] |
| `menu` | MenuDraft \| None |

| 메서드 | 반환 | 내용 |
|---|---|---|
| `reference_problems()` | list[str] | 섹션별 중복 handle, 상품의 미정의 컬렉션 참조, 메뉴의 대상 누락·미정의 컬렉션/페이지 참조. 빈 리스트면 정상 |
| `approval_counts()` | dict[str, (int, int)] | 섹션별 (승인 수, 전체 수) |

교차 참조 검사를 validator가 아닌 메서드로 둔 이유: LLM 출력에 참조 오류가 있어도 계획을 저장·표시·수정할 수 있어야 하기 때문이다. 적용 시점에만 강제한다.

계획 파일 예 (발췌):

```json
{
  "brand_name": "Haneul Mask Co.",
  "brief_summary": "Curated Korean sheet masks for a weekly routine.",
  "collections": [
    {"approved": true, "handle": "sheet-masks", "title": "Sheet Masks", "description_html": ""}
  ],
  "products": [
    {"approved": true, "handle": "hydration-10-pack", "title": "Hydration 10-pack",
     "description_html": "<p>Ten hydrating sheet masks.</p>", "product_type": "", "vendor": "",
     "tags": [], "price": "24.00", "collection_handles": ["sheet-masks"],
     "seo_title": "", "seo_description": ""}
  ],
  "pages": [
    {"approved": false, "handle": "about", "title": "About", "body_html": "<p>About us.</p>"}
  ],
  "menu": {
    "approved": true, "handle": "main-menu", "title": "Main menu",
    "items": [
      {"title": "Home", "type": "FRONTPAGE", "target_handle": null},
      {"title": "Sheet Masks", "type": "COLLECTION", "target_handle": "sheet-masks"}
    ]
  }
}
```

---

## 6. 에이전트와 태스크

| 항목 | Analyst | Builder |
|---|---|---|
| 파일 | `agents/analyst.py` | `agents/builder.py` |
| role | Shopify 운영 분석가 | Shopify 스토어 설계자 |
| 도구 | `get_products`, `get_orders` | `get_products` |
| LLM | `LLM(model=LLM_MODEL, max_tokens=16384)` | 동일 |
| `max_iter` | 4 | 4 |
| `allow_delegation` | False | False |
| 태스크 | `build_analysis_task` | `build_store_plan_task(agent, brief)` |
| 출력 계약 | `AnalysisReport` | `StorePlan` |
| 크루 | `crew.build_crew()` | `crew_setup.build_setup_crew(brief)` |

`max_tokens=16384`인 이유: provider 기본값 4096을 extended thinking이 소진해 text 없이 응답이 잘리는 문제가 있었다 (USER_TEST_CASES E-6).

### 6.1 Builder 태스크 규칙 (프롬프트로 강제)

1. `get_products`로 기존 카탈로그를 확인하고 같은 상품은 계획하지 않는다.
2. 컬렉션 2~4개, 고객의 선택 기준(용도·고민·라인)으로 나눈다.
3. 상품은 브리프 우선, 없으면 4~8개 제안. 단일 옵션, 가격은 숫자 문자열, 컬렉션은 계획 내 handle만.
4. 설명은 `<p>`, `<ul><li>`만 쓰는 짧은 HTML. 브리프에 없는 효능·인증·수치 금지.
5. 페이지는 about, faq, shipping-returns, contact. 정책이 브리프에 없으면 `[운영자가 확정 필요]` 자리표시.
6. 메뉴는 `main-menu`: Home → 각 컬렉션 → Shop all → About·FAQ.
7. handle은 영어 소문자·하이픈. `approved`는 전부 false.
8. 고객용 문구는 브리프가 지정한 언어, 없으면 영어.

규칙 7은 코드로도 강제한다: `cmd_plan`이 저장 전에 모든 `approved`를 false로 덮어쓴다.

---

## 7. Executor 명세 (`executor/store_executor.py`)

### 7.1 인터페이스

```python
StoreExecutor(
    plan: StorePlan,
    dry_run: bool = True,
    audit_path: str | None = None,          # 기본 settings.AUDIT_LOG_PATH
    api: ModuleType = shopify_write_tool,   # 테스트에서 가짜로 교체
)
.run() -> list[ActionRecord]                # PlanError 가능
.summary() -> dict[str, int]                # outcome별 개수
```

### 7.2 알고리즘

```
run():
  problems = plan.reference_problems()
  if problems: raise PlanError            # API 호출 전에 중단

  for c in collections:                   # 1. 컬렉션
    if not approved: record skipped; continue
    id = find_collection_by_handle(c)
    if id: remember; record exists; continue
    if dry_run: record would_create; continue
    id = create_collection(c); remember; record created
    publish(id)                            # record published / error

  for p in products:                      # 2. 상품
    if not approved: record skipped; continue
    ids = [resolve_collection(h) for h in p.collection_handles]   # 이번 실행 생성분 → 없으면 스토어 조회
    missing → detail에 기록, 연결에서 제외
    if dry_run: record would_upsert; continue
    id = upsert_product(p, ids); record upserted
    publish(id)

  for pg in pages:                        # 3. 페이지
    (컬렉션과 같은 find → create 패턴, publish 없음: isPublished=true로 생성)

  if menu:                                # 4. 메뉴
    if not approved: record skipped; return
    menu_id = find_menu_by_handle(menu.handle)
    if not menu_id: record error "menu not found in store"; return
    items = 고정 URL 타입 → {title, type, url}
            COLLECTION/PAGE → {title, type, resourceId} (해결 못 하면 제외, detail에 기록)
    if dry_run: record would_update; return
    update_menu(menu_id, menu.title, items); record updated
```

각 항목은 `try/except`로 감싸고, 예외는 그 항목의 `error`로 기록한 뒤 다음 항목으로 넘어간다. publication ID는 첫 게시 때 한 번만 조회해 캐시한다.

### 7.3 outcome 값

| outcome | 의미 | dry run에서 |
|---|---|---|
| `skipped` | 미승인 | ✅ |
| `exists` | 같은 handle이 이미 있음, 변경 안 함 | ✅ |
| `would_create` / `would_upsert` / `would_update` | 실행하면 생성/upsert/교체 | ✅ |
| `created` | 컬렉션·페이지 생성 | |
| `upserted` | 상품 생성 또는 갱신 | |
| `updated` | 메뉴 교체 | |
| `published` | Online Store 게시 | |
| `error` | 실패 (`detail`에 원인) | ✅ (조회 실패 시) |

### 7.4 감사 로그 형식

`outputs/audit_log.jsonl`에 액션마다 한 줄, append-only.

```json
{"section": "product", "handle": "hydration-10-pack", "outcome": "upserted",
 "detail": "price 24.00", "resource_id": "gid://shopify/Product/123",
 "dry_run": false, "timestamp": "2026-10-01T22:10:05+00:00"}
```

| 필드 | 타입 | 값 |
|---|---|---|
| `section` | str | `collection` \| `product` \| `page` \| `menu` \| `publish` |
| `handle` | str | 대상 handle |
| `outcome` | str | 7.3 표 |
| `detail` | str | 사람이 읽는 부가 정보 또는 오류 메시지 |
| `resource_id` | str | Shopify GID (있을 때) |
| `dry_run` | bool | |
| `timestamp` | str | UTC ISO 8601, 초 단위 |

---

## 8. CLI 명세

### 8.1 `main.py` (Phase 1)

| 명령 | 동작 | 출력 |
|---|---|---|
| `python main.py` | 설정 검증 → 분석 크루 실행 → 보고서 저장 | 콘솔 + `outputs/ops_report_<날짜>.md` |

LLM·Shopify 오류는 잡아서 점검 안내 로그를 남기고 종료한다 (스택트레이스 노출 안 함).

### 8.2 `setup_store.py` (Phase 2A)

| 명령 | 인자 | 키 필요 | 동작 | 종료 코드 |
|---|---|---|---|---|
| `plan` | `--brief PATH` (필수), `--out PATH` | ✅ | Builder 실행 → 승인 false 강제 → 계획 저장 → 요약 출력 | 0, StorePlan이 아니면 1 |
| `review` | `PLAN`, `--approve-all` | ❌ | 항목별 `[y/N]` (y/yes만 승인) → 같은 파일에 저장 → 요약 | 0 |
| `apply` | `PLAN`, `--execute` | ✅ | 기본 dry run. 결과 표, 요약, 감사 로그 경로 출력 | 0, `PlanError` 또는 `error` 항목이 있으면 1 |

공개 함수 (2C의 Streamlit이 재사용할 인터페이스):

```python
save_plan(plan: StorePlan, path: str | None = None) -> str
load_plan(path: str) -> StorePlan
review_plan(plan, approve_all=False, ask=input) -> StorePlan   # ask는 테스트·UI에서 교체
describe_plan(plan) -> str
format_records(records) -> str
```

---

## 9. 오류 처리 요약

| 오류 | 발생 위치 | 처리 |
|---|---|---|
| 필수 환경변수 누락 | `settings.validate()` | `ValueError`, API 호출 전 중단 |
| 토큰 교환 실패 | `_get_access_token()` | `RuntimeError` |
| HTTP 오류 | `_graphql_request()` | 예외 전파 |
| GraphQL `errors` | `_graphql_request()` | `RuntimeError` |
| mutation `userErrors` | 쓰기 함수 | `ShopifyUserError` |
| 계획 불일치 | `StoreExecutor.run()` 시작 | `PlanError`, 아무 호출도 하지 않음 |
| 항목 단위 실패 | Executor 각 섹션 | `error`로 기록, 다음 항목 진행, 종료 코드 1 |
| LLM 출력 계약 위반 | `cmd_plan` | 메시지 출력, 종료 코드 1 |

---

## 10. 테스트 전략

| 층 | 방식 | 개수 | 문서 |
|---|---|---|---|
| 읽기 도구·인증 | `requests.post` mock | 11 | T-01~T-07d |
| 계약 (Phase 1) | 순수 Pydantic | 5 | T-08~T-12 |
| 설정·main | patch | 4 | T-13~T-16 |
| StorePlan | 순수 Pydantic | 14 | T-20~T-31 |
| 쓰기 레이어 | `requests.post` mock | 9 | T-32~T-40 |
| Executor | 가짜 API 객체 (`FakeApi`) | 12 | T-41~T-52 |
| CLI | tmp 파일, patch | 6 | T-53~T-58 |
| **합계** | | **61** | |
| LLM 경로·실 스토어 | 수동 UAT | TC-01~TC-15 | USER_TEST_CASES.md |

원칙: 자동 테스트는 **우리 코드의 판단**(파싱, 계약, 순서, 멱등성, 기록)을 검증하고, Shopify·Claude가 실제로 어떻게 응답하는지는 수동 UAT에서 본다.

---

## 11. 확장 가이드

### 11.1 새 쓰기 기능을 추가할 때 (예: 2C 가격 변경)

1. `models/`에 새 Draft와 Plan 모델을 만든다. 모든 항목은 `PlanItem`을 상속해 `approved=False`로 시작한다.
2. `tools/shopify_write_tool.py`에 함수를 추가한다. **CrewAI 도구로 만들지 않는다.** `userErrors`는 `_raise_user_errors`로 처리한다.
3. `executor/`에 Executor를 만든다. dry run 기본, 항목별 try/except, `_record`로 감사 로그.
4. 에이전트는 읽기 도구만 가진다. 출력은 3의 Plan 모델.
5. 필요한 scope를 README·`.env.example`·TECH_SPEC 3.3에 추가한다.
6. 테스트: 계약, 쓰기 함수(mock), Executor(가짜 API)로 dry run 무쓰기·미승인 건너뜀·멱등성·감사 로그를 확인한다.

### 11.2 Phase 2B — 헤드리스 storefront (설계 초안)

| 항목 | 초안 |
|---|---|
| 위치 | 별도 디렉터리 `storefront/` 또는 별도 레포 (프론트 빌드 체인 분리) |
| 프레임워크 | Hydrogen(Shopify 공식, Oxygen 호스팅) 또는 Next.js. 결정은 ADR로 남긴다 |
| 데이터 | Storefront API (GraphQL). 2A가 만든 상품·컬렉션·페이지·`main-menu`를 그대로 조회 |
| 인증 | Storefront API 토큰 (공개 토큰, 읽기 전용 scope) |
| 결제 | `cart` API → Shopify checkout URL로 이동 |
| 에이전트 친화 | 상품·조직 JSON-LD, 정책 페이지 노출 |
| 이 레포와의 관계 | 쓰기는 계속 이 레포의 Executor만 한다. 프론트는 읽기 전용 |

### 11.3 Phase 2C — Ops executor + 승인 UI (설계 초안)

| 항목 | 초안 |
|---|---|
| 에이전트 | Ops Planner: `AnalysisReport`를 입력받아 조치안 제안 |
| 계약 | `OpsPlan`: `PriceChangeDraft(variant_id, current_price, new_price, reason)`, `ProductStatusDraft(product_id, new_status, reason)` 등, 모두 `PlanItem` |
| 안전 장치 | 가격 변경률 상한(예: ±20%) 초과 시 별도 확인 플래그 필요, 1회 적용 항목 수 상한 |
| 쓰기 | `productVariantsBulkUpdate`(가격), `productUpdate`(상태) — 버전별 시그니처는 구현 시 공식 문서로 재확인 |
| Executor | `OpsExecutor`, StoreExecutor와 같은 기록 형식(`section` 값만 추가) |
| UI | Streamlit `app.py`: 계획 목록 → 항목별 승인 체크 → dry run 결과 표 → 실행 버튼. `load_plan`·`review_plan`(ask 교체)·Executor 재사용 |
| 닫힌 루프 | 다음 Analyst 실행에 최근 감사 로그를 컨텍스트로 제공해 조치 효과를 점검 |

---

## 12. 알려진 제한 (Phase 2A)

- 단일 variant 상품만 지원. 옵션이 있는 상품은 계획에 넣을 수 없다.
- 이미지·파일 업로드 없음.
- 기존 컬렉션·페이지는 갱신하지 않는다 (`exists`로 남김).
- `productSet`은 컬렉션 연결을 전체 교체한다. 스토어에서 수동으로 추가한 연결은 재적용 시 사라진다.
- 메뉴는 1단계 항목만 지원 (하위 메뉴 없음).
- 실 스토어 대상 검증 전 (UAT TC-10~TC-15 미실행).
