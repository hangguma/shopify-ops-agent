"""
Store plan - turns the seller's brief plus the LLM's copy into a StorePlan.

Why this design?
- Same split as enrich.py: the LLM writes copy, code supplies facts. Title,
  price, vendor and handle come from store_brief.yaml and are never copied by
  the model, so a hallucinated price cannot reach the store.
- Everything the model wrote is checked before a person sees it: HTML is
  reduced to a small allowlist, tags are normalised, and copy that makes a
  medical-sounding claim is kept but marked needs_review. A marked product
  blocks apply (executor.py) until a person edits the plan file.
- The plan is a JSON file on disk. That file - not the model's output - is
  what gets approved and applied, so what a person read is what is written.
"""

import json
import os
import re
from datetime import date
from html import escape
from html.parser import HTMLParser

import yaml

from config import settings
from models.schemas import (
    PlannedCollection,
    PlannedProduct,
    PlannerOutput,
    StoreBrief,
    StorePlan,
)

_ALLOWED_TAGS = {"p", "ul", "ol", "li", "strong", "em", "b", "i", "br", "h3", "h4"}
_DROP_WITH_CONTENT = {"script", "style", "iframe", "object", "embed"}
_MAX_TAGS = 8

# Cosmetics copy must not claim to treat or cure anything; a match does not
# delete the copy, it sends the product to a person.
_CLAIM_WORDS = re.compile(
    r"\b(cures?|cured|heals?|healing|fda|clinically proven|eczema|rosacea|psoriasis|prescription)\b",
    re.IGNORECASE,
)


class _Sanitizer(HTMLParser):
    """Keeps allowlisted tags without attributes; escapes all text."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in _DROP_WITH_CONTENT:
            self._skip_depth += 1
        elif not self._skip_depth and tag in _ALLOWED_TAGS:
            self.out.append(f"<{tag}>")

    def handle_endtag(self, tag):
        if tag in _DROP_WITH_CONTENT:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif not self._skip_depth and tag in _ALLOWED_TAGS and tag != "br":
            self.out.append(f"</{tag}>")

    def handle_data(self, data):
        if not self._skip_depth:
            self.out.append(escape(data, quote=False))


def sanitize_html(html: str) -> str:
    parser = _Sanitizer()
    parser.feed(html or "")
    parser.close()
    return "".join(parser.out).strip()


def _text_only(html: str) -> str:
    return re.sub(r"<[^>]+>", " ", html)


def normalize_tags(tags: list[str], always: list[str]) -> list[str]:
    """Lowercase, trim, drop empties/commas/duplicates; `always` tags come first."""
    seen: list[str] = []
    for tag in [*always, *tags]:
        clean = " ".join(str(tag).replace(",", " ").split()).lower()
        if clean and clean not in seen:
            seen.append(clean)
    return seen[: max(_MAX_TAGS, len(always))]


def load_brief(path: str) -> StoreBrief:
    with open(path, encoding="utf-8") as f:
        return StoreBrief.model_validate(yaml.safe_load(f))


def build_plan(brief: StoreBrief, copy: PlannerOutput, today: str | None = None) -> StorePlan:
    """Merge brief facts with LLM copy. Unknown keys from the LLM are ignored."""
    by_key = {}
    for c in copy.products:
        by_key.setdefault(c.key.strip(), c)  # first wins if the model repeats a key

    products = []
    for p in brief.products:
        c = by_key.get(p.key)
        description = sanitize_html(c.description_html) if c else ""
        always = [brief.store_tag] + (["bundle"] if p.bundle_of else [])

        reason = ""
        if c is None:
            reason = "the model returned no copy for this product"
        elif not _text_only(description).strip():
            reason = "description is empty after cleaning"
        elif (m := _CLAIM_WORDS.search(_text_only(description))):
            reason = f"description contains a medical-sounding claim: {m.group(0)!r}"

        products.append(PlannedProduct(
            key=p.key,
            handle=p.key,
            title=p.title,
            vendor=p.vendor,
            price=p.price,
            product_type=" ".join(c.product_type.split())[:60] if c else "",
            description_html=description,
            tags=normalize_tags(c.tags if c else [], always),
            needs_review=bool(reason),
            review_reason=reason,
        ))

    return StorePlan(
        date=today or date.today().isoformat(),
        store_tag=brief.store_tag,
        collection=PlannedCollection(
            handle=brief.collection_key,
            title=brief.collection_title,
            description_html=sanitize_html(copy.collection_description_html),
            tag=brief.store_tag,
        ),
        products=products,
    )


def render_markdown(plan: StorePlan) -> str:
    """The human-readable plan. Written by code so it always matches the JSON."""
    lines = [
        f"# Store setup plan ({plan.date})",
        "",
        f"Everything is created as a **draft** and tagged `{plan.store_tag}`.",
        "",
        f"## Collection: {plan.collection.title}",
        f"- handle: `{plan.collection.handle}`",
        f"- includes every product tagged `{plan.collection.tag}`",
        f"- description: {_text_only(plan.collection.description_html).strip()}",
        "",
        "## Products",
    ]
    for p in plan.products:
        flag = "  **NEEDS REVIEW**" if p.needs_review else ""
        lines += [
            "",
            f"### {p.title}{flag}",
            f"- handle: `{p.handle}`",
            f"- vendor: {p.vendor} | type: {p.product_type or '-'} | price: {p.price:.2f}",
            f"- tags: {', '.join(p.tags)}",
        ]
        if p.needs_review:
            lines.append(f"- review reason: {p.review_reason}")
        lines += ["", p.description_html or "_(no description)_"]
    return "\n".join(lines) + "\n"


def save_plan(plan: StorePlan) -> tuple[str, str]:
    """Write the plan as JSON (applied) and Markdown (read). Returns both paths."""
    os.makedirs(settings.OUTPUT_DIR, exist_ok=True)
    base = os.path.join(settings.OUTPUT_DIR, f"store_plan_{plan.date}")
    with open(f"{base}.json", "w", encoding="utf-8") as f:
        f.write(plan.model_dump_json(indent=2))
    with open(f"{base}.md", "w", encoding="utf-8") as f:
        f.write(render_markdown(plan))
    return f"{base}.json", f"{base}.md"


def load_plan(path: str) -> StorePlan:
    with open(path, encoding="utf-8") as f:
        return StorePlan.model_validate(json.load(f))
