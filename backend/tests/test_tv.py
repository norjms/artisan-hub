"""TV sign-in: start, approve, collect the key once; the wall feed."""
from test_mobile_api import H, _client, _key


def test_a_tv_signs_in_with_a_code_and_gets_a_view_only_key_once():
    c, key = _client(), _key(["admin"])
    s = c.post("/api/mobile/v1/tv/start", json={"device": "Apple TV"}).json()
    assert len(s["user_code"]) == 9 and s["user_code"][4] == "-" and s["interval"] == 5
    assert not set(s["user_code"].replace("-", "")) & set("01OIL")
    poll = lambda: c.post("/api/mobile/v1/tv/poll", json={"device_code": s["device_code"]})  # noqa: E731
    assert poll().json()["state"] == "pending"
    assert c.post("/api/mobile/v1/tv/approve", json={"code": "zzzz-zzzz"}, headers=H(key)).status_code == 404
    a = c.post("/api/mobile/v1/tv/approve", json={"code": s["user_code"].lower().replace("-", " "), "name": "Print room"},
               headers=H(key))
    assert a.status_code == 200, a.text
    r = poll().json()
    assert r["state"] == "ready" and r["key"] and r["name"] == "Print room"
    assert poll().status_code == 410                                   # collected once
    tv = {"Authorization": f"Bearer {r['key']}"}
    w = c.get("/api/mobile/v1/wall", headers=tv)
    assert w.status_code == 200 and "tiles" in w.json() and "printers" in w.json()
    assert c.patch("/api/mobile/v1/spools/1", headers=tv, json={"comment": "x"}).status_code == 403   # view only


def test_poll_needs_a_real_code():
    c = _client()
    assert c.post("/api/mobile/v1/tv/poll", json={"device_code": "nope"}).status_code == 404
