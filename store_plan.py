"""
Store plan - turns the seller's brief plus the LLM's copy into a StorePlan.

Why this design?
- Same split as enrich.py: the LLM writes copy, code supplies facts. Title,
  price, vendor and handle come from store_brief.yaml and are never copied by
  the model, so a hallucinated price cannot reach the store.
- Everything the model wrote is checked before a person sees it: HTML is
  reduced to a small allowlist, tags are normalised, and copy that makes a
  medical-sounding claim is kept but marked needs_review.
- The plan is a JSON file on disk. That file - not the model's output - is
  what gets approved and applied, and a person may edit it. So the same
  checks run again on the file (plan_problems) before anything is written:
  an edit cannot bring back unsafe HTML, a claim word, a bad handle or a
  product that has lost the store tag.
- A claim word is cleared by rewording the copy, not by flipping
  needs_review: the check runs on the text itself every time.
"""

import json
import os
import re
from datetime import date
from html import escape, unescape
from html.parser import HTMLParser

import yaml

from config import settings
from models.schemas import (
    _SLUG,
    PlannedCollection,
    PlannedProduct,
    PlannerOutput,
    StoreBrief,
    StorePlan,
)

_ALLOWED_TAGS = {"p", "ul", "ol", "li", "strong", "em", "b", "i", "br", "h3", "h4"}
# Elements whose text content must go too. Only elements that have an end
# tag belong here: a void element (embed, img) never closes, so it would
# swallow the rest of the document. Void elements are simply not allowlisted.
_DROP_WITH_CONTENT = {"script", "style", "iframe", "object"}
_MAX_TAGS = 8

# Cosmetics copy must not claim to treat, cure or prevent anything. Applied
# to text with tags removed and whitespace collapsed.
_CLAIM_WORDS = re.compile(
    r"\b("
    r"cures?|cured|curing|heals?|healed|healing|"
    r"treats?|treated|treating|prevents?|prevented|preventing|"
    r"acne|eczema|rosacea|psoriasis|fda|prescription|"
    r"clinically[\s-]+proven"
    r")\b",
    re.IGNORECASE,
)


class _Sanitizer(HTMLParser):
    """Keeps allowlisted tags without attributes; escapes all text."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in _DROP_WITH_CONTENT:
            self.skip_depth += 1
        elif not self.skip_depth and tag in _ALLOWED_TAGS:
            self.out.append(f"<{tag}>")

    def handle_endtag(self, tag):
        if tag in _DROP_WITH_CONTENT:
            self.skip_depth = max(0, self.skip_depth - 1)
        elif not self.skip_depth and tag in _ALLOWED_TAGS and tag != "br":
            self.out.append(f"</{tag}>")

    def handle_data(self, data):
        if not self.skip_depth:
            self.out.append(escape(data, quote=False))


def clean_html(html: str) -> tuple[str, bool]:
    """Return (cleaned html, truncated).

    `truncated` is True when a script/style/iframe/object block was never
    closed, which means everything after it was dropped.
    """
    parser = _Sanitizer()
    parser.feed(html or "")
    parser.close()
    return "".join(parser.out).strip(), parser.skip_depth > 0


def sanitize_html(html: str) -> str:
    return clean_html(html)[0]


def _text_only(html: str) -> str:
    """Visible text: tags become spaces, entities are decoded, whitespace collapses."""
    return " ".join(unescape(re.sub(r"<[^>]+>", " ", html or "")).split())


def find_claim(*texts: str) -> str | None:
    """Return the first medical-sounding word found in any of the texts."""
    for text in texts:
        if (m := _CLAIM_WORDS.search(_text_only(text))):
            return m.group(0)
    return None


def normalize_tags(tags: list[str], always: list[str]) -> list[str]:
    """Lowercase, trim, drop empties/commas/duplicates; `always` tags come first."""
    seen: list[str] = []
    for tag in [*always, *tags]:
        clean = " ".join(str(tag).replace(",", " ").split()).lower()
        if clean and clean not in seen:
            seen.append(clean)
    return seen[: max(_MAX_TAGS, len(always))]


def _copy_problem(description_html: str, product_type: str, tags: list[str]) -> str:
    """Why this copy cannot be applied as it stands, or '' if it can."""
    cleaned, truncated = clean_html(description_html)
    if cleaned != (description_html or "").strip():
        return "description contains HTML that is not allowed (only simple tags, no attributes)"
    if truncated:
        return "description has an unclosed script/style block; the text after it was dropped"
    if not _text_only(cleaned):
        return "description is empty"
    for label, text in (("description", cleaned), ("product type", product_type), ("tags", " | ".join(tags))):
        if (word := find_claim(text)):
            return f"{label} contains a medical-sounding claim: {word!r}"
    return ""


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
        always = [brief.store_tag] + (["bundle"] if p.bundle_of else [])
        description, truncated = clean_html(c.description_html) if c else ("", False)
        product_type = " ".join(c.product_type.split())[:60] if c else ""
        tags = normalize_tags(c.tags if c else [], always)

        if c is None:
            reason = "the model returned no copy for this product"
        elif truncated:
            reason = "description had an unclosed script/style block; the text after it was dropped"
        else:
            reason = _copy_problem(description, product_type, [t for t in tags if t not in always])

        products.append(PlannedProduct(
            key=p.key,
            handle=p.key,
            title=p.title,
            vendor=p.vendor,
            price=p.price,
            product_type=product_type,
            description_html=description,
            tags=tags,
            needs_review=bool(reason),
            review_reason=reason,
        ))

    collection_description, truncated = clean_html(copy.collection_description_html)
    collection_reason = ""
    if truncated:
        collection_reason = "description had an unclosed script/style block; the text after it was dropped"
    elif (word := find_claim(collection_description)):
        collection_reason = f"description contains a medical-sounding claim: {word!r}"

    return StorePlan(
        date=today or date.today().isoformat(),
        store_tag=brief.store_tag,
        collection=PlannedCollection(
            handle=brief.collection_key,
            title=brief.collection_title,
            description_html=collection_description,
            tag=brief.store_tag,
            needs_review=bool(collection_reason),
            review_reason=collection_reason,
        ),
        products=products,
    )


def plan_problems(plan: StorePlan) -> list[str]:
    """Everything that stops this plan from being applied. Empty list = applicable.

    Runs on the plan as loaded from disk, so it also covers hand edits. A
    needs_review flag blocks on its own; the content checks block regardless
    of the flag.
    """
    problems: list[str] = []
    tag = plan.store_tag

    if not _SLUG.match(tag):
        problems.append(f"store_tag {tag!r} must be lowercase letters, digits and hyphens")

    c = plan.collection
    if c.needs_review:
        problems.append(f"collection: marked needs_review ({c.review_reason or 'no reason given'})")
    if not _SLUG.match(c.handle):
        problems.append(f"collection: handle {c.handle!r} must be lowercase letters, digits and hyphens")
    if c.tag != tag:
        problems.append(f"collection: tag {c.tag!r} must equal store_tag {tag!r}")
    cleaned, truncated = clean_html(c.description_html)
    if cleaned != (c.description_html or "").strip() or truncated:
        problems.append("collection: description contains HTML that is not allowed")
    elif (word := find_claim(cleaned)):
        problems.append(f"collection: description contains a medical-sounding claim: {word!r}")

    handles = [p.handle for p in plan.products]
    for dupe in sorted({h for h in handles if handles.count(h) > 1}):
        problems.append(f"{dupe}: handle is used by more than one product")

    for p in plan.products:
        if p.needs_review:
            problems.append(f"{p.key}: marked needs_review ({p.review_reason or 'no reason given'})")
        if not _SLUG.match(p.handle):
            problems.append(f"{p.key}: handle {p.handle!r} must be lowercase letters, digits and hyphens")
        if tag.casefold() not in {t.casefold() for t in p.tags}:
            problems.append(f"{p.key}: tags must include {tag!r}, or the product is left out of the collection")
        if not p.title.strip() or not p.vendor.strip():
            problems.append(f"{p.key}: title and vendor must not be empty")
        other_tags = [t for t in p.tags if t.casefold() != tag.casefold()]
        if (problem := _copy_problem(p.description_html, p.product_type, other_tags)):
            problems.append(f"{p.key}: {problem}")

    return problems


def render_markdown(plan: StorePlan) -> str:
    """The human-readable plan. Written by code so it always matches the JSON."""
    c = plan.collection
    lines = [
        f"# Store setup plan ({plan.date})",
        "",
        f"New products are created as **drafts** and tagged `{plan.store_tag}`.",
        "",
        f"## Collection: {c.title}{'  **NEEDS REVIEW**' if c.needs_review else ''}",
        f"- handle: `{c.handle}`",
        f"- includes every product tagged `{c.tag}`",
        f"- description: {_text_only(c.description_html)}",
    ]
    if c.needs_review:
        lines.append(f"- review reason: {c.review_reason}")
    lines += ["", "## Products"]
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


def plan_paths(day: str) -> tuple[str, str]:
    """(json path, markdown path) of the plan for a date."""
    base = os.path.join(settings.OUTPUT_DIR, f"store_plan_{day}")
    return f"{base}.json", f"{base}.md"


def save_plan(plan: StorePlan, overwrite: bool = False) -> tuple[str, str]:
    """Write the plan as JSON (applied) and Markdown (read). Returns both paths.

    Refuses to replace an existing plan file unless overwrite=True: that
    file may hold a person's edits.
    """
    json_path, md_path = plan_paths(plan.date)
    if os.path.exists(json_path) and not overwrite:
        raise FileExistsError(json_path)
    os.makedirs(settings.OUTPUT_DIR, exist_ok=True)
    with open(json_path, "w", encoding="utf-8") as f:
        f.write(plan.model_dump_json(indent=2))
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(render_markdown(plan))
    return json_path, md_path


def load_plan(path: str) -> StorePlan:
    """Parse a plan file. Types only - call plan_problems() before applying."""
    with open(path, encoding="utf-8") as f:
        return StorePlan.model_validate(json.load(f))
