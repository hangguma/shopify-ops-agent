"""
Store-setup writes to the Shopify Admin API. Called by executor.py only.

Why this design?
- Plain functions, not CrewAI tools, in a module that does not import
  crewai: no agent is ever handed a write tool.
- Each write looks first. A product or collection that already exists and
  was not made by this setup (it lacks the store tag / the tag rule) is
  never touched: the write stops with StoreConflict instead.
- A product is created as a DRAFT. When this setup later updates a product
  it created, `status` is left out, so a product the seller has published
  stays published.
- Needs the `write_products` access scope. Mutations report bad input as
  `userErrors` with HTTP 200, so those are raised here like GraphQL errors.
"""

from tools.shopify_client import _graphql_request


class ShopifyUserError(RuntimeError):
    """A mutation was rejected by Shopify (payload.userErrors was not empty)."""


class StoreConflict(RuntimeError):
    """Something with this handle already exists and does not belong to this setup."""


def _raise_user_errors(payload: dict, what: str) -> None:
    errors = payload.get("userErrors") or []
    if errors:
        detail = "; ".join(f"{e.get('field')}: {e.get('message')}" for e in errors)
        raise ShopifyUserError(f"{what} rejected: {detail}")


PRODUCT_BY_HANDLE_QUERY = """
query ($handle: String!) {
  productByHandle(handle: $handle) { id handle status tags }
}
"""

PRODUCT_SET_MUTATION = """
mutation ($identifier: ProductSetIdentifiers, $input: ProductSetInput!) {
  productSet(identifier: $identifier, input: $input, synchronous: true) {
    product { id handle title status }
    userErrors { field message }
  }
}
"""


def upsert_product(product_input: dict, store_tag: str) -> tuple[dict, bool]:
    """Create the product, or update it if this setup made it. Returns (product, created).

    `product_input` must not carry `status`: it is DRAFT on create and left
    unchanged on update. An update sets the product's options, variants and
    price to exactly the input, so it is only done on products that carry
    `store_tag`; any other product with this handle raises StoreConflict.
    """
    handle = product_input["handle"]
    existing = _graphql_request(PRODUCT_BY_HANDLE_QUERY, {"handle": handle})["productByHandle"]

    if existing is None:
        variables = {"identifier": None, "input": {**product_input, "status": "DRAFT"}}
    else:
        tags = {t.casefold() for t in existing.get("tags") or []}
        if store_tag.casefold() not in tags:
            raise StoreConflict(
                f"A product with handle '{handle}' already exists and is not tagged "
                f"'{store_tag}', so it was not made by this setup. Left untouched. "
                f"Change the key in the brief, or tag the product if it should be managed here."
            )
        update = {k: v for k, v in product_input.items() if k != "status"}
        variables = {"identifier": {"id": existing["id"]}, "input": update}

    payload = _graphql_request(PRODUCT_SET_MUTATION, variables)["productSet"]
    _raise_user_errors(payload, f"productSet({handle})")
    return payload["product"], existing is None


COLLECTION_BY_HANDLE_QUERY = """
query ($handle: String!) {
  collectionByHandle(handle: $handle) {
    id
    handle
    title
    ruleSet { appliedDisjunctively rules { column relation condition } }
  }
}
"""

# This is the `input` + `ruleSet` form, the only one in API version 2026-04.
# From 2026-07 it is deprecated in favour of `collection` + `sources`;
# migrate when SHOPIFY_API_VERSION is raised.
COLLECTION_CREATE_MUTATION = """
mutation ($input: CollectionInput!) {
  collectionCreate(input: $input) {
    collection { id handle title }
    userErrors { field message }
  }
}
"""


def _is_tag_collection(collection: dict, tag: str) -> bool:
    rules = (collection.get("ruleSet") or {}).get("rules") or []
    if len(rules) != 1:
        return False
    rule = rules[0]
    return (
        rule.get("column") == "TAG"
        and rule.get("relation") == "EQUALS"
        and str(rule.get("condition", "")).casefold() == tag.casefold()
    )


def ensure_collection(handle: str, title: str, description_html: str, tag: str) -> tuple[dict, bool]:
    """Return (collection, created). The collection holds every product tagged `tag`.

    An existing collection is accepted only if its single rule is
    "tag equals `tag`"; it is then left as it is. A manual collection, or one
    with other rules, raises StoreConflict because the new products would
    not be in it. New collections are unpublished by default.
    """
    existing = _graphql_request(COLLECTION_BY_HANDLE_QUERY, {"handle": handle})["collectionByHandle"]
    if existing:
        if not _is_tag_collection(existing, tag):
            raise StoreConflict(
                f"A collection with handle '{handle}' already exists but is not the "
                f"'tag equals {tag}' collection this setup creates, so the new products "
                f"would not be in it. Left untouched. Change collection_key in the brief."
            )
        return existing, False

    data = _graphql_request(
        COLLECTION_CREATE_MUTATION,
        {
            "input": {
                "handle": handle,
                "title": title,
                "descriptionHtml": description_html,
                "ruleSet": {
                    "appliedDisjunctively": False,
                    "rules": [{"column": "TAG", "relation": "EQUALS", "condition": tag}],
                },
            }
        },
    )
    payload = data["collectionCreate"]
    _raise_user_errors(payload, f"collectionCreate({handle})")
    return payload["collection"], True
