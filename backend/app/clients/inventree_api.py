from .base import BaseClient


class InvenTreeClient(BaseClient):
    service = "inventree"

    def __init__(self, url, host, token):
        h = {"Authorization": f"Token {token}", "Accept": "application/json"}
        if host:
            h["Host"] = host
            h["X-Forwarded-Proto"] = "https"
        super().__init__(url, headers=h, timeout=60.0)

    def _all(self, path, params=None):
        params = dict(params or {})
        params.setdefault("limit", 500)
        out, offset = [], 0
        while True:
            params["offset"] = offset
            r = self.get(path, params=params)
            if isinstance(r, list):
                return r
            out += r["results"]
            offset += len(r["results"])
            if not r.get("next") or not r["results"]:
                return out

    def info(self):
        return self.get("/api/")

    # parts
    def parts(self, **params):
        return self._all("/api/part/", params)

    def part(self, pk):
        return self.get(f"/api/part/{pk}/")

    def create_part(self, data):
        return self.post("/api/part/", json=data)

    def update_part(self, pk, data):
        return self.patch(f"/api/part/{pk}/", json=data)

    def categories(self):
        return self._all("/api/part/category/")

    def create_category(self, name, parent=None, description=""):
        return self.post("/api/part/category/", json={"name": name, "parent": parent, "description": description})

    # stock
    def stock(self, **params):
        return self._all("/api/stock/", params)

    def locations(self):
        return self._all("/api/stock/location/")

    def create_location(self, name, parent=None, description=""):
        return self.post("/api/stock/location/", json={"name": name, "parent": parent, "description": description})

    def add_stock(self, part, qty, location=None, notes=""):
        return self.post("/api/stock/", json={"part": part, "quantity": qty, "location": location, "notes": notes})

    def remove_stock(self, items: list[dict], notes=""):
        """items: [{"pk": stock_item_id, "quantity": n}]"""
        return self.post("/api/stock/remove/", json={"items": items, "notes": notes})

    # suppliers
    def companies(self, **params):
        return self._all("/api/company/", params)

    def create_company(self, name, website="", is_supplier=True):
        return self.post("/api/company/", json={"name": name, "website": website, "is_supplier": is_supplier,
                                                "is_customer": False, "is_manufacturer": False})

    def supplier_parts(self, **params):
        return self._all("/api/company/part/", params)

    def create_supplier_part(self, data):
        return self.post("/api/company/part/", json=data)

    # purchasing
    def create_purchase_order(self, supplier, reference=None, description=""):
        body = {"supplier": supplier, "description": description}
        if reference:
            body["reference"] = reference
        return self.post("/api/order/po/", json=body)

    def add_po_line(self, order, supplier_part, qty):
        return self.post("/api/order/po-line/", json={"order": order, "part": supplier_part, "quantity": qty})
