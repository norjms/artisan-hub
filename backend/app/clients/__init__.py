"""Service clients for the current team. Each team has its own connection settings (services.connections);
clients are cached per team and rebuilt when the team changes its settings. The slicer is shared by all teams
unless a team connects its own."""
import threading
from functools import lru_cache

from ..config import settings

_clients: dict = {}
_lock = threading.Lock()


def _cfg(kind: str) -> dict:
    from ..services.connections import get
    return get(kind)


def _cached(kind: str, cfg: dict, build):
    from ..tenant import current_team_id
    key = (current_team_id(), kind)
    ver = cfg.get("_version")
    with _lock:
        hit = _clients.get(key)
        if hit and hit[0] == ver:
            return hit[1]
    c = build()
    with _lock:
        _clients[key] = (ver, c)
    return c


def woo():
    from .woo_api import WooClient
    c = _cfg("woo")
    return _cached("woo", c, lambda: WooClient(c.get("url"), c.get("key"), c.get("secret"), verify=c.get("verify_tls", True)))


def dam():
    from .resourcespace import ResourceSpaceClient
    c = _cfg("dam")
    return _cached("dam", c, lambda: ResourceSpaceClient(c.get("url"), c.get("host"), c.get("user"), c.get("key")))


def manyfold():
    from .manyfold_api import ManyfoldClient
    c = _cfg("manyfold")
    return _cached("manyfold", c, lambda: ManyfoldClient(c.get("url"), c.get("client_id"), c.get("client_secret"), c.get("public_url")))


def legacy_farm():
    from .legacy_farm_api import LegacyFarmClient
    c = _cfg("legacy_farm")
    return _cached("legacy_farm", c, lambda: LegacyFarmClient(c.get("url"), c.get("api_key"), c.get("username"), c.get("password")))


def openprinthq():
    from .openprinthq_api import OpenPrintHQClient
    c = _cfg("openprinthq")
    return _cached("openprinthq", c, lambda: OpenPrintHQClient(c.get("url"), c.get("api_key"), c.get("target_group"),
                                                              verify=c.get("verify_tls", True)))


FARM_KINDS = ("legacy_farm", "openprinthq")   # farm apps the hub has a driver for (clients/farm_base.py)


def farm_kind(team_id: int | None = None) -> str:
    """The installation's print farm. It is chosen once, explicitly, at first-run setup (invariant 1) and never
    inferred. Only an installation that predates the choice and could not be migrated unambiguously ("legacy")
    still infers per team, as it always did, until an admin chooses."""
    from ..services import installation
    st = installation.state()
    if st["farm"]:
        return st["farm"]
    if st["farm_source"] == "legacy":
        return _inferred_farm_kind(team_id)
    raise installation.FarmNotChosen()


def _inferred_farm_kind(team_id: int | None = None) -> str:
    """The old rule, for legacy installations only: OpenPrintHQ when it is the only one connected, or is marked
    use_as_farm; otherwise Legacy farm."""
    from ..services.connections import get, is_connected
    if is_connected("openprinthq", team_id):
        if not is_connected("legacy_farm", team_id):
            return "openprinthq"
        if (get("openprinthq", team_id, required=False) or {}).get("use_as_farm"):
            return "openprinthq"
    return "legacy_farm"


def farm():
    """The team's print farm, as the farm interface (clients/farm_base.py). Use this, not legacy_farm(), for farm work."""
    return openprinthq() if farm_kind() == "openprinthq" else legacy_farm()


def farm_connected(team_id: int | None = None) -> bool:
    from ..services.connections import is_connected
    from ..services.installation import FarmNotChosen
    try:
        return is_connected(farm_kind(team_id), team_id)
    except FarmNotChosen:
        return False


def inventree():
    from .inventree_api import InvenTreeClient
    c = _cfg("inventree")
    return _cached("inventree", c, lambda: InvenTreeClient(c.get("url"), c.get("host"), c.get("token")))


def spoolman():
    from .spoolman_api import SpoolmanClient
    c = _cfg("spoolman")
    return _cached("spoolman", c, lambda: SpoolmanClient(c.get("url")))


def public_url(kind: str, field: str = "public_url") -> str | None:
    """A team's public link for a service (for links in the UI), or None."""
    from ..services.connections import get
    c = get(kind, required=False) or {}
    return c.get(field) or c.get("url")


@lru_cache
def _shared_slicer():
    from .slicer_api import SlicerClient
    s = settings()
    return SlicerClient(s.slicer_url, s.slicer_token)


def slicer():
    """The team's own slicer service when it set one up (Connections), else the hub's shared one (env)."""
    from .slicer_api import SlicerClient
    from ..tenant import current_team_id
    c = _cfg_optional("slicer") if current_team_id() else None
    if c and c.get("url"):
        return _cached("slicer", c, lambda: SlicerClient(c.get("url"), c.get("token") or ""))
    return _shared_slicer()


def slicer_configured() -> bool:
    from ..tenant import current_team_id
    c = _cfg_optional("slicer") if current_team_id() else None
    return bool((c and c.get("url")) or settings().slicer_url)


def _cfg_optional(kind: str) -> dict | None:
    from ..services.connections import get
    return get(kind, required=False)
