"""Shopify and Etsy beside WooCommerce (services/stores.py): pushing products, one shared stock pool, orders in,
shipping out, webhooks and the Etsy sign-in. The stores themselves are fakes."""
import base64
import hashlib
import hmac
import json
import secrets as _s
import time

import pytest
from sqlalchemy import create_engine, inspect, text


class FakeShopify:
    def __init__(self):
        self.sets, self.quantities, self.statuses, self.fulfilled, self.notes, self.hooks = [], [], [], [], [], []
        self.orders = {}

    def product_set(self, data, gid=None):
        self.sets.append((data, gid))
        media = [{"id": f.get("id") or f"gid://shopify/MediaImage/{i + 1}"} for i, f in enumerate(data["files"])]
        nodes = [{"id": f"gid://shopify/ProductVariant/{i + 1}", "sku": v["sku"],
                  "inventoryItem": {"id": f"gid://shopify/InventoryItem/{v['sku']}"}} for i, v in enumerate(data["variants"])]
        return {"id": gid or "gid://shopify/Product/77", "handle": "vase", "status": data["status"],
                "onlineStoreUrl": "https://shop.example/products/vase", "media": {"nodes": media},
                "variants": {"nodes": nodes}}

    def find_product_by_sku(self, sku):
        return None

    def product_exists(self, gid):
        return True

    def primary_location(self):
        return "gid://shopify/Location/1"

    def stock_at(self, loc, items):
        pass

    def set_quantities(self, loc, qty, key):
        self.quantities.append(dict(qty))

    def set_status(self, gid, status):
        self.statuses.append(status)
        return {"id": gid, "status": status}

    def get_order(self, gid):
        return self.orders.get(gid)

    def orders_updated_since(self, since, after=None):
        return list(self.orders.values()), None

    def fulfill_order(self, gid, tracking=None, notify=True):
        self.fulfilled.append((gid, tracking))
        return 1

    def add_order_note(self, gid, text_):
        self.notes.append(text_)

    def webhooks(self):
        return self.hooks

    def add_webhook(self, topic, uri):
        self.hooks.append({"topic": topic, "uri": uri})


class FakeEtsy:
    def __init__(self):
        self.listings, self.inventory, self.images, self.updates, self.shipped = {}, {}, [], [], []
        self.receipts_ = {}

    next_id = 555

    def create_draft_listing(self, data):
        lid = FakeEtsy.next_id
        FakeEtsy.next_id += 1
        self.last = str(lid)
        self.listings[self.last] = {"listing_id": lid, "state": "draft", **data}
        return self.listings[self.last]

    def get_listing(self, lid):
        return self.listings.get(str(lid))

    def update_listing(self, lid, data):
        self.updates.append(data)
        self.listings[str(lid)].update(data)
        return self.listings[str(lid)]

    def upload_image(self, lid, path, filename, rank, alt=""):
        self.images.append(filename)
        return {"listing_image_id": 9000 + len(self.images)}

    def delete_image(self, lid, iid):
        pass

    def update_inventory(self, lid, data):
        self.inventory[str(lid)] = data
        return {"products": [{"sku": p["sku"], "product_id": 7000 + i} for i, p in enumerate(data["products"])]}

    def receipt(self, rid):
        return self.receipts_[str(rid)]

    def receipts(self, since, offset=0):
        return list(self.receipts_.values())

    def ship_receipt(self, rid, code=None, carrier=None):
        self.shipped.append((rid, code, carrier))
        return {}


@pytest.fixture
def shops(env, monkeypatch):
    from app.db import session_scope
    from app.services import connections, stores
    fakes = {"shopify": FakeShopify(), "etsy": FakeEtsy()}
    with session_scope() as db:
        connections.save(db, "shopify", {"shop": "vase-shop"}, {"client_id": "cid", "client_secret": "shpss_test"})
        connections.save(db, "etsy", {"taxonomy_id": "1234", "shipping_profile_id": "55", "readiness_state_id": "66"},
                         {"keystring": "ks", "shared_secret": "ss", "webhook_secret": "whsec_" + base64.b64encode(b"etsy-key").decode()})
    monkeypatch.setattr(stores, "_etsy_tokens", lambda team_id=None: {"refresh_token": "r", "access_token": "1.a"})
    monkeypatch.setattr(stores, "client", lambda ch: fakes[ch])
    yield fakes
    from app.models import Connection
    with session_scope() as db:
        for row in db.query(Connection).filter(Connection.kind.in_(("shopify", "etsy"))):
            db.delete(row)
    connections.forget(1)


def _product(env, colors=("Red", "Blue"), live=False):
    from PIL import Image as PImage
    from app.db import session_scope
    from app.models import Image, Model, Product, Variant
    tag = _s.token_hex(3)
    rel = f"originals/st-{tag}.png"
    p = env / "data" / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    PImage.new("RGB", (40, 30), (10, 200, 10)).save(p)
    sha = hashlib.sha256(p.read_bytes()).hexdigest()
    with session_scope() as db:
        m = Model(code=f"ST-{tag}", name=f"Vase {tag}", slug=f"vase-{tag}", license_ok=True, tags=["vase", "home & decor"])
        db.add(m)
        db.flush()
        db.add(Image(model_id=m.id, sha256=sha, filename="a.png", role="hero", store_rel=rel))
        pr = Product(model_id=m.id, sku=f"ST-{tag}", title=f"Spiral vase: {tag} & more & more", price=20.0, live=live,
                     description="A vase.\n\nPrinted in PLA.", color_mode="options" if colors else "single", weight_g=120)
        db.add(pr)
        db.flush()
        for i, c in enumerate(colors):
            db.add(Variant(product_id=pr.id, sku=f"ST-{tag}-{c[:3].upper()}", color_name=c, sort=i))
        return pr.id


def test_push_to_shopify_and_etsy(env, shops):
    from app.db import session_scope
    from app.models import Product, StoreListing
    from app.services import stores
    pid = _product(env)
    with session_scope() as db:
        out = stores.push_product(db, pid, "shopify")
        assert out["ext_id"] == "gid://shopify/Product/77" and out["status"] == "draft"
        data, gid = shops["shopify"].sets[-1]
        assert data["status"] == "DRAFT" and [v["optionValues"][0]["name"] for v in data["variants"]] == ["Red", "Blue"]
        assert data["variants"][0]["inventoryItem"]["tracked"] is True
        assert data["files"][0]["originalSource"].startswith("http://test/public/img/")
        # no bill of materials yet: each color shows the stock cap, pushed to the location
        assert list(shops["shopify"].quantities[-1].values()) == [10, 10]

        out = stores.push_product(db, pid, "etsy")
        lst = shops["etsy"].listings[shops["etsy"].last]
        assert lst["taxonomy_id"] == 1234 and lst["who_made"] == "i_did" and lst["when_made"] == "made_to_order"
        assert lst["title"].count("&") == 1 and ":" in lst["title"]          # Etsy allows & and : once
        assert lst["description"].startswith("A vase.") and "<" not in lst["description"]
        inv = shops["etsy"].inventory[shops["etsy"].last]
        assert [p["property_values"][0]["values"] for p in inv["products"]] == [["Red"], ["Blue"]]
        assert inv["quantity_on_property"] == [stores.ETSY_COLOR] and inv["products"][0]["offerings"][0]["quantity"] == 10
        assert len(shops["etsy"].images) == 1 and out["status"] == "draft"
        p = db.get(Product, pid)
        rows = {r.channel: r for r in db.query(StoreListing).filter_by(product_id=pid)}
        assert rows["etsy"].refs["variants"] and rows["shopify"].refs["items"]
        # pushing again reuses the uploaded picture
        stores.push_product(db, pid, "etsy")
        assert len(shops["etsy"].images) == 1
        assert p.id == pid


def test_going_live_and_hiding_on_each_store(env, shops):
    from app.db import session_scope
    from app.models import Product
    from app.services import stores
    pid = _product(env, colors=())
    with session_scope() as db:
        stores.push_product(db, pid, "shopify")
        stores.push_product(db, pid, "etsy")
        assert stores.set_live(db, pid, "etsy", True)["status"] == "active"
        assert stores.set_live(db, pid, "shopify", True)["status"] == "active"
        assert db.get(Product, pid).live and shops["shopify"].statuses[-1] == "ACTIVE"
        # off Etsy only: inactive there, still live elsewhere
        stores.set_live(db, pid, "etsy", False, this_store_only=True)
        assert shops["etsy"].listings[shops["etsy"].last]["state"] == "inactive" and db.get(Product, pid).live


def test_etsy_needs_its_listing_defaults(env, shops):
    from app.db import session_scope
    from app.jobs import PermanentError
    from app.models import Product
    from app.services import connections, stores
    pid = _product(env)
    with session_scope() as db:
        connections.save(db, "etsy", {"shipping_profile_id": ""}, None)
    with session_scope() as db:
        with pytest.raises(PermanentError, match="shipping profile"):
            stores.push_product(db, pid, "etsy")
        # a per-product override fills the gap
        row = stores.listing(db, db.get(Product, pid), "etsy", create=True)
        row.options = {"shipping_profile_id": 77}
        stores.push_product(db, pid, "etsy")
        assert shops["etsy"].listings[shops["etsy"].last]["shipping_profile_id"] == 77


def test_one_store_can_be_left_out(env, shops):
    from fastapi.testclient import TestClient
    from app.db import session_scope
    from app.main import app
    from app.models import Product
    from app.services import stores
    pid = _product(env)
    c = TestClient(app)
    r = c.put(f"/api/products/{pid}/stores/etsy", json={"enabled": False})
    assert r.status_code == 200
    assert next(s for s in r.json()["stores"] if s["channel"] == "etsy")["enabled"] is False
    with session_scope() as db:
        t = stores.targets(db, db.get(Product, pid))
        assert "shopify" in t and "etsy" not in t
        assert stores.push_product(db, pid, "etsy") == {"skipped": "not sold on Etsy"}
    assert c.put(f"/api/products/{pid}/stores/ebay", json={"enabled": True}).status_code == 400


def test_shopify_and_etsy_orders_come_in_and_match_products(env, shops, monkeypatch):
    from app.db import session_scope
    from app.models import Order, Product
    from app.services import stores
    pid = _product(env)
    with session_scope() as db:
        p = db.get(Product, pid)
        red, blue = p.variants[0], p.variants[1]
        red_sku, blue_id = red.sku, blue.id
        stores.push_product(db, pid, "etsy")
    gid = "gid://shopify/Order/4001"
    shops["shopify"].orders[gid] = {
        "id": gid, "legacyResourceId": "4001", "name": "#1001", "createdAt": "2026-11-13T10:00:00Z", "cancelledAt": None,
        "test": False, "displayFinancialStatus": "PAID", "displayFulfillmentStatus": "UNFULFILLED",
        "totalPriceSet": {"shopMoney": {"amount": "40.00", "currencyCode": "USD"}}, "customer": {"displayName": "Ann Lee"},
        "lineItems": {"nodes": [{"id": "gid://shopify/LineItem/1", "sku": red_sku, "name": "Vase - Red", "quantity": 2,
                                 "variant": {"id": "x"}, "product": {"id": "y"}}]}}
    shops["etsy"].receipts_["3001"] = {
        "receipt_id": 3001, "status": "paid", "is_paid": True, "is_shipped": False, "name": "Bo Diaz",
        "grandtotal": {"amount": 2000, "divisor": 100, "currency_code": "USD"}, "create_timestamp": 1763000000,
        "transactions": [{"transaction_id": 81, "listing_id": int(shops["etsy"].last), "product_id": 7001, "sku": "", "quantity": 1,
                          "title": "Vase"}]}
    with session_scope() as db:
        assert stores.ingest_order(db, "shopify", gid)["status"] == "processing"
        assert stores.ingest_order(db, "etsy", "3001")["status"] == "processing"
        s = db.query(Order).filter_by(channel="shopify", ext_id=gid).one()
        assert s.number == "1001" and s.customer == "Ann Lee" and s.total == 40.0 and s.lines[0].qty == 2
        assert s.lines[0].product_id == pid and s.lines[0].variant_id is not None
        e = db.query(Order).filter_by(channel="etsy", ext_id="3001").one()
        # no SKU on the Etsy line: matched through the listing and its variation's product id
        assert e.lines[0].product_id == pid and e.lines[0].variant_id == blue_id and e.total == 20.0
        # the same order again updates it, never duplicates it
        shops["shopify"].orders[gid]["displayFulfillmentStatus"] = "FULFILLED"
        stores.ingest_order(db, "shopify", gid)
        assert db.query(Order).filter_by(channel="shopify", ext_id=gid).count() == 1
        assert db.query(Order).filter_by(channel="shopify", ext_id=gid).one().status == "completed"
        assert stores.poll_orders(db, "etsy") == {"orders": 1}


def test_status_mapping():
    from app.services.stores import etsy_status, shopify_status
    assert shopify_status({"displayFinancialStatus": "PAID", "displayFulfillmentStatus": "UNFULFILLED"}) == "processing"
    assert shopify_status({"displayFinancialStatus": "AUTHORIZED"}) == "on-hold"
    assert shopify_status({"displayFinancialStatus": "PENDING"}) == "pending"
    assert shopify_status({"displayFinancialStatus": "PAID", "cancelledAt": "2026-11-01"}) == "cancelled"
    assert shopify_status({"displayFinancialStatus": "REFUNDED"}) == "refunded"
    assert shopify_status({"displayFinancialStatus": "PAID", "displayFulfillmentStatus": "FULFILLED"}) == "completed"
    assert etsy_status({"status": "paid", "is_paid": True}) == "processing"
    assert etsy_status({"status": "completed", "is_shipped": True}) == "completed"
    assert etsy_status({"status": "canceled"}) == "cancelled"
    assert etsy_status({"status": "payment processing"}) == "pending"
    assert etsy_status({"status": "fully refunded"}) == "refunded"


def test_stock_is_one_pool_pushed_where_it_changed(env, shops, monkeypatch):
    from app.db import session_scope
    from app.models import Product
    from app.services import availability, stores
    pid = _product(env, colors=())
    with session_scope() as db:
        stores.push_product(db, pid, "shopify")
        stores.push_product(db, pid, "etsy")
        n = len(shops["shopify"].quantities)
        assert stores.sync_stock(db, "shopify", pid)["changed"] == 0          # nothing changed: nothing sent
        assert len(shops["shopify"].quantities) == n
        monkeypatch.setattr(availability, "stock_for_woo", lambda p, r: {"p": 3})
        assert stores.sync_stock(db, None, pid)["changed"] == 2              # both stores get the same number
        assert shops["shopify"].quantities[-1] == {"gid://shopify/InventoryItem/" + db.get(Product, pid).sku: 3}
        assert shops["etsy"].inventory[shops["etsy"].last]["products"][0]["offerings"][0]["quantity"] == 3


def test_marking_shipped_fulfils_in_the_store(env, shops):
    from fastapi.testclient import TestClient
    from app.db import session_scope
    from app.main import app
    from app.models import Order
    with session_scope() as db:
        s = Order(channel="shopify", ext_id=f"gid://shopify/Order/{_s.randbelow(10**6)}", number="1002", status="processing")
        e = Order(channel="etsy", ext_id=str(_s.randbelow(10**6)), number="E1", status="processing")
        db.add_all([s, e])
        db.flush()
        sid, eid, sext, eext = s.id, e.id, s.ext_id, e.ext_id
    c = TestClient(app)
    r = c.post(f"/api/orders/{sid}/ship", json={"company": "USPS", "number": "9400"})
    assert r.status_code == 200 and r.json()["status"] == "completed"
    assert shops["shopify"].fulfilled[-1] == (sext, {"company": "USPS", "number": "9400"})
    assert c.post(f"/api/orders/{eid}/ship", json={}).status_code == 200
    assert shops["etsy"].shipped[-1] == (eext, None, None)
    with session_scope() as db:
        assert db.get(Order, eid).status == "completed"
    rows = {o["id"]: o for o in c.get("/api/orders").json()}
    assert rows[sid]["store"] == "Shopify" and "admin.shopify.com/store/vase-shop/orders/" in rows[sid]["store_url"]
    assert rows[eid]["store_url"].endswith(f"order_id={eext}")


def test_webhooks_are_signed(env, shops):
    from fastapi.testclient import TestClient
    from app.db import session_scope
    from app.main import app
    from app.models import Job
    c = TestClient(app)
    body = json.dumps({"id": 5005, "admin_graphql_api_id": "gid://shopify/Order/5005"}).encode()
    good = base64.b64encode(hmac.new(b"shpss_test", body, hashlib.sha256).digest()).decode()
    assert c.post("/hooks/shopify", content=body, headers={"x-shopify-hmac-sha256": "bad", "x-shopify-topic": "orders/create"}).status_code == 401
    assert c.post("/hooks/shopify", content=body, headers={"x-shopify-hmac-sha256": good, "x-shopify-topic": "orders/create"}).status_code == 200
    body = json.dumps({"event_type": "order.paid", "shop_id": 1,
                       "resource_url": "https://api.etsy.com/v3/application/shops/1/receipts/6006"}).encode()
    ts, wid = str(int(time.time())), "msg_1"
    sig = base64.b64encode(hmac.new(b"etsy-key", f"{wid}.{ts}.".encode() + body, hashlib.sha256).digest()).decode()
    h = {"webhook-id": wid, "webhook-timestamp": ts, "webhook-signature": f"v1,bogus v1,{sig}"}
    assert c.post("/hooks/etsy", content=body, headers={**h, "webhook-signature": "v1,bogus"}).status_code == 401
    assert c.post("/hooks/etsy", content=body, headers={**h, "webhook-timestamp": str(int(time.time()) - 3600)}).status_code == 401
    assert c.post("/hooks/etsy", content=body, headers=h).status_code == 200
    with session_scope() as db:
        payloads = [j.payload for j in db.query(Job).filter_by(kind="stores.ingest_order")]
    assert {"channel": "shopify", "ext_id": "gid://shopify/Order/5005"} in payloads
    assert {"channel": "etsy", "ext_id": "6006"} in payloads


def test_etsy_sign_in(env, shops, monkeypatch):
    from urllib.parse import parse_qs, urlparse
    from app.clients import etsy_api
    from app.db import session_scope
    from app.kv import setting
    from app.services import stores
    from app.services.connections import decrypt
    with session_scope() as db:
        url = stores.etsy_connect(db)
    q = parse_qs(urlparse(url).query)
    assert q["client_id"] == ["ks"] and q["code_challenge_method"] == ["S256"] and q["redirect_uri"] == ["http://test/oauth/etsy/callback"]
    state = q["state"][0]
    assert state.startswith("1.")
    monkeypatch.setattr(etsy_api, "exchange_code", lambda ks, ru, code, v: {"access_token": "12.tok", "refresh_token": "rr", "expires_at": 0})
    monkeypatch.setattr(etsy_api.EtsyClient, "shop", lambda self: {"shop_id": 42, "shop_name": "VaseCo"})
    with session_scope() as db:
        with pytest.raises(Exception, match="expired"):
            stores.etsy_finish(db, "1.wrong", "code")
    with session_scope() as db:
        assert stores.etsy_finish(db, state, "code")["shop_name"] == "VaseCo"
    with session_scope() as db:
        assert decrypt(setting(db, stores.ETSY_TOKENS))["refresh_token"] == "rr"
        assert setting(db, stores.ETSY_SHOP)["shop_id"] == 42
        stores.etsy_disconnect(db)


def test_etsy_text_rules():
    from app.services.stores import etsy_tags, etsy_title
    assert etsy_title("Vase: big & bold & tall: 100% + 5% + more") == "Vase: big & bold tall 100% + 5 more"
    assert len(etsy_title("x" * 300)) == 140
    tags = etsy_tags(["Home & Decor", "vase", "VASE", "a-very-long-tag-name-over-twenty"] + [f"t{i}" for i in range(20)])
    assert tags[0] == "Home Decor" and tags.count("vase") == 1 and len(tags[2]) <= 20 and len(tags) == 13


def test_existing_orders_become_woo_orders(tmp_path):
    from app.db import migrate_stores
    eng = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with eng.begin() as c:
        c.execute(text("CREATE TABLE shop_order (id INTEGER PRIMARY KEY, team_id INTEGER, woo_order_id INTEGER NOT NULL)"))
        c.execute(text("CREATE TABLE shop_order_line (id INTEGER PRIMARY KEY, woo_line_id INTEGER)"))
        c.execute(text("INSERT INTO shop_order VALUES (1, 1, 501), (2, 1, 502)"))
    with eng.begin() as c:
        migrate_stores(c)
        migrate_stores(c)
    with eng.connect() as c:
        assert c.execute(text("SELECT channel, ext_id FROM shop_order ORDER BY id")).all() == [("woo", "501"), ("woo", "502")]
    assert "ext_line_id" in {x["name"] for x in inspect(eng).get_columns("shop_order_line")}
