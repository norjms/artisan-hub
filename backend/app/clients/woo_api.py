from .base import BaseClient


class WooClient(BaseClient):
    service = "woocommerce"

    def __init__(self, url, key, secret, verify=True):
        super().__init__(url.rstrip("/") + "/wp-json/wc/v3", auth=(key, secret), verify=verify, timeout=120.0)

    # products
    def get_product(self, pid):
        return self.get(f"/products/{pid}")

    def find_product_by_sku(self, sku):
        r = self.get("/products", params={"sku": sku, "status": "any"})
        return r[0] if r else None

    def create_product(self, data):
        return self.post("/products", json=data)

    def update_product(self, pid, data):
        return self.put(f"/products/{pid}", json=data)

    def list_products(self, page=1, per_page=100):
        return self.get("/products", params={"page": page, "per_page": per_page, "status": "any"})

    # variations
    def list_variations(self, pid):
        out, page = [], 1
        while True:
            r = self.get(f"/products/{pid}/variations", params={"per_page": 100, "page": page})
            out += r
            if len(r) < 100:
                return out
            page += 1

    def create_variation(self, pid, data):
        return self.post(f"/products/{pid}/variations", json=data)

    def update_variation(self, pid, vid, data):
        return self.put(f"/products/{pid}/variations/{vid}", json=data)

    def delete_variation(self, pid, vid):
        return self.delete(f"/products/{pid}/variations/{vid}", params={"force": "true"})

    def batch_variations(self, pid, data):
        return self.post(f"/products/{pid}/variations/batch", json=data)

    # taxonomy
    def list_categories(self):
        out, page = [], 1
        while True:
            r = self.get("/products/categories", params={"per_page": 100, "page": page})
            out += r
            if len(r) < 100:
                return out
            page += 1

    def create_category(self, name, parent=0):
        return self.post("/products/categories", json={"name": name, "parent": parent})

    def list_attributes(self):
        return self.get("/products/attributes")

    def create_attribute(self, name, slug):
        return self.post("/products/attributes", json={"name": name, "slug": slug, "type": "select",
                                                       "order_by": "menu_order", "has_archives": False})

    # orders
    def get_order(self, oid):
        return self.get(f"/orders/{oid}")

    def list_orders(self, after=None, page=1, status="any"):
        p = {"per_page": 100, "page": page, "status": status, "orderby": "date", "order": "desc"}
        if after:
            p["modified_after"] = after
        return self.get("/orders", params=p)

    def create_order(self, data):
        return self.post("/orders", json=data)

    def update_order(self, oid, data):
        return self.put(f"/orders/{oid}", json=data)

    def send_order_details(self, oid, email: str | None = None):
        """Email the customer Woo's own invoice ("order details"): for an unpaid order it carries a Pay link."""
        return self.post(f"/orders/{oid}/actions/send_order_details", json={"email": email} if email else {})

    def add_order_note(self, oid, note: str, customer_note: bool = False):
        return self.post(f"/orders/{oid}/notes", json={"note": note, "customer_note": customer_note})

    def delete_order(self, oid):
        return self.delete(f"/orders/{oid}", params={"force": "true"})

    # webhooks
    def list_webhooks(self):
        return self.get("/webhooks", params={"per_page": 100})

    def create_webhook(self, name, topic, url, secret):
        return self.post("/webhooks", json={"name": name, "topic": topic, "delivery_url": url, "secret": secret,
                                            "status": "active"})

    def update_webhook(self, wid, data):
        return self.put(f"/webhooks/{wid}", json=data)

    # misc
    def system_status(self):
        return self.get("/system_status")

    def settings_group(self, group):
        return self.get(f"/settings/{group}")

    def update_setting(self, group, sid, value):
        return self.put(f"/settings/{group}/{sid}", json={"value": value})

    def payment_gateways(self):
        return self.get("/payment_gateways")

    def update_gateway(self, gid, data):
        return self.put(f"/payment_gateways/{gid}", json=data)
