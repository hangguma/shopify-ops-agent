"""
Unit tests for the StoreExecutor (Phase 2A).

The Shopify write layer is replaced by a fake that records calls, so these
tests check the Executor's own guarantees: no writes in dry run, only approved
items applied, dependency order, idempotency, and a complete audit log.
"""

import json

import pytest

from executor.store_executor import PlanError, StoreExecutor

WRITES = {"create_collection", "upsert_product", "create_page", "update_menu", "publish"}


class FakeApi:
    """Stands in for tools.shopify_write_tool; `existing` maps handle -> GID."""

    def __init__(self, existing_collections=None, existing_pages=None, fail_on=None):
        self.calls: list[tuple] = []
        self.existing_collections = dict(existing_collections or {})
        self.existing_pages = dict(existing_pages or {})
        self.fail_on = fail_on or set()

    def _call(self, name, *args):
        self.calls.append((name, *args))
        if name in self.fail_on:
            raise RuntimeError(f"{name} boom")

    # reads
    def find_collection_by_handle(self, handle):
        self._call("find_collection_by_handle", handle)
        return self.existing_collections.get(handle)

    def find_page_by_handle(self, handle):
        self._call("find_page_by_handle", handle)
        return self.existing_pages.get(handle)

    def find_menu_by_handle(self, handle):
        self._call("find_menu_by_handle", handle)
        return "gid://shopify/Menu/1"

    def get_online_store_publication_id(self):
        self._call("get_online_store_publication_id")
        return "gid://shopify/Publication/1"

    # writes
    def create_collection(self, draft):
        self._call("create_collection", draft.handle)
        return f"gid://shopify/Collection/{draft.handle}"

    def upsert_product(self, draft, collection_ids):
        self._call("upsert_product", draft.handle, tuple(collection_ids))
        return f"gid://shopify/Product/{draft.handle}"

    def create_page(self, draft):
        self._call("create_page", draft.handle)
        return f"gid://shopify/Page/{draft.handle}"

    def update_menu(self, menu_id, title, items):
        self._call("update_menu", menu_id, title, json.dumps(items))
        return menu_id

    def publish(self, resource_id, publication_id):
        self._call("publish", resource_id)

    def names(self):
        return [c[0] for c in self.calls]


@pytest.fixture
def audit_path(tmp_path):
    return str(tmp_path / "audit.jsonl")


def _run(plan, api, audit_path, dry_run):
    ex = StoreExecutor(plan, dry_run=dry_run, audit_path=audit_path, api=api)
    return ex, ex.run()


def test_dry_run_makes_no_writes(plan, audit_path):
    api = FakeApi()
    _, records = _run(plan, api, audit_path, dry_run=True)

    assert not WRITES & set(api.names())
    assert {r.outcome for r in records} <= {"would_create", "would_upsert", "would_update", "exists"}
    assert all(r.dry_run for r in records)


def test_execute_applies_in_dependency_order(plan, audit_path):
    api = FakeApi()
    _run(plan, api, audit_path, dry_run=False)

    writes = [n for n in api.names() if n in WRITES - {"publish"}]
    first = {name: writes.index(name) for name in reversed(writes)}
    assert first["create_collection"] < first["upsert_product"] < first["create_page"] < first["update_menu"]


def test_products_receive_ids_of_collections_created_this_run(plan, audit_path):
    api = FakeApi()
    _run(plan, api, audit_path, dry_run=False)

    upserts = [c for c in api.calls if c[0] == "upsert_product"]
    assert ("upsert_product", "hydration-10-pack", ("gid://shopify/Collection/sheet-masks",)) in upserts


def test_collections_and_products_are_published(plan, audit_path):
    api = FakeApi()
    _run(plan, api, audit_path, dry_run=False)

    published = {c[1] for c in api.calls if c[0] == "publish"}
    assert "gid://shopify/Collection/sheet-masks" in published
    assert "gid://shopify/Product/sleeping-mask" in published


def test_unapproved_items_are_skipped(unapproved_plan, audit_path):
    api = FakeApi()
    _, records = _run(unapproved_plan, api, audit_path, dry_run=False)

    assert not WRITES & set(api.names())
    assert {r.outcome for r in records} == {"skipped"}


def test_existing_handles_are_not_recreated(plan, audit_path):
    api = FakeApi(
        existing_collections={"sheet-masks": "gid://shopify/Collection/old"},
        existing_pages={"about": "gid://shopify/Page/old"},
    )
    _, records = _run(plan, api, audit_path, dry_run=False)

    created = [c[1] for c in api.calls if c[0] in ("create_collection", "create_page")]
    assert "sheet-masks" not in created and "about" not in created
    # the product still links to the pre-existing collection
    assert ("upsert_product", "hydration-10-pack", ("gid://shopify/Collection/old",)) in api.calls
    assert sum(r.outcome == "exists" for r in records) == 2


def test_menu_items_resolve_to_resource_ids(plan, audit_path):
    api = FakeApi()
    _run(plan, api, audit_path, dry_run=False)

    _, menu_id, title, items_json = next(c for c in api.calls if c[0] == "update_menu")
    items = json.loads(items_json)
    assert menu_id == "gid://shopify/Menu/1"
    assert items[0] == {"title": "Home", "type": "FRONTPAGE", "url": "/"}
    assert items[1]["resourceId"] == "gid://shopify/Collection/sheet-masks"
    assert items[3]["resourceId"] == "gid://shopify/Page/about"


def test_one_failure_does_not_stop_the_run(plan, audit_path):
    api = FakeApi(fail_on={"create_collection"})
    _, records = _run(plan, api, audit_path, dry_run=False)

    assert any(r.section == "collection" and r.outcome == "error" for r in records)
    assert "create_page" in api.names()  # later sections still ran


def test_plan_with_problems_is_refused_before_any_call(plan, audit_path):
    plan.products[0].collection_handles.append("ghost")
    api = FakeApi()
    with pytest.raises(PlanError, match="unknown collection 'ghost'"):
        StoreExecutor(plan, dry_run=False, audit_path=audit_path, api=api).run()
    assert api.calls == []


def test_every_action_is_written_to_the_audit_log(plan, audit_path):
    api = FakeApi()
    _, records = _run(plan, api, audit_path, dry_run=False)

    with open(audit_path, encoding="utf-8") as f:
        lines = [json.loads(line) for line in f]
    assert len(lines) == len(records)
    assert lines[0].keys() >= {"section", "handle", "outcome", "dry_run", "timestamp"}
    assert lines[0]["dry_run"] is False


def test_summary_counts_outcomes(plan, audit_path):
    ex, _ = _run(plan, FakeApi(), audit_path, dry_run=True)
    summary = ex.summary()
    assert summary["would_upsert"] == 2
    assert summary["would_create"] == 4  # 2 collections + 2 pages


def test_dry_run_reports_menu_links_not_yet_in_store(plan, audit_path):
    _, records = _run(plan, FakeApi(), audit_path, dry_run=True)
    menu = next(r for r in records if r.section == "menu")
    assert menu.outcome == "would_update"
    assert "not in store yet: Sheet Masks, About" in menu.detail
