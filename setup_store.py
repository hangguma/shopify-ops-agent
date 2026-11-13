"""
Store setup entry point - plan first, apply only after a person approves.

Usage:
    python setup_store.py plan                       # brief -> plan files, no writes
    python setup_store.py plan --brief my_brief.yaml
    python setup_store.py plan --overwrite           # replace today's plan file
    python setup_store.py apply outputs/store_plan_<date>.json

`plan` calls the model and never touches the store. `apply` never calls the
model and does not need ANTHROPIC_API_KEY: it checks the plan file, shows
it, asks for confirmation at the keyboard, then writes to the store. There
is deliberately no flag to skip the confirmation.
"""

import argparse
import json
import os
import sys
from datetime import date

import yaml
from pydantic import ValidationError

from config import settings
from config.logging_setup import setup_logging
from executor import apply_plan, audit_path_for
from store_plan import load_brief, load_plan, plan_paths, plan_problems, render_markdown, save_plan

_AUTH_HINTS = ("authentication", "unauthorized", "invalid x-api-key", "invalid api key", "401")


def cmd_plan(brief_path: str, overwrite: bool) -> int:
    log = setup_logging(to_file=True)
    settings.validate()

    try:
        brief = load_brief(brief_path)
    except FileNotFoundError:
        log.error("Brief not found: %s", brief_path)
        return 1
    except (ValidationError, yaml.YAMLError) as e:
        log.error("Brief %s is not valid:\n%s", brief_path, e)
        return 1

    # Checked before the model is called, so a refusal costs nothing.
    today = date.today().isoformat()
    json_path, _ = plan_paths(today)
    if os.path.exists(json_path) and not overwrite:
        log.error("%s already exists and may contain your edits.", json_path)
        log.error("Apply it, or run `plan --overwrite` to replace it with fresh copy.")
        return 1

    log.info("Planning store setup from %s (%d products)", brief_path, len(brief.products))

    # Imported here: only `plan` needs crewai. `apply` runs without it.
    from crew import run_planning

    try:
        plan = run_planning(brief, today=today)
    except Exception as e:  # noqa: BLE001
        log.error("Planning failed: %s", e)
        if any(hint in str(e).lower() for hint in _AUTH_HINTS):
            log.error("  - Check ANTHROPIC_API_KEY validity.")
        return 1

    json_path, md_path = save_plan(plan, overwrite=overwrite)
    print("\n" + "=" * 50)
    print(render_markdown(plan))
    print("=" * 50)

    for problem in plan_problems(plan):
        log.warning("Needs fixing before apply: %s", problem)
    log.info("Plan saved -> %s (read: %s)", json_path, md_path)
    log.info("Nothing was written to the store. To apply: python setup_store.py apply %s", json_path)
    return 0


def cmd_apply(plan_path: str) -> int:
    log = setup_logging(to_file=True)
    settings.validate(llm=False)

    try:
        plan = load_plan(plan_path)
    except FileNotFoundError:
        log.error("Plan file not found: %s", plan_path)
        return 1
    except (ValidationError, json.JSONDecodeError) as e:
        log.error("Plan file %s is not valid:\n%s", plan_path, e)
        return 1

    print("\n" + "=" * 50)
    print(render_markdown(plan))
    print("=" * 50)

    # Before the prompt: nobody should approve a plan that will be refused.
    problems = plan_problems(plan)
    if problems:
        log.error("This plan cannot be applied yet:")
        for problem in problems:
            log.error("  - %s", problem)
        log.error("Fix these in %s, then run apply again. Nothing was written.", plan_path)
        return 1

    store = settings.SHOPIFY_STORE_DOMAIN
    print(f"\nThis will create or update {len(plan.products)} products and 1 collection on {store}.")
    print("New products are created as drafts. Products this setup made before are updated")
    print("and keep their current status; their variants and price are set back to this plan.")
    if input("Type 'apply' to continue: ").strip().lower() != "apply":
        log.info("Cancelled. Nothing was written.")
        return 1

    entries = apply_plan(plan)

    for e in entries:
        line = f"{e['status']:>7}  {e['action']:<10} {e['handle']}  {e.get('detail', '')}"
        (log.error if e["status"] == "error" else log.info)(line)
    log.info("Audit log -> %s", audit_path_for(plan))

    if any(e["status"] != "ok" for e in entries):
        log.error("Setup did not finish. Steps marked ok were written; fix the error above and run apply again.")
        return 1
    log.info("Setup complete. Review the products in the Shopify admin.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Plan and apply a Shopify store setup.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_plan = sub.add_parser("plan", help="Generate a plan from the brief (no writes)")
    p_plan.add_argument("--brief", default="store_brief.yaml")
    p_plan.add_argument("--overwrite", action="store_true",
                        help="Replace today's plan file if it already exists")

    p_apply = sub.add_parser("apply", help="Apply an approved plan file to the store")
    p_apply.add_argument("plan_file")

    args = parser.parse_args(argv)
    if args.command == "plan":
        return cmd_plan(args.brief, args.overwrite)
    return cmd_apply(args.plan_file)


if __name__ == "__main__":
    sys.exit(main())
