"""
Executor - applies an approved StorePlan to the store. Code, not an LLM agent.

Why this design?
- Writes are the expensive mistake, so nothing here is decided by a model:
  the plan file a person approved is translated field by field into API
  input.
- Every product is created as a DRAFT and the collection is unpublished, so
  a wrong plan is never visible to shoppers.
- Re-running is safe: products are upserted by handle and an existing
  collection is left alone.
- A plan with any needs_review product is refused as a whole. Fix or remove
  the product in the plan JSON, then apply again.
- Each step is appended to an audit log (JSON lines) as it happens, so a
  crash still leaves a record of what was written. The run stops at the
  first failure and marks the remaining steps as skipped.
"""

import json
import os
from datetime import datetime, timezone

from config import settings
from models.schemas import PlannedProduct, StorePlan
from tools.shopify_tool import ensure_collection, upsert_product


class PlanNotApprovable(ValueError):
    """The plan still has products marked needs_review."""


def product_input(p: PlannedProduct) -> dict:
    """ProductSetInput for a single-variant product (Shopify's default option)."""
    return {
        "handle": p.handle,
        "title": p.title,
        "descriptionHtml": p.description_html,
        "vendor": p.vendor,
        "productType": p.product_type,
        "tags": p.tags,
        "status": "DRAFT",
        "productOptions": [{"name": "Title", "values": [{"name": "Default Title"}]}],
        "variants": [
            {
                "optionValues": [{"optionName": "Title", "name": "Default Title"}],
                "price": float(p.price),
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
    blocked = [p.key for p in plan.products if p.needs_review]
    if blocked:
        raise PlanNotApprovable(
            f"Plan has products marked needs_review: {', '.join(blocked)}. "
            f"Fix them in the plan file, set needs_review to false, then apply again."
        )

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
                result = upsert_product(product_input(item))
                _record(path, entries, action="product", handle=handle, status="ok",
                        id=result["id"], detail=f"status {result['status']}")
            else:
                result, created = ensure_collection(
                    item.handle, item.title, item.description_html, item.tag
                )
                _record(path, entries, action="collection", handle=handle, status="ok",
                        id=result["id"], detail="created" if created else "already existed")
        except Exception as e:  # noqa: BLE001 - recorded, then the run stops
            failed = True
            _record(path, entries, action=kind, handle=handle, status="error", detail=str(e))

    return entries
