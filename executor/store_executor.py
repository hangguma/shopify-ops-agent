"""
Store Executor - applies the approved parts of a StorePlan to Shopify.

Why this design?
- Deterministic code, not an agent. The LLM's job ended when it produced the
  plan; a human approved items; this module only carries out those approvals.
  Same input, same writes, every time.
- Dry run is the default. A dry run still performs READS (to tell you which
  handles already exist) but never a write, so you can preview a run against
  the real store safely.
- Dependency order: collections -> products (need collection IDs) -> pages ->
  menu (needs collection and page IDs).
- Idempotent: collections and pages are looked up by handle before creating;
  products go through productSet's handle upsert. Re-running a plan does not
  create duplicates.
- One failed item does not abort the run. Each action, success or failure,
  is returned and appended to a JSONL audit log, so a partial run is visible
  and can be re-run after a fix.
"""

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from types import ModuleType

from config import settings
from models.store_plan import FIXED_URLS, StorePlan
from tools import shopify_write_tool


class PlanError(ValueError):
    """The plan is internally inconsistent and must be fixed before applying."""


@dataclass
class ActionRecord:
    section: str          # collection | product | page | menu | publish
    handle: str
    outcome: str          # created | exists | upserted | updated | published | would_* | skipped | error
    detail: str = ""
    resource_id: str = ""
    dry_run: bool = True
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )


class StoreExecutor:
    def __init__(
        self,
        plan: StorePlan,
        dry_run: bool = True,
        audit_path: str | None = None,
        api: ModuleType = shopify_write_tool,
    ):
        self.plan = plan
        self.dry_run = dry_run
        self.audit_path = audit_path or settings.AUDIT_LOG_PATH
        self.api = api  # injectable so tests never touch the network
        self.records: list[ActionRecord] = []
        self._collection_ids: dict[str, str] = {}
        self._page_ids: dict[str, str] = {}
        self._publication_id: str | None = None

    # -- public ------------------------------------------------------------

    def run(self) -> list[ActionRecord]:
        problems = self.plan.reference_problems()
        if problems:
            raise PlanError("Plan has problems; fix them before applying:\n- " + "\n- ".join(problems))

        self._apply_collections()
        self._apply_products()
        self._apply_pages()
        self._apply_menu()
        return self.records

    def summary(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for r in self.records:
            counts[r.outcome] = counts.get(r.outcome, 0) + 1
        return counts

    # -- helpers -----------------------------------------------------------

    def _record(self, section: str, handle: str, outcome: str, detail: str = "", resource_id: str = "") -> None:
        record = ActionRecord(
            section=section, handle=handle, outcome=outcome, detail=detail,
            resource_id=resource_id, dry_run=self.dry_run,
        )
        self.records.append(record)
        os.makedirs(os.path.dirname(self.audit_path) or ".", exist_ok=True)
        with open(self.audit_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")

    def _publication(self) -> str:
        if self._publication_id is None:
            self._publication_id = self.api.get_online_store_publication_id()
        return self._publication_id

    def _publish(self, section: str, handle: str, resource_id: str) -> None:
        try:
            self.api.publish(resource_id, self._publication())
            self._record("publish", handle, "published", f"{section} -> Online Store", resource_id)
        except Exception as e:  # noqa: BLE001 - recorded, run continues
            self._record("publish", handle, "error", f"{section}: {e}", resource_id)

    def _resolve_collection(self, handle: str) -> str | None:
        """ID for a collection: created/found this run, else an existing one in the store."""
        if handle not in self._collection_ids:
            existing = self.api.find_collection_by_handle(handle)
            if existing:
                self._collection_ids[handle] = existing
        return self._collection_ids.get(handle)

    def _resolve_page(self, handle: str) -> str | None:
        if handle not in self._page_ids:
            existing = self.api.find_page_by_handle(handle)
            if existing:
                self._page_ids[handle] = existing
        return self._page_ids.get(handle)

    # -- sections ----------------------------------------------------------

    def _apply_collections(self) -> None:
        for draft in self.plan.collections:
            if not draft.approved:
                self._record("collection", draft.handle, "skipped", "not approved")
                continue
            try:
                existing = self.api.find_collection_by_handle(draft.handle)
                if existing:
                    self._collection_ids[draft.handle] = existing
                    self._record("collection", draft.handle, "exists", "left unchanged", existing)
                    continue
                if self.dry_run:
                    self._record("collection", draft.handle, "would_create", draft.title)
                    continue
                new_id = self.api.create_collection(draft)
                self._collection_ids[draft.handle] = new_id
                self._record("collection", draft.handle, "created", draft.title, new_id)
                self._publish("collection", draft.handle, new_id)
            except Exception as e:  # noqa: BLE001
                self._record("collection", draft.handle, "error", str(e))

    def _apply_products(self) -> None:
        for draft in self.plan.products:
            if not draft.approved:
                self._record("product", draft.handle, "skipped", "not approved")
                continue
            try:
                collection_ids: list[str] = []
                missing: list[str] = []
                for h in draft.collection_handles:
                    cid = self._resolve_collection(h)
                    if cid:
                        collection_ids.append(cid)
                    else:
                        missing.append(h)

                note = f"price {draft.price}"
                if missing:
                    note += f"; collections not in store yet: {', '.join(missing)}"

                if self.dry_run:
                    self._record("product", draft.handle, "would_upsert", note)
                    continue
                product_id = self.api.upsert_product(draft, collection_ids)
                self._record("product", draft.handle, "upserted", note, product_id)
                self._publish("product", draft.handle, product_id)
            except Exception as e:  # noqa: BLE001
                self._record("product", draft.handle, "error", str(e))

    def _apply_pages(self) -> None:
        for draft in self.plan.pages:
            if not draft.approved:
                self._record("page", draft.handle, "skipped", "not approved")
                continue
            try:
                existing = self.api.find_page_by_handle(draft.handle)
                if existing:
                    self._page_ids[draft.handle] = existing
                    self._record("page", draft.handle, "exists", "left unchanged", existing)
                    continue
                if self.dry_run:
                    self._record("page", draft.handle, "would_create", draft.title)
                    continue
                new_id = self.api.create_page(draft)
                self._page_ids[draft.handle] = new_id
                self._record("page", draft.handle, "created", draft.title, new_id)
            except Exception as e:  # noqa: BLE001
                self._record("page", draft.handle, "error", str(e))

    def _apply_menu(self) -> None:
        menu = self.plan.menu
        if menu is None:
            return
        if not menu.approved:
            self._record("menu", menu.handle, "skipped", "not approved")
            return
        try:
            menu_id = self.api.find_menu_by_handle(menu.handle)
            if not menu_id:
                self._record("menu", menu.handle, "error", "menu not found in store")
                return

            items: list[dict] = []
            unresolved: list[str] = []
            for item in menu.items:
                if item.type in FIXED_URLS:
                    items.append({"title": item.title, "type": item.type, "url": FIXED_URLS[item.type]})
                    continue
                resolver = self._resolve_collection if item.type == "COLLECTION" else self._resolve_page
                resource_id = resolver(item.target_handle)
                if resource_id:
                    items.append({"title": item.title, "type": item.type, "resourceId": resource_id})
                else:
                    unresolved.append(item.title)

            note = f"{len(items)} items"
            if unresolved:
                note += f"; not in store yet: {', '.join(unresolved)}"

            if self.dry_run:
                self._record("menu", menu.handle, "would_update", note, menu_id)
                return
            self.api.update_menu(menu_id, menu.title, items)
            self._record("menu", menu.handle, "updated", note, menu_id)
        except Exception as e:  # noqa: BLE001
            self._record("menu", menu.handle, "error", str(e))
