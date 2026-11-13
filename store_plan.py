"""
Store plan - turns the seller's brief plus the LLM's copy into a StorePlan.

Why this design?
- Same split as enrich.py: the LLM writes copy, code supplies facts. Title,
  price, vendor and handle come from store_brief.yaml and are never copied by
  the model, so a hallucinated price cannot reach the store.
- Everything the model wrote is checked before a person sees it: HTML is
  reduced to a small allowlist, tags are normalised, and copy that makes a
  medical claim is kept but marked needs_review.
- The plan is a JSON file on disk. That file - not the model's output - is
  what gets approved and applied, and a person may edit it. So the same
  checks run again on the file (plan_problems) before anything is written:
  an edit cannot bring back a disallowed tag, a blocked claim, a bad handle
  or a product that has lost the store tag.
- Claims come in two strengths. Wording that makes a product sound like a
  drug ("heals", "treats acne", "clinically proven") blocks apply and is
  cleared only by rewording - the check runs on the text itself, so
  flipping needs_review does nothing. Words that are usually fine in
  cosmetic copy but worth a look ("prevent", "treat", "acne" on their own)
  are shown as warnings (plan_warnings) and do not block.
"""

import json
import os
import re
from datetime import date
from html import escape, unescape
from html.parser import HTMLParser
from typing import NamedTuple

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

# Both patterns run on visible text: tags removed, whitespace collapsed.
_CONDITION = r"(?:acne|breakouts?|pimples?|blemish(?:es)?)"

# Blocks apply. Disease/drug language, and treat/prevent aimed at a condition.
_CLAIM_BLOCK = re.compile(
    r"\b("
    r"cures?|cured|curing|heals?|healed|healing|"
    r"eczema|rosacea|psoriasis|fda|prescription|"
    r"clinically[\s-]+proven|"
    r"(?:treat|prevent|fight|clear)(?:s|ed|ing)?\s+(?:\w+\s+){0,2}?" + _CONDITION + r"|"
    r"acne[\s-]+(?:treatment|medication|medicine)"
    r")\b",
    re.IGNORECASE,
)

# Warns only. Common in ordinary cosmetic copy ("helps prevent moisture
# loss", "a treat for tired skin", "for acne-prone skin").
_CLAIM_WARN = re.compile(
    r"\b(treats?|treated|treating|prevents?|prevented|preventing|acne)\b",
    re.IGNORECASE,
)


class Cleaned(NamedTuple):
    html: str
    truncated: bool       # an unclosed script/style block swallowed the rest
    dropped: list[str]    # what cleaning removed, e.g. ["<a>", "attribute onclick on <p>"]


class _Sanitizer(HTMLParser):
    """Keeps allowlisted tags without attributes; escapes all text."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.dropped: list[str] = []
        self.skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in _DROP_WITH_CONTENT:
            self.skip_depth += 1
            self.dropped.append(f"<{tag}>")
        elif self.skip_depth:
            return
        elif tag in _ALLOWED_TAGS:
            self.dropped.extend(f"attribute {name} on <{tag}>" for name, _ in attrs)
            self.out.append(f"<{tag}>")
        else:
            self.dropped.append(f"<{tag}>")

    def handle_endtag(self, tag):
        if tag in _DROP_WITH_CONTENT:
            self.skip_depth = max(0, self.skip_depth - 1)
        elif not self.skip_depth and tag in _ALLOWED_TAGS and tag != "br":
            self.out.append(f"</{tag}>")

    def handle_data(self, data):
        if not self.skip_depth:
            self.out.append(escape(data, quote=False))

    def handle_comment(self, data):
        self.dropped.append("an HTML comment")

    def handle_decl(self, decl):
        self.dropped.append("a <!...> declaration")

    def handle_pi(self, data):
        self.dropped.append("a <?...> instruction")

    def unknown_decl(self, data):
        self.dropped.append("a <![...]> section")


def clean_html(html: str) -> Cleaned:
    """Reduce HTML to the allowlist and report what was removed.

    Spelling differences that change nothing visible (`<br/>`, `<P>`,
    `&nbsp;`, `&rsquo;`) are normalised and are not reported as dropped.
    """
    parser = _Sanitizer()
    parser.feed(html or "")
    parser.close()
    return Cleaned("".join(parser.out).strip(), parser.skip_depth > 0, parser.dropped)


def sanitize_html(html: str) -> str:
    return clean_html(html).html


def _text_only(html: str) -> str:
    """Visible text: tags become spaces, entities are decoded, whitespace collapses."""
    return " ".join(unescape(re.sub(r"<[^>]+>", " ", html or "")).split())


def find_claim(*texts: str) -> str | None:
    """First wording that blocks apply (drug-like claim), or None."""
    for text in texts:
        if (m := _CLAIM_BLOCK.search(_text_only(text))):
            return m.group(0)
    return None


def find_caution(*texts: str) -> str | None:
    """First word that deserves a look but does not block, or None."""
    for text in texts:
        if (m := _CLAIM_WARN.search(_text_only(text))):
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


def _html_problem(html: str) -> str:
    """Why this HTML cannot be written as it stands, or ''."""
    cleaned = clean_html(html)
    if cleaned.dropped:
        allowed = ", ".join(sorted(_ALLOWED_TAGS))
        return (f"description uses {cleaned.dropped[0]}, which is not allowed "
                f"(allowed tags: {allowed}; no attributes)")
    if cleaned.truncated:
        return "description has an unclosed script/style block; the text after it was dropped"
    return ""


def _copy_fields(description_html: str, product_type: str, tags: list[str]):
    return (("description", description_html), ("product type", product_type), ("tags", " | ".join(tags)))


def _copy_problem(description_html: str, product_type: str, tags: list[str]) -> str:
    """Why this copy cannot be applied as it stands, or '' if it can."""
    if (problem := _html_problem(description_html)):
        return problem
    if not _text_only(description_html):
        return "description is empty"
    for label, text in _copy_fields(description_html, product_type, tags):
        if (wording := find_claim(text)):
            return f"{label} makes a medical claim: {wording!r}. Reword it."
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
        cleaned = clean_html(c.description_html) if c else Cleaned("", False, [])
        product_type = " ".join(c.product_type.split())[:60] if c else ""
        tags = normalize_tags(c.tags if c else [], always)

        if c is None:
            reason = "the model returned no copy for this product"
        elif cleaned.truncated:
            reason = "description had an unclosed script/style block; the text after it was dropped"
        else:
            reason = _copy_problem(cleaned.html, product_type, [t for t in tags if t not in always])

        products.append(PlannedProduct(
            key=p.key,
            handle=p.key,
            title=p.title,
            vendor=p.vendor,
            price=p.price,
            product_type=product_type,
            description_html=cleaned.html,
            tags=tags,
            needs_review=bool(reason),
            review_reason=reason,
        ))

    cleaned = clean_html(copy.collection_description_html)
    collection_reason = ""
    if cleaned.truncated:
        collection_reason = "description had an unclosed script/style block; the text after it was dropped"
    elif (wording := find_claim(cleaned.html)):
        collection_reason = f"description makes a medical claim: {wording!r}. Reword it."

    return StorePlan(
        date=today or date.today().isoformat(),
        store_tag=brief.store_tag,
        collection=PlannedCollection(
            handle=brief.collection_key,
            title=brief.collection_title,
            description_html=cleaned.html,
            tag=brief.store_tag,
            needs_review=bool(collection_reason),
            review_reason=collection_reason,
        ),
        products=products,
    )


def _other_tags(p: PlannedProduct, store_tag: str) -> list[str]:
    return [t for t in p.tags if t.casefold() != store_tag.casefold()]


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
    if (problem := _html_problem(c.description_html)):
        problems.append(f"collection: {problem}")
    elif (wording := find_claim(c.description_html)):
        problems.append(f"collection: description makes a medical claim: {wording!r}. Reword it.")

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
        if (problem := _copy_problem(p.description_html, p.product_type, _other_tags(p, tag))):
            problems.append(f"{p.key}: {problem}")

    return problems


def plan_warnings(plan: StorePlan) -> list[str]:
    """Wording worth a second look. Shown to the person; never blocks apply."""
    warnings: list[str] = []

    def check(owner: str, label: str, text: str) -> None:
        if not find_claim(text) and (word := find_caution(text)):
            warnings.append(
                f"{owner}: {label} uses {word!r} - fine for a cosmetic effect, not for a skin condition"
            )

    check("collection", "description", plan.collection.description_html)
    for p in plan.products:
        for label, text in _copy_fields(p.description_html, p.product_type, _other_tags(p, plan.store_tag)):
            check(p.key, label, text)
    return warnings


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

    warnings = plan_warnings(plan)
    if warnings:
        lines += ["", "## Wording to check (does not block apply)"]
        lines += [f"- {w}" for w in warnings]
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
