"""
Store setup entry point - plan first, apply only after a person approves.

Usage:
    python setup_store.py plan                       # brief -> plan files, no writes
    python setup_store.py plan --brief my_brief.yaml
    python setup_store.py apply outputs/store_plan_<date>.json

`plan` calls the model and never touches the store. `apply` never calls the
model: it shows the plan file, asks for confirmation at the keyboard, then
writes drafts to the store. There is deliberately no flag to skip the
confirmation.
"""

import argparse
import sys

from config import settings
from config.logging_setup import setup_logging
from executor import PlanNotApprovable, apply_plan, audit_path_for
from store_plan import load_brief, load_plan, render_markdown, save_plan


def cmd_plan(brief_path: str) -> int:
    log = setup_logging(to_file=True)
    settings.validate()

    brief = load_brief(brief_path)
    log.info("Planning store setup from %s (%d products)", brief_path, len(brief.products))

    from crew import run_planning  # imported here so `apply` never loads the LLM stack

    try:
        plan = run_planning(brief)
    except Exception as e:  # noqa: BLE001
        log.error("Planning failed: %s", e)
        log.error("  - Check ANTHROPIC_API_KEY validity.")
        return 1

    json_path, md_path = save_plan(plan)
    print("\n" + "=" * 50)
    print(render_markdown(plan))
    print("=" * 50)

    for p in plan.products:
        if p.needs_review:
            log.warning("Needs review: %s - %s", p.key, p.review_reason)
    log.info("Plan saved -> %s (read: %s)", json_path, md_path)
    log.info("Nothing was written to the store. To apply: python setup_store.py apply %s", json_path)
    return 0


def cmd_apply(plan_path: str) -> int:
    log = setup_logging(to_file=True)
    settings.validate()

    plan = load_plan(plan_path)
    print("\n" + "=" * 50)
    print(render_markdown(plan))
    print("=" * 50)

    store = settings.SHOPIFY_STORE_DOMAIN
    print(f"\nThis will create/update {len(plan.products)} draft products and 1 collection on {store}.")
    if input("Type 'apply' to continue: ").strip().lower() != "apply":
        log.info("Cancelled. Nothing was written.")
        return 1

    try:
        entries = apply_plan(plan)
    except PlanNotApprovable as e:
        log.error("%s", e)
        return 1

    for e in entries:
        line = f"{e['status']:>7}  {e['action']:<10} {e['handle']}  {e.get('detail', '')}"
        (log.error if e["status"] == "error" else log.info)(line)
    log.info("Audit log -> %s", audit_path_for(plan))

    if any(e["status"] != "ok" for e in entries):
        log.error("Setup did not finish. Fix the error above and run apply again (re-running is safe).")
        return 1
    log.info("Setup complete. Products are drafts; review them in the Shopify admin.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Plan and apply a Shopify store setup.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_plan = sub.add_parser("plan", help="Generate a plan from the brief (no writes)")
    p_plan.add_argument("--brief", default="store_brief.yaml")

    p_apply = sub.add_parser("apply", help="Apply an approved plan file to the store")
    p_apply.add_argument("plan_file")

    args = parser.parse_args(argv)
    if args.command == "plan":
        return cmd_plan(args.brief)
    return cmd_apply(args.plan_file)


if __name__ == "__main__":
    sys.exit(main())
