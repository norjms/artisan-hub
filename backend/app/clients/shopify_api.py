"""Shopify Admin GraphQL API (services/stores.py is the only caller).

Credentials: an app made in the store owner's Shopify Dev Dashboard and installed on the store. The hub trades its
client ID and secret for an access token (client credentials grant, valid 24 hours) and renews it before it ends.
A store that still has a legacy custom app can give its Admin API access token (shpat_...) instead.
"""
import threading
import time

import httpx

from .base import ApiError, BaseClient

API_VERSION = "2026-10"
_tokens: dict = {}          # shop -> (token, expires_at): the worker and each web process keep their own
_lock = threading.Lock()


def shop_domain(shop: str) -> str:
    """"my-store", "my-store.myshopify.com" or its admin URL -> "my-store.myshopify.com"."""
    s = (shop or "").strip().lower()
    for p in ("https://", "http://"):
        if s.startswith(p):
            s = s[len(p):]
    s = s.split("/")[0]
    if s.startswith("admin.shopify.com"):
        raise ValueError("use the store's myshopify.com name, for example my-store.myshopify.com")
    if not s:
        raise ValueError("the Shopify store name is missing")
    return s if s.endswith(".myshopify.com") else f"{s}.myshopify.com"


class ShopifyClient(BaseClient):
    service = "shopify"

    def __init__(self, shop: str, client_id: str | None = None, client_secret: str | None = None,
                 access_token: str | None = None, timeout=60.0):
        self.shop = shop_domain(shop)
        self.client_id, self.client_secret, self.static_token = client_id, client_secret, access_token
        if not access_token and not (client_id and client_secret):
            raise ValueError("Shopify needs the app's client ID and client secret (or a legacy access token)")
        super().__init__(f"https://{self.shop}/admin/api/{API_VERSION}", timeout=timeout)

    # ---------- auth ----------
    def token(self) -> str:
        if self.static_token:
            return self.static_token
        with _lock:
            hit = _tokens.get(self.shop)
        if hit and hit[1] - 300 > time.time():
            return hit[0]
        r = httpx.post(f"https://{self.shop}/admin/oauth/access_token", timeout=30.0,
                       data={"grant_type": "client_credentials", "client_id": self.client_id,
                             "client_secret": self.client_secret})
        if r.status_code >= 400:
            raise ApiError(self.service, r.status_code, f"could not get an access token: {r.text[:300]}")
        d = r.json()
        tok = d["access_token"]
        with _lock:
            _tokens[self.shop] = (tok, time.time() + float(d.get("expires_in") or 86399))
        return tok

    def gql(self, query: str, variables: dict | None = None) -> dict:
        """Run one GraphQL operation; GraphQL errors and userErrors become ApiError."""
        r = self.request("POST", "/graphql.json", json={"query": query, "variables": variables or {}},
                         headers={"X-Shopify-Access-Token": self.token()})
        if r.get("errors"):
            msg = "; ".join(str(e.get("message")) for e in r["errors"])[:600]
            throttled = any((e.get("extensions") or {}).get("code") == "THROTTLED" for e in r["errors"])
            raise ApiError(self.service, 429 if throttled else 400, msg)
        data = r.get("data") or {}
        for v in data.values():
            if isinstance(v, dict) and v.get("userErrors"):
                raise ApiError(self.service, 422, "; ".join(
                    f"{'.'.join(map(str, e.get('field') or []))}: {e.get('message')}".lstrip(": ")
                    for e in v["userErrors"])[:600])
        return data

    # ---------- store ----------
    def shop_info(self) -> dict:
        return self.gql("{ shop { name myshopifyDomain currencyCode primaryDomain { url } } }")["shop"]

    def primary_location(self) -> str:
        d = self.gql("{ locations(first: 10) { nodes { id name isActive fulfillsOnlineOrders } } }")
        nodes = [n for n in d["locations"]["nodes"] if n.get("isActive")]
        best = next((n for n in nodes if n.get("fulfillsOnlineOrders")), nodes[0] if nodes else None)
        if not best:
            raise ApiError(self.service, 404, "the store has no active location to hold stock")
        return best["id"]

    # ---------- products ----------
    def product_set(self, data: dict, product_gid: str | None = None) -> dict:
        q = """mutation set($input: ProductSetInput!, $identifier: ProductSetIdentifiers) {
          productSet(synchronous: true, input: $input, identifier: $identifier) {
            product { id handle status onlineStoreUrl
              media(first: 50) { nodes { id alt ... on MediaImage { image { url } } } }
              variants(first: 100) { nodes { id sku inventoryItem { id } } } }
            userErrors { field message code } } }"""
        v = {"input": data, "identifier": {"id": product_gid} if product_gid else None}
        return self.gql(q, v)["productSet"]["product"]

    def find_product_by_sku(self, sku: str) -> str | None:
        d = self.gql("""query($q: String!) { productVariants(first: 1, query: $q) { nodes { product { id } } } }""",
                     {"q": f"sku:{_quote(sku)}"})
        nodes = d["productVariants"]["nodes"]
        return nodes[0]["product"]["id"] if nodes else None

    def product_exists(self, gid: str) -> bool:
        return bool(self.gql("query($id: ID!) { product(id: $id) { id } }", {"id": gid}).get("product"))

    def set_status(self, gid: str, status: str) -> dict:
        q = """mutation($p: ProductUpdateInput!) { productUpdate(product: $p) { product { id status }
               userErrors { field message } } }"""
        return self.gql(q, {"p": {"id": gid, "status": status}})["productUpdate"]["product"]

    def track_inventory(self, item_gids: list[str]):
        q = """mutation($id: ID!, $input: InventoryItemInput!) { inventoryItemUpdate(id: $id, input: $input) {
               inventoryItem { id tracked } userErrors { field message } } }"""
        for gid in item_gids:
            self.gql(q, {"id": gid, "input": {"tracked": True}})

    def stock_at(self, location_gid: str, item_gids: list[str]):
        """Make sure each inventory item is stocked at the location (quantities can only be set where it is)."""
        q = """mutation($id: ID!, $l: ID!) { inventoryActivate(inventoryItemId: $id, locationId: $l) {
               inventoryLevel { id } userErrors { field message } } }"""
        for gid in item_gids:
            try:
                self.gql(q, {"id": gid, "l": location_gid})
            except ApiError as e:
                if "already" not in e.message.lower():
                    raise

    def set_quantities(self, location_gid: str, qty_by_item: dict[str, int], key: str):
        """Set "available" stock for inventory items at one location. key makes a retry of the same push harmless."""
        if not qty_by_item:
            return
        q = """mutation($input: InventorySetQuantitiesInput!, $key: String!) {
          inventorySetQuantities(input: $input) @idempotent(key: $key) {
            inventoryAdjustmentGroup { id } userErrors { field message code } } }"""
        inp = {"name": "available", "reason": "correction",
               "quantities": [{"inventoryItemId": i, "locationId": location_gid, "quantity": int(n),
                               "changeFromQuantity": None} for i, n in qty_by_item.items()]}
        self.gql(q, {"input": inp, "key": key[:255]})

    def delete_product(self, gid: str):
        self.gql("""mutation($i: ProductDeleteInput!) { productDelete(input: $i) { deletedProductId
                    userErrors { field message } } }""", {"i": {"id": gid}})

    # ---------- orders ----------
    ORDER_FIELDS = """id legacyResourceId name createdAt updatedAt cancelledAt test note
        displayFinancialStatus displayFulfillmentStatus currencyCode
        totalPriceSet { shopMoney { amount currencyCode } }
        %s
        lineItems(first: 100) { nodes { id sku name quantity variant { id } product { id } } }"""

    def _order_fields(self, customer: bool) -> str:
        return self.ORDER_FIELDS % ("customer { displayName }" if customer else "")

    def _with_customer(self, run):
        """The customer's name is protected data: an app not yet granted it gets the order without the name."""
        try:
            return run(True)
        except ApiError as e:
            if "customer" not in e.message.lower() and "access denied" not in e.message.lower():
                raise
            return run(False)

    def get_order(self, gid: str) -> dict | None:
        def run(cust):
            q = "query($id: ID!) { order(id: $id) { %s } }" % self._order_fields(cust)
            return self.gql(q, {"id": gid}).get("order")
        return self._with_customer(run)

    def orders_updated_since(self, since_iso: str | None, after: str | None = None) -> tuple[list, str | None]:
        """One page of orders updated after since_iso (all orders without it), oldest first, and the next cursor."""
        def run(cust):
            q = """query($q: String, $after: String) { orders(first: 50, after: $after, query: $q, sortKey: UPDATED_AT) {
                   nodes { %s } pageInfo { hasNextPage endCursor } } }""" % self._order_fields(cust)
            return self.gql(q, {"q": f"updated_at:>'{since_iso}'" if since_iso else None, "after": after})["orders"]
        d = self._with_customer(run)
        return d["nodes"], (d["pageInfo"]["endCursor"] if d["pageInfo"]["hasNextPage"] else None)

    def fulfill_order(self, order_gid: str, tracking: dict | None = None, notify: bool = True) -> int:
        """Fulfil every open fulfillment order of the order (all items shipped). Returns how many were fulfilled."""
        d = self.gql("""query($id: ID!) { order(id: $id) { fulfillmentOrders(first: 20) {
                        nodes { id status } } } }""", {"id": order_gid})
        open_fos = [n["id"] for n in ((d.get("order") or {}).get("fulfillmentOrders") or {}).get("nodes", [])
                    if n["status"] in ("OPEN", "IN_PROGRESS")]
        if not open_fos:
            return 0
        f = {"lineItemsByFulfillmentOrder": [{"fulfillmentOrderId": i} for i in open_fos], "notifyCustomer": notify}
        if tracking and (tracking.get("number") or tracking.get("url")):
            f["trackingInfo"] = {k: v for k, v in tracking.items() if k in ("company", "number", "url") and v}
        self.gql("""mutation($f: FulfillmentInput!) { fulfillmentCreate(fulfillment: $f) { fulfillment { id status }
                    userErrors { field message } } }""", {"f": f})
        return len(open_fos)

    def add_order_note(self, order_gid: str, text: str):
        d = self.gql("query($id: ID!) { order(id: $id) { note } }", {"id": order_gid})
        note = ((d.get("order") or {}).get("note") or "").strip()
        note = f"{note}\n{text}".strip() if text not in note else note
        self.gql("""mutation($i: OrderInput!) { orderUpdate(input: $i) { order { id } userErrors { field message } } }""",
                 {"i": {"id": order_gid, "note": note[:5000]}})

    # ---------- webhooks ----------
    def webhooks(self) -> list[dict]:
        d = self.gql("{ webhookSubscriptions(first: 100) { nodes { id topic uri } } }")
        return d["webhookSubscriptions"]["nodes"]

    def add_webhook(self, topic: str, uri: str) -> dict:
        q = """mutation($t: WebhookSubscriptionTopic!, $s: WebhookSubscriptionInput!) {
               webhookSubscriptionCreate(topic: $t, webhookSubscription: $s) { webhookSubscription { id topic uri }
               userErrors { field message } } }"""
        return self.gql(q, {"t": topic, "s": {"uri": uri, "format": "JSON"}})["webhookSubscriptionCreate"]


def _quote(s: str) -> str:
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'


def gid(kind: str, legacy_id) -> str:
    return f"gid://shopify/{kind}/{legacy_id}"
