"""
Tests for the store-setup planning side: brief validation and store_plan.py.

The rule under test is the same as enrich.py's: facts come from the brief,
only copy comes from the model, and anything doubtful goes to a person.
"""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from models.schemas import PlannedCopy, PlannerOutput, StoreBrief
from store_plan import (
    build_plan,
    clean_html,
    find_caution,
    find_claim,
    load_brief,
    load_plan,
    normalize_tags,
    plan_problems,
    plan_warnings,
    render_markdown,
    sanitize_html,
    save_plan,
)
from config import settings


def brief_dict(**overrides) -> dict:
    base = {
        "store_tag": "demo-tag",
        "collection_key": "glow",
        "collection_title": "Glow",
        "products": [
            {"key": "oil", "title": "Cleansing Oil", "one_liner": "Melts makeup.",
             "price": "20.00", "vendor": "Anua"},
            {"key": "balm", "title": "Multi Balm", "one_liner": "A stick balm.",
             "price": "25.00", "vendor": "medicube"},
            {"key": "set", "title": "Routine Set", "one_liner": "Both in one set.",
             "price": "40.00", "vendor": "Shop", "bundle_of": ["oil", "balm"]},
        ],
    }
    base.update(overrides)
    return base


def copy_for(*keys, html="<p>Nice.</p>", **overrides) -> PlannerOutput:
    return PlannerOutput(
        collection_description_html="<p>A routine.</p>",
        products=[
            PlannedCopy(key=k, description_html=html, product_type="Skincare", tags=["Glow", "k-beauty"])
            for k in keys
        ],
        **overrides,
    )


BRIEF = StoreBrief.model_validate(brief_dict())


# -- Brief validation ---------------------------------------------------

def test_sample_brief_in_repo_is_valid():
    brief = load_brief("store_brief.yaml")
    assert len(brief.products) == 4
    assert [p.key for p in brief.products if p.bundle_of] == ["glow-routine-set"]


def test_brief_rejects_duplicate_keys():
    d = brief_dict()
    d["products"][1]["key"] = "oil"
    with pytest.raises(ValidationError, match="duplicate product keys: oil"):
        StoreBrief.model_validate(d)


@pytest.mark.parametrize("bad", ["Has Space", "UPPER", "under_score", "-lead", ""])
def test_brief_rejects_keys_that_are_not_slugs(bad):
    d = brief_dict()
    d["products"][0]["key"] = bad
    d["products"][2]["bundle_of"] = ["balm"]
    with pytest.raises(ValidationError):
        StoreBrief.model_validate(d)


def test_brief_rejects_bundle_of_unknown_or_bundle_product():
    d = brief_dict()
    d["products"][2]["bundle_of"] = ["oil", "ghost"]
    with pytest.raises(ValidationError, match="ghost"):
        StoreBrief.model_validate(d)

    d = brief_dict()
    d["products"][2]["bundle_of"] = ["set"]  # a set cannot contain a set (or itself)
    with pytest.raises(ValidationError, match="bundle_of"):
        StoreBrief.model_validate(d)


def test_brief_rejects_zero_or_negative_price():
    d = brief_dict()
    d["products"][0]["price"] = "0"
    with pytest.raises(ValidationError):
        StoreBrief.model_validate(d)


def test_brief_rejects_price_with_more_than_two_decimals():
    # 19.999 would be shown as 20.00 on the approval screen but sent as 19.999.
    d = brief_dict()
    d["products"][0]["price"] = "19.999"
    with pytest.raises(ValidationError, match="decimal"):
        StoreBrief.model_validate(d)


# -- sanitize_html / normalize_tags ---------------------------------------

def test_sanitize_keeps_allowlisted_tags_and_drops_attributes():
    out = sanitize_html('<p class="x" onclick="evil()">Hi <strong>there</strong></p><ul><li>a</li></ul>')
    assert out == "<p>Hi <strong>there</strong></p><ul><li>a</li></ul>"


def test_sanitize_removes_script_with_content_and_unknown_tags():
    out = sanitize_html('<p>ok</p><script>alert(1)</script><a href="http://x">link</a><img src=x>')
    assert out == "<p>ok</p>link"


def test_sanitize_escapes_text_and_handles_empty():
    assert sanitize_html("<p>5 &lt; 6 & more</p>") == "<p>5 &lt; 6 &amp; more</p>"
    assert sanitize_html("") == ""
    assert sanitize_html(None) == ""


def test_void_embed_does_not_swallow_the_rest_of_the_description():
    cleaned = clean_html("<p>Intro.</p><embed src=x><ul><li>Feature</li></ul>")
    assert cleaned.html == "<p>Intro.</p><ul><li>Feature</li></ul>"
    assert not cleaned.truncated
    assert cleaned.dropped == ["<embed>"]


def test_unclosed_script_is_reported_as_truncated():
    cleaned = clean_html("<p>Intro.</p><script>var x = 1;<p>Lost text</p>")
    assert cleaned.html == "<p>Intro.</p>"
    assert cleaned.truncated


@pytest.mark.parametrize("edit", ["<p>Line one<br/>two</p>", "<p>A&nbsp;B</p>", "<p>It&rsquo;s nice</p>", "<P>Caps</P>"])
def test_harmless_spelling_differences_are_not_reported_as_dropped(edit):
    assert clean_html(edit).dropped == []


def test_dropped_names_what_was_removed():
    cleaned = clean_html('<p onclick="x()">Hi</p><a href="http://x">link</a><!-- note -->')
    assert cleaned.dropped == ["attribute onclick on <p>", "<a>", "an HTML comment"]


@pytest.mark.parametrize("text", [
    "Helps treat acne.", "Prevents breakouts.", "Fights stubborn pimples", "An acne treatment you can trust",
    "clinically <strong>proven</strong> results", "Clinically-proven glow", "clinically\n  proven",
    "Heals overnight", "FDA approved",
])
def test_medical_claims_block(text):
    assert find_claim(text)


@pytest.mark.parametrize("text", [
    "Helps prevent moisture loss", "A gentle treat for tired skin", "Suitable for acne-prone skin",
])
def test_common_cosmetic_wording_warns_but_does_not_block(text):
    assert find_claim(text) is None
    assert find_caution(text)


@pytest.mark.parametrize("text", [
    "A gentle treatment essence for a glowy look.", "Melts makeup and sunscreen.",
    "Leaves skin looking plump.", "A secure, curated routine.",
])
def test_ordinary_cosmetic_copy_is_not_flagged(text):
    assert find_claim(text) is None
    assert find_caution(text) is None


def test_normalize_tags_puts_required_first_and_dedupes():
    tags = normalize_tags(["Glow", " glow ", "k-beauty, skincare", "", "demo-tag"], always=["demo-tag"])
    assert tags == ["demo-tag", "glow", "k-beauty skincare"]


def test_normalize_tags_caps_length_but_never_drops_required():
    tags = normalize_tags([f"t{i}" for i in range(20)], always=["demo-tag", "bundle"])
    assert tags[:2] == ["demo-tag", "bundle"]
    assert len(tags) == 8


# -- build_plan -----------------------------------------------------------

def test_facts_come_from_brief_not_from_model():
    plan = build_plan(BRIEF, copy_for("oil", "balm", "set"), today="2026-11-12")

    oil = plan.products[0]
    assert (oil.handle, oil.title, oil.vendor, oil.price) == ("oil", "Cleansing Oil", "Anua", Decimal("20.00"))
    assert oil.description_html == "<p>Nice.</p>"
    assert oil.tags == ["demo-tag", "glow", "k-beauty"]
    assert not oil.needs_review
    assert plan.date == "2026-11-12"
    assert plan.collection.handle == "glow" and plan.collection.tag == "demo-tag"


def test_bundle_gets_bundle_tag():
    plan = build_plan(BRIEF, copy_for("oil", "balm", "set"))
    assert plan.products[2].tags[:2] == ["demo-tag", "bundle"]
    assert "bundle" not in plan.products[0].tags


def test_product_without_copy_is_kept_and_marked_for_review():
    plan = build_plan(BRIEF, copy_for("oil", "set"))

    balm = plan.products[1]
    assert balm.needs_review and "no copy" in balm.review_reason
    assert balm.title == "Multi Balm" and balm.price == Decimal("25.00")
    assert balm.tags == ["demo-tag"]


def test_products_invented_by_model_are_ignored():
    plan = build_plan(BRIEF, copy_for("oil", "balm", "set", "ghost-product"))
    assert [p.key for p in plan.products] == ["oil", "balm", "set"]


def test_medical_claim_is_kept_but_marked_for_review():
    plan = build_plan(BRIEF, copy_for("oil", "balm", "set", html="<p>Heals acne overnight.</p>"))
    assert all(p.needs_review for p in plan.products)
    assert "Heals" in plan.products[0].review_reason
    assert plan.products[0].description_html == "<p>Heals acne overnight.</p>"


def test_description_that_is_empty_after_cleaning_is_marked_for_review():
    plan = build_plan(BRIEF, copy_for("oil", "balm", "set", html="<script>x</script><p> </p>"))
    assert plan.products[0].needs_review
    assert "empty" in plan.products[0].review_reason


def test_truncated_description_is_marked_for_review():
    plan = build_plan(BRIEF, copy_for("oil", "balm", "set", html="<p>Intro.</p><script>x<p>lost</p>"))
    assert plan.products[0].needs_review
    assert "unclosed" in plan.products[0].review_reason


def test_claim_in_tags_or_product_type_marks_product_for_review():
    copy = copy_for("oil", "balm", "set")
    copy.products[0].tags = ["glow", "eczema relief"]
    copy.products[1].product_type = "Eczema Cream"
    plan = build_plan(BRIEF, copy)

    assert "tags" in plan.products[0].review_reason
    assert "product type" in plan.products[1].review_reason
    assert not plan.products[2].needs_review


def test_claim_in_collection_description_marks_collection_for_review():
    copy = copy_for("oil", "balm", "set")
    copy.collection_description_html = "<p>A routine that prevents breakouts.</p>"
    plan = build_plan(BRIEF, copy)

    assert plan.collection.needs_review and "prevents" in plan.collection.review_reason
    assert any(p.startswith("collection: marked needs_review") for p in plan_problems(plan))


# -- plan_problems: the checks that run again on the (possibly edited) file --

def good_plan():
    return build_plan(BRIEF, copy_for("oil", "balm", "set"), today="2026-11-12")


def test_clean_plan_has_no_problems():
    assert plan_problems(good_plan()) == []


def test_needs_review_flag_is_a_problem():
    plan = build_plan(BRIEF, copy_for("oil", "set"))
    assert any(p.startswith("balm: marked needs_review") for p in plan_problems(plan))


def test_edited_html_is_checked_again():
    plan = good_plan()
    plan.products[0].description_html = '<p onclick="x()">Hi</p><script>alert(1)</script>'
    assert any("oil: description uses attribute onclick on <p>, which is not allowed" in p
               for p in plan_problems(plan))


@pytest.mark.parametrize("edit", ["<p>Line one<br/>two</p>", "<p>A&nbsp;B</p>", "<p>It&rsquo;s nice</p>", "<P>Caps</P>"])
def test_harmless_hand_edits_are_accepted(edit):
    plan = good_plan()
    plan.products[0].description_html = edit
    assert plan_problems(plan) == []


def test_warning_wording_does_not_block_and_is_listed():
    plan = good_plan()
    plan.products[0].description_html = "<p>Helps prevent moisture loss. Suitable for acne-prone skin.</p>"
    plan.collection.description_html = "<p>A gentle treat for tired skin.</p>"

    assert plan_problems(plan) == []
    warnings = plan_warnings(plan)
    assert any(w.startswith("oil: description uses 'prevent'") for w in warnings)
    assert any(w.startswith("collection: description uses 'treat'") for w in warnings)
    assert "## Wording to check (does not block apply)" in render_markdown(plan)


def test_clean_plan_has_no_warnings_section():
    plan = good_plan()
    assert plan_warnings(plan) == []
    assert "Wording to check" not in render_markdown(plan)


def test_claim_cannot_be_cleared_by_flipping_the_flag():
    plan = build_plan(BRIEF, copy_for("oil", "balm", "set", html="<p>Heals acne overnight.</p>"))
    for p in plan.products:  # a person sets needs_review to false without rewording
        p.needs_review = False
    problems = plan_problems(plan)
    assert any("oil: description makes a medical claim" in p for p in problems)


def test_edited_handle_tags_and_duplicates_are_caught():
    plan = good_plan()
    plan.products[0].handle = "Bad Handle"
    plan.products[1].tags = ["glow"]          # store tag removed -> would miss the collection
    plan.products[2].handle = "balm"          # same handle as products[1]
    problems = "\n".join(plan_problems(plan))

    assert "oil: handle 'Bad Handle' must be lowercase" in problems
    assert "balm: tags must include 'demo-tag'" in problems
    assert "balm: handle is used by more than one product" in problems


def test_plan_file_rejects_bad_price(tmp_path):
    plan = good_plan()
    data = plan.model_dump(mode="json")
    for bad in ("0", "-5", "19.999"):
        data["products"][0]["price"] = bad
        with pytest.raises(ValidationError):
            type(plan).model_validate(data)


# -- render / save / load ---------------------------------------------------

def test_markdown_shows_facts_and_review_flags():
    plan = build_plan(BRIEF, copy_for("oil", "set"), today="2026-11-12")
    md = render_markdown(plan)

    assert "# Store setup plan (2026-11-12)" in md
    assert "price: 20.00" in md
    assert "### Multi Balm  **NEEDS REVIEW**" in md
    assert "**drafts**" in md


def test_plan_round_trips_through_json_file(tmp_path):
    plan = build_plan(BRIEF, copy_for("oil", "balm", "set"), today="2026-11-12")
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(settings, "OUTPUT_DIR", str(tmp_path))
        json_path, md_path = save_plan(plan)

    assert json_path.endswith("store_plan_2026-11-12.json") and md_path.endswith(".md")
    assert load_plan(json_path) == plan


def test_save_plan_does_not_overwrite_an_existing_plan(tmp_path):
    plan = good_plan()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(settings, "OUTPUT_DIR", str(tmp_path))
        json_path, _ = save_plan(plan)
        edited = open(json_path, encoding="utf-8").read().replace("Nice.", "Edited by a person.")
        open(json_path, "w", encoding="utf-8").write(edited)

        with pytest.raises(FileExistsError):
            save_plan(plan)
        assert "Edited by a person." in open(json_path, encoding="utf-8").read()

        save_plan(plan, overwrite=True)
        assert "Edited by a person." not in open(json_path, encoding="utf-8").read()
