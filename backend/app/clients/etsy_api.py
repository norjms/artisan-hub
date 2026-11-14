"""Etsy Open API v3 (services/stores.py is the only caller).

Credentials: an app at etsy.com/developers/your-apps gives a keystring and a shared secret. Every request sends
both in x-api-key ("keystring:secret"). The shop owner connects the hub once through OAuth 2.0 with PKCE; the
access token lasts an hour and is renewed with the refresh token (valid 90 days, replaced on each renewal). The
caller stores the tokens (save_tokens); this client only uses and renews them.
"""
import base64
import hashlib
import secrets
import time
from urllib.parse import urlencode

import httpx

from .base import ApiError, BaseClient

API = "https://api.etsy.com/v3/application"
TOKEN_URL = "https://api.etsy.com/v3/public/oauth/token"
CONNECT_URL = "https://www.etsy.com/oauth/connect"
SCOPES = "listings_r listings_w listings_d transactions_r transactions_w shops_r shops_w profile_r"


def pkce() -> tuple[str, str]:
    """(verifier, challenge) for the OAuth PKCE exchange."""
    verifier = secrets.token_urlsafe(48)[:64]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return verifier, challenge


def connect_url(keystring: str, redirect_uri: str, state: str, challenge: str) -> str:
    return CONNECT_URL + "?" + urlencode({"response_type": "code", "client_id": keystring, "redirect_uri": redirect_uri,
                                          "scope": SCOPES, "state": state, "code_challenge": challenge,
                                          "code_challenge_method": "S256"})


def exchange_code(keystring: str, redirect_uri: str, code: str, verifier: str) -> dict:
    r = httpx.post(TOKEN_URL, timeout=30.0, data={"grant_type": "authorization_code", "client_id": keystring,
                                                  "redirect_uri": redirect_uri, "code": code, "code_verifier": verifier})
    if r.status_code >= 400:
        raise ApiError("etsy", r.status_code, f"could not finish connecting: {r.text[:300]}")
    return _stamp(r.json())


def form(data: dict) -> dict:
    """Listing calls take form fields: lists as comma-separated text, booleans as true/false, no empty values."""
    out = {}
    for k, v in data.items():
        if v is None:
            continue
        if isinstance(v, bool):
            out[k] = "true" if v else "false"
        elif isinstance(v, (list, tuple)):
            out[k] = ",".join(str(x) for x in v)
        else:
            out[k] = str(v)
    return out


def _stamp(tok: dict) -> dict:
    tok["expires_at"] = time.time() + float(tok.get("expires_in") or 3600)
    return tok


class EtsyClient(BaseClient):
    service = "etsy"

    def __init__(self, keystring: str, shared_secret: str, tokens: dict | None, save_tokens=None, shop_id=None,
                 timeout=60.0):
        if not keystring or not shared_secret:
            raise ValueError("Etsy needs the app's keystring and shared secret")
        self.keystring, self.tokens, self.save_tokens = keystring, dict(tokens or {}), save_tokens
        self.shop_id = shop_id
        super().__init__(API, headers={"x-api-key": f"{keystring}:{shared_secret}"}, timeout=timeout)

    # ---------- auth ----------
    def access_token(self) -> str:
        t = self.tokens
        if not t.get("refresh_token"):
            raise ApiError(self.service, 401, "Etsy is not signed in yet: use Connect Etsy on the Connections page")
        if not t.get("access_token") or float(t.get("expires_at") or 0) - 120 < time.time():
            r = httpx.post(TOKEN_URL, timeout=30.0, data={"grant_type": "refresh_token", "client_id": self.keystring,
                                                          "refresh_token": t["refresh_token"]})
            if r.status_code >= 400:
                raise ApiError(self.service, 401, f"Etsy sign-in expired, connect Etsy again: {r.text[:200]}")
            self.tokens = _stamp({**t, **r.json()})
            if self.save_tokens:
                self.save_tokens(self.tokens)
        return self.tokens["access_token"]

    def request(self, method, path, retries: int = 4, **kw):
        kw["headers"] = {**(kw.get("headers") or {}), "Authorization": f"Bearer {self.access_token()}"}
        return super().request(method, path, retries=retries, **kw)

    def user_id(self) -> str:
        """The signed-in user's id: Etsy access tokens start with it."""
        return str(self.access_token().split(".", 1)[0])

    def shop(self) -> dict:
        """The signed-in owner's shop (its shop_id is what every shop call uses)."""
        r = self.get(f"/users/{self.user_id()}/shops")
        shop = r if isinstance(r, dict) and r.get("shop_id") else ((r or {}).get("results") or [None])[0]
        if not shop:
            raise ApiError(self.service, 404, "this Etsy account has no shop")
        return shop

    def sid(self):
        if not self.shop_id:
            self.shop_id = self.shop()["shop_id"]
        return self.shop_id

    # ---------- shop setup lists (for the settings pickers) ----------
    def shipping_profiles(self) -> list:
        return (self.get(f"/shops/{self.sid()}/shipping-profiles") or {}).get("results") or []

    def return_policies(self) -> list:
        return (self.get(f"/shops/{self.sid()}/policies/return") or {}).get("results") or []

    def readiness_states(self) -> list:
        return (self.get(f"/shops/{self.sid()}/readiness-state-definitions") or {}).get("results") or []

    def taxonomy(self) -> list:
        return (self.get("/seller-taxonomy/nodes") or {}).get("results") or []

    # ---------- listings ----------
    def create_draft_listing(self, data: dict) -> dict:
        return self.post(f"/shops/{self.sid()}/listings", data=form(data))

    def get_listing(self, listing_id) -> dict | None:
        try:
            return self.get(f"/listings/{listing_id}")
        except ApiError as e:
            if e.status == 404:
                return None
            raise

    def update_listing(self, listing_id, data: dict) -> dict:
        return self.patch(f"/shops/{self.sid()}/listings/{listing_id}", data=form(data))

    def delete_listing(self, listing_id):
        return self.delete(f"/listings/{listing_id}")

    def listing_images(self, listing_id) -> list:
        return (self.get(f"/listings/{listing_id}/images") or {}).get("results") or []

    def upload_image(self, listing_id, path, filename: str, rank: int, alt: str = "") -> dict:
        with open(path, "rb") as fh:
            files = {"image": (filename, fh, "image/jpeg")}
            data = {"rank": str(rank)}
            if alt:
                data["alt_text"] = alt[:500]
            return self.post(f"/shops/{self.sid()}/listings/{listing_id}/images", files=files, data=data)

    def delete_image(self, listing_id, image_id):
        return self.delete(f"/shops/{self.sid()}/listings/{listing_id}/images/{image_id}")

    def get_inventory(self, listing_id) -> dict:
        return self.get(f"/listings/{listing_id}/inventory")

    def update_inventory(self, listing_id, data: dict) -> dict:
        return self.put(f"/listings/{listing_id}/inventory", json=data)

    # ---------- orders (receipts) ----------
    def receipts(self, min_last_modified: int | None, offset: int = 0) -> list:
        p = {"limit": 100, "offset": offset, "sort_on": "updated", "sort_order": "asc"}
        if min_last_modified:
            p["min_last_modified"] = int(min_last_modified)
        return (self.get(f"/shops/{self.sid()}/receipts", params=p) or {}).get("results") or []

    def receipt(self, receipt_id) -> dict:
        return self.get(f"/shops/{self.sid()}/receipts/{receipt_id}")

    def ship_receipt(self, receipt_id, tracking_code: str | None = None, carrier: str | None = None) -> dict:
        body = {}
        if tracking_code:
            body["tracking_code"] = tracking_code
        if carrier:
            body["carrier_name"] = carrier
        return self.post(f"/shops/{self.sid()}/receipts/{receipt_id}/tracking", json=body)
