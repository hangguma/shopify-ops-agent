"""
Executor - applies an approved StorePlan to the store. Code, not an LLM agent.

Why this design?
- Writes are the expensive mistake, so nothing here is decided by a model:
  the plan file a person approved is translated field by field into API
  input.
- The plan is checked again here (plan_problems), whatever the caller did:
  a plan with a needs_review flag, unsafe HTML, a claim word or a missing
  store tag is refused as a whole, before any write.
- A new product is created as a DRAFT and the collection is unpublished, so
  a wrong plan is never visible to shoppers.
- Running apply again only touches what this setup made. A product it
  created is updated and keeps its current status (a published product
  stays published); its options, variants and price are set back to the
  plan, so edits made to those in the Shopify admin are lost. A product or
  collection with the same handle that this setup did not make is left
  alone and the run stops (StoreConflict).
- Each step is appended to an audit log (JSON lines) as it happens, so a
  crash still leaves a record of what was written. The run stops at the
  first failure and marks the remaining steps as skipped.
"""

import json
import os
from datetime import datetime, timezone

from config import settings
from models.schemas import PlannedProduct, StorePlan
from store_plan import plan_problems
from tools.shopify_write import ensure_collection, upsert_product


class PlanNotApprovable(ValueError):
    """The plan cannot be applied as it stands. `problems` lists why."""

    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("Plan cannot be applied:\n" + "\n".join(f"  - {p}" for p in problems))


def product_input(p: PlannedProduct) -> dict:
    """ProductSetInput for a single-variant product (Shopify's default option).

    No `status` here: tools/shopify_write.py sets DRAFT on create and leaves
    it unchanged on update. The price is sent as the same two-decimal string
    the plan shows.
    """
    return {
        "handle": p.handle,
        "title": p.title,
        "descriptionHtml": p.description_html,
        "vendor": p.vendor,
        "productType": p.product_type,
        "tags": p.tags,
        "productOptions": [{"name": "Title", "values": [{"name": "Default Title"}]}],
        "variants": [
            {
                "optionValues": [{"optionName": "Title", "name": "Default Title"}],
                "price": f"{p.price:.2f}",
            }
        ],
    }


def audit_path_for(plan: StorePlan) -> str:
    return os.path.join(settings.OUTPUT_DIR, f"setup_audit_{plan.date}.jsonl")


def _record(path: str, entries: list[dict], **entry) -> None:
    entry = {
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "store": settings.SHOPIFY_STORE_DOMAIN,
        **entry,
    }
    entries.append(entry)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def apply_plan(plan: StorePlan, audit_path: str | None = None) -> list[dict]:
    """Write the plan to the store. Returns the audit entries for this run."""
    problems = plan_problems(plan)
    if problems:
        raise PlanNotApprovable(problems)

    path = audit_path or audit_path_for(plan)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    entries: list[dict] = []
    failed = False

    steps = [("product", p.handle, p) for p in plan.products]
    steps.append(("collection", plan.collection.handle, plan.collection))

    for kind, handle, item in steps:
        if failed:
            _record(path, entries, action=kind, handle=handle, status="skipped")
            continue
        try:
            if kind == "product":
                result, created = upsert_product(product_input(item), plan.store_tag)
                _record(path, entries, action="product", handle=handle, status="ok", id=result["id"],
                        detail=f"{'created' if created else 'updated'}, status {result['status']}")
            else:
                result, created = ensure_collection(
                    item.handle, item.title, item.description_html, item.tag
                )
                _record(path, entries, action="collection", handle=handle, status="ok", id=result["id"],
                        detail="created" if created else "already existed, left as is")
        except Exception as e:  # noqa: BLE001 - recorded, then the run stops
            failed = True
            _record(path, entries, action=kind, handle=handle, status="error", detail=str(e))

    return entries
