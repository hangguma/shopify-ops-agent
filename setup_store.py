"""
Phase 2A entry point - set up the Online Store from a brand brief.

    python setup_store.py plan   --brief briefs/example_brief.md
    python setup_store.py review outputs/plans/store_plan_<date>.json [--approve-all]
    python setup_store.py apply  outputs/plans/store_plan_<date>.json            # dry run
    python setup_store.py apply  outputs/plans/store_plan_<date>.json --execute  # real writes

Why three steps instead of one command?
- plan:   the Builder (LLM) proposes. The plan is a JSON file you can read,
          diff and hand-edit before anything touches the store.
- review: a human approves item by item. Only approved items are applied.
- apply:  the Executor (plain code) applies approved items. Dry run by
          default; --execute is required for real writes.

The approval gate is a CLI for now. The planned Streamlit UI (Phase 2) will
call the same load_plan / review / StoreExecutor functions.
"""

import argparse
import json
import os
import sys
from collections.abc import Callable
from datetime import date

from config import settings
from config.logging_setup import setup_logging
from executor.store_executor import ActionRecord, PlanError, StoreExecutor
from models.store_plan import StorePlan


# -- plan file I/O -------------------------------------------------------

def save_plan(plan: StorePlan, path: str | None = None) -> str:
    os.makedirs(settings.STORE_PLAN_DIR, exist_ok=True)
    path = path or os.path.join(settings.STORE_PLAN_DIR, f"store_plan_{date.today().isoformat()}.json")
    with open(path, "w", encoding="utf-8") as f:
        f.write(plan.model_dump_json(indent=2))
    return path


def load_plan(path: str) -> StorePlan:
    with open(path, encoding="utf-8") as f:
        return StorePlan.model_validate(json.load(f))


# -- review --------------------------------------------------------------

def review_plan(
    plan: StorePlan,
    approve_all: bool = False,
    ask: Callable[[str], str] = input,
) -> StorePlan:
    """Set `approved` on each item. `ask` is injectable for tests."""

    def decide(label: str) -> bool:
        if approve_all:
            return True
        answer = ask(f"Approve {label}? [y/N] ").strip().lower()
        return answer in ("y", "yes")

    for c in plan.collections:
        c.approved = decide(f"collection '{c.handle}' ({c.title})")
    for p in plan.products:
        p.approved = decide(f"product '{p.handle}' ({p.title}, ${p.price})")
    for pg in plan.pages:
        pg.approved = decide(f"page '{pg.handle}' ({pg.title})")
    if plan.menu:
        titles = ", ".join(i.title for i in plan.menu.items)
        plan.menu.approved = decide(f"menu '{plan.menu.handle}' -> [{titles}]")
    return plan


def describe_plan(plan: StorePlan) -> str:
    lines = [f"Store plan: {plan.brand_name}", f"  {plan.brief_summary}"]
    for section, (approved, total) in plan.approval_counts().items():
        lines.append(f"  {section:<12} {approved}/{total} approved")
    problems = plan.reference_problems()
    if problems:
        lines.append("  Problems (fix before apply):")
        lines.extend(f"    - {p}" for p in problems)
    return "\n".join(lines)


def format_records(records: list[ActionRecord]) -> str:
    return "\n".join(
        f"  [{r.outcome:<12}] {r.section:<10} {r.handle:<28} {r.detail}" for r in records
    )


# -- commands ------------------------------------------------------------

def cmd_plan(args: argparse.Namespace) -> int:
    from crew_setup import build_setup_crew  # imported lazily: pulls in CrewAI

    with open(args.brief, encoding="utf-8") as f:
        brief = f.read()

    result = build_setup_crew(brief).kickoff()
    plan = getattr(result, "pydantic", None)
    if not isinstance(plan, StorePlan):
        print("Builder did not return a valid StorePlan:\n" + str(result), file=sys.stderr)
        return 1

    # The Builder is told to leave approvals false; enforce it regardless.
    review_plan(plan, ask=lambda _: "n")
    path = save_plan(plan, args.out)
    print(describe_plan(plan))
    print(f"\nSaved -> {path}\nNext: python setup_store.py review {path}")
    return 0


def cmd_review(args: argparse.Namespace) -> int:
    plan = review_plan(load_plan(args.plan), approve_all=args.approve_all)
    save_plan(plan, args.plan)
    print(describe_plan(plan))
    print(f"\nNext (dry run): python setup_store.py apply {args.plan}")
    return 0


def cmd_apply(args: argparse.Namespace) -> int:
    plan = load_plan(args.plan)
    executor = StoreExecutor(plan, dry_run=not args.execute)
    mode = "EXECUTE" if args.execute else "DRY RUN (no writes)"
    print(f"Applying {args.plan} - {mode}")
    try:
        records = executor.run()
    except PlanError as e:
        print(str(e), file=sys.stderr)
        return 1

    print(format_records(records))
    print(f"\nSummary: {executor.summary()}")
    print(f"Audit log -> {executor.audit_path}")
    if not args.execute:
        print(f"\nLooks right? Run again with --execute:\n  python setup_store.py apply {args.plan} --execute")
    return 1 if any(r.outcome == "error" for r in records) else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Set up a Shopify Online Store from a brand brief.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_plan = sub.add_parser("plan", help="Builder agent proposes a StorePlan from a brief")
    p_plan.add_argument("--brief", required=True, help="Path to a brand brief (markdown/text)")
    p_plan.add_argument("--out", help="Where to save the plan JSON")
    p_plan.set_defaults(func=cmd_plan)

    p_review = sub.add_parser("review", help="Approve plan items one by one")
    p_review.add_argument("plan", help="Path to a plan JSON")
    p_review.add_argument("--approve-all", action="store_true", help="Approve every item")
    p_review.set_defaults(func=cmd_review)

    p_apply = sub.add_parser("apply", help="Apply approved items (dry run unless --execute)")
    p_apply.add_argument("plan", help="Path to a plan JSON")
    p_apply.add_argument("--execute", action="store_true", help="Perform real writes")
    p_apply.set_defaults(func=cmd_apply)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    setup_logging(to_file=True)
    if args.command != "review":
        settings.validate()  # review is offline; plan and apply need keys
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
