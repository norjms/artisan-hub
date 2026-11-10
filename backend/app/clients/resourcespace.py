"""ResourceSpace 11 API client (signed userkey auth)."""
import hashlib
import json
from urllib.parse import urlencode

from .base import BaseClient, ApiError


class ResourceSpaceClient(BaseClient):
    service = "resourcespace"

    def __init__(self, url, host, user, key):
        super().__init__(url, headers={"Host": host} if host else {}, timeout=300.0)
        self.user = user
        self.key = key

    def _q(self, function, params):
        q = {"user": self.user, "function": function}
        for i, v in enumerate(params, start=1):
            if isinstance(v, bool):
                v = "1" if v else "0"
            elif isinstance(v, (dict, list)):
                v = json.dumps(v)
            elif v is None:
                v = ""
            q[f"param{i}"] = str(v)
        qs = urlencode(q)
        sign = hashlib.sha256((self.key + qs).encode()).hexdigest()
        return qs, sign

    def call(self, function, *params):
        qs, sign = self._q(function, params)
        r = self.request("POST", "/api/", data={"query": qs, "sign": sign, "user": self.user}, expect_json=False)
        return self._decode(function, r)

    def _decode(self, function, r):
        txt = r.text.strip()
        if txt.startswith("Invalid signature"):
            raise ApiError(self.service, r.status_code, "invalid signature")
        try:
            return json.loads(txt)
        except ValueError:
            return txt

    def upload(self, ref: int, path: str, filename: str):
        qs, sign = self._q("upload_multipart", [ref, False, False, False, 0, True])
        with open(path, "rb") as fh:
            r = self.request("POST", "/api/", data={"query": qs, "sign": sign, "user": self.user},
                             files={"file": (filename, fh)}, expect_json=False, retries=2)
        out = self._decode("upload_multipart", r)
        if isinstance(out, dict) and out.get("status") is False:
            raise ApiError(self.service, r.status_code, json.dumps(out)[:400])
        return out

    # convenience wrappers
    def create_resource(self, resource_type=1, metadata=None, archive=0):
        ref = self.call("create_resource", resource_type, archive, "", False, False, True, metadata or {})
        try:
            return int(ref)
        except (TypeError, ValueError):
            raise ApiError(self.service, None, f"create_resource failed: {str(ref)[:200]}")

    def has_file(self, ref) -> bool:
        """True when the resource has an uploaded original (file_extension is set)."""
        d = self.call("get_resource_data", ref)
        return isinstance(d, dict) and bool(d.get("file_extension"))

    def update_field(self, ref, field, value):
        return self.call("update_field", ref, field, value)

    def call_named(self, function, params: list[tuple]):
        """Named-parameter call; needed for array params (e.g. coldata[type]=3)."""
        q = [("user", self.user), ("function", function)] + [(k, "1" if v is True else "0" if v is False else str(v))
                                                              for k, v in params]
        qs = urlencode(q)
        sign = hashlib.sha256((self.key + qs).encode()).hexdigest()
        r = self.request("POST", "/api/", data={"query": qs, "sign": sign, "user": self.user}, expect_json=False)
        return self._decode(function, r)

    def save_collection(self, ref, coldata: dict):
        out = self.call_named("save_collection", [("ref", ref)] + [(f"coldata[{k}]", v) for k, v in coldata.items()])
        if out is not True:
            raise ApiError(self.service, None, f"save_collection {ref} failed: {str(out)[:200]}")
        return out

    def create_collection(self, name):
        return int(self.call("create_collection", name))

    def add_to_collection(self, ref, collection):
        return self.call("add_resource_to_collection", ref, collection)

    def delete_resource(self, ref):
        return self.call("delete_resource", ref)

    def resource_url(self, ref, size="scr", ext="jpg"):
        # get_resource_path(ref, getfilepath, size, generate, extension)
        return self.call("get_resource_path", ref, False, size, False, ext)

    def fields(self):
        return self.call("get_resource_type_fields")

    def create_field(self, name, resource_types="1", ftype=0):
        return self.call("create_resource_type_field", name, resource_types, ftype)

    def search(self, query, restypes="", rows=50):
        return self.call("do_search", query, restypes, "relevance", 0, rows)
