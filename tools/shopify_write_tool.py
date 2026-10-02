"""
Shopify Admin GraphQL write operations for store setup (Phase 2A).

Why this design?
- These are plain functions, NOT CrewAI tools. No LLM ever calls them. The
  Executor calls them only for plan items a human approved, so the model can
  propose anything but can change nothing on its own.
- Reuses `_graphql_request` (and its token cache) from tools/shopify_tool.py,
  as planned in the Phase 1 README.
- Shopify reports most write failures as `userErrors` inside a 200 response,
  not as HTTP or GraphQL errors. `_raise_user_errors` turns those into an
  exception so a failed write is never mistaken for a success.
- Lookups by handle (`find_*`) make the setup idempotent: the Executor checks
  before it creates. Products use `productSet` with a handle identifier,
  which is an upsert by design.

Scopes needed on the Dev Dashboard app (in addition to Phase 1's read scopes):
write_products, write_content, read_online_store_navigation,
write_online_store_navigation, read_publications, write_publications.
"""

from config import settings
from models.store_plan import CollectionDraft, PageDraft, ProductDraft
from tools.shopify_tool import _graphql_request


class ShopifyUserError(RuntimeError):
    """A mutation returned userErrors (validation or permission problems)."""


def _raise_user_errors(payload: dict, operation: str) -> None:
    errors = payload.get("userErrors") or []
    if errors:
        detail = "; ".join(
            f"{'.'.join(str(p) for p in (e.get('field') or []))}: {e.get('message')}".lstrip(": ")
            for e in errors
        )
        raise ShopifyUserError(f"{operation} failed: {detail}")


# -- Collections ---------------------------------------------------------

def find_collection_by_handle(handle: str) -> str | None:
    """Return the collection GID for `handle`, or None if it does not exist."""
    query = """
    query ($handle: String!) {
      collectionByIdentifier(identifier: {handle: $handle}) { id }
    }
    """
    node = _graphql_request(query, {"handle": handle}).get("collectionByIdentifier")
    return node["id"] if node else None


def create_collection(draft: CollectionDraft) -> str:
    """Create a manual collection and return its GID. Created unpublished."""
    mutation = """
    mutation ($collection: CollectionCreateInput!) {
      collectionCreate(collection: $collection) {
        collection { id handle }
        userErrors { field message }
      }
    }
    """
    variables = {
        "collection": {
            "title": draft.title,
            "handle": draft.handle,
            "descriptionHtml": draft.description_html,
        }
    }
    payload = _graphql_request(mutation, variables)["collectionCreate"]
    _raise_user_errors(payload, f"collectionCreate({draft.handle})")
    return payload["collection"]["id"]


# -- Products ------------------------------------------------------------

def upsert_product(draft: ProductDraft, collection_ids: list[str]) -> str:
    """Create or update a single-variant product by handle; return its GID.

    `productSet` treats list fields (variants, collections) as the complete
    set, so re-running replaces rather than appends - which is what makes
    re-applying a plan safe.
    """
    mutation = """
    mutation ($input: ProductSetInput!, $identifier: ProductSetIdentifiers) {
      productSet(input: $input, identifier: $identifier, synchronous: true) {
        product { id handle }
        userErrors { field message }
      }
    }
    """
    product_input = {
        "title": draft.title,
        "handle": draft.handle,
        "descriptionHtml": draft.description_html,
        "productType": draft.product_type,
        "vendor": draft.vendor,
        "tags": draft.tags,
        "status": "ACTIVE",
        "productOptions": [{"name": "Title", "values": [{"name": "Default Title"}]}],
        "variants": [
            {
                "optionValues": [{"optionName": "Title", "name": "Default Title"}],
                "price": draft.price,
            }
        ],
        "collections": collection_ids,
    }
    if draft.seo_title or draft.seo_description:
        product_input["seo"] = {"title": draft.seo_title, "description": draft.seo_description}

    payload = _graphql_request(
        mutation, {"input": product_input, "identifier": {"handle": draft.handle}}
    )["productSet"]
    _raise_user_errors(payload, f"productSet({draft.handle})")
    return payload["product"]["id"]


# -- Pages ---------------------------------------------------------------

def find_page_by_handle(handle: str) -> str | None:
    """Return the page GID for `handle`, or None if it does not exist."""
    query = """
    query ($query: String!) {
      pages(first: 1, query: $query) { nodes { id handle } }
    }
    """
    nodes = _graphql_request(query, {"query": f"handle:{handle}"})["pages"]["nodes"]
    # The search is fuzzy; only trust an exact handle match.
    for node in nodes:
        if node["handle"] == handle:
            return node["id"]
    return None


def create_page(draft: PageDraft) -> str:
    """Create and publish an Online Store page; return its GID."""
    mutation = """
    mutation ($page: PageCreateInput!) {
      pageCreate(page: $page) {
        page { id handle }
        userErrors { field message }
      }
    }
    """
    variables = {
        "page": {
            "title": draft.title,
            "handle": draft.handle,
            "body": draft.body_html,
            "isPublished": True,
        }
    }
    payload = _graphql_request(mutation, variables)["pageCreate"]
    _raise_user_errors(payload, f"pageCreate({draft.handle})")
    return payload["page"]["id"]


# -- Navigation ----------------------------------------------------------

def find_menu_by_handle(handle: str) -> str | None:
    """Return the menu GID for `handle`, or None if it does not exist."""
    query = """
    query {
      menus(first: 25) { nodes { id handle } }
    }
    """
    for node in _graphql_request(query)["menus"]["nodes"]:
        if node["handle"] == handle:
            return node["id"]
    return None


def update_menu(menu_id: str, title: str, items: list[dict]) -> str:
    """Replace a menu's items. `items` are MenuItemUpdateInput dicts."""
    mutation = """
    mutation ($id: ID!, $title: String!, $items: [MenuItemUpdateInput!]!) {
      menuUpdate(id: $id, title: $title, items: $items) {
        menu { id handle }
        userErrors { field message }
      }
    }
    """
    payload = _graphql_request(mutation, {"id": menu_id, "title": title, "items": items})[
        "menuUpdate"
    ]
    _raise_user_errors(payload, f"menuUpdate({menu_id})")
    return payload["menu"]["id"]


# -- Publishing ----------------------------------------------------------

def get_online_store_publication_id() -> str:
    """Return the Online Store sales channel's publication GID.

    Set SHOPIFY_ONLINE_STORE_PUBLICATION_ID to skip the lookup (or if the
    channel has a non-default name).
    """
    if settings.SHOPIFY_ONLINE_STORE_PUBLICATION_ID:
        return settings.SHOPIFY_ONLINE_STORE_PUBLICATION_ID

    query = """
    query {
      publications(first: 25) { nodes { id name } }
    }
    """
    for node in _graphql_request(query)["publications"]["nodes"]:
        if node.get("name") == "Online Store":
            return node["id"]
    raise RuntimeError(
        "Online Store publication not found. Is the Online Store channel installed? "
        "Set SHOPIFY_ONLINE_STORE_PUBLICATION_ID to override."
    )


def publish(resource_id: str, publication_id: str) -> None:
    """Publish a product or collection to a sales channel (idempotent)."""
    mutation = """
    mutation ($id: ID!, $input: [PublicationInput!]!) {
      publishablePublish(id: $id, input: $input) {
        userErrors { field message }
      }
    }
    """
    payload = _graphql_request(
        mutation, {"id": resource_id, "input": [{"publicationId": publication_id}]}
    )["publishablePublish"]
    _raise_user_errors(payload, f"publishablePublish({resource_id})")
