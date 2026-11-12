"""Files sent to the hub from OrcaSlicer in the browser ("Send to hub" in the desktop's Files).

The installer mounts the desktop's outbox in the hub's worker at HUB_ORCASLICER_OUTBOX:

    <outbox>/shared/          the shared desktop's "Send to hub": credited to "OrcaSlicer in the browser"
    <outbox>/people/<uid>/    a person's own desktop's: credited to that person

Who sent a file is told by the folder it is in, which only the installer decides (each desktop sees its own
folder and nothing else), never by anything inside it. A people/ folder that is not one of the hub's people
is left alone.

Each model file that has stopped changing becomes a new model in the library, as a draft with its licence
unknown, and leaves the outbox. Anything else moves into "Not sent" beside it, with a note saying why, so the
person sees it in the same folder.
"""
import logging
import shutil
import time
import uuid
from pathlib import Path

from sqlalchemy import select

from ..config import settings
from ..jobs import enqueue, event
from ..models import User
from . import importer

log = logging.getLogger("hub.orcaslicer_outbox")

NOT_SENT = "Not sent"
SHARED_LABEL = "OrcaSlicer in the browser"
# The files the library keeps from a slicer: projects and sliced plates, meshes and CAD, and G-code.
ACCEPTED = {".3mf", ".stl", ".step", ".stp", ".obj", ".gcode", ".bgcode"}
# The same ceiling as a file sent from the browser clipper.
MAX_BYTES = 500 * 1024 * 1024
# A file still being written is left for the next pass.
SETTLE_SECONDS = 5


def root() -> Path | None:
    value = settings().orcaslicer_outbox
    return Path(value) if value else None


def _senders(db, base: Path):
    """(folder, uid or None) for every outbox folder whose files may be taken in."""
    shared = base / "shared"
    if shared.is_dir():
        yield shared, None
    people = base / "people"
    if people.is_dir():
        known = {u for (u,) in db.execute(select(User.username).where(User.username.is_not(None)))}
        for folder in sorted(p for p in people.iterdir() if p.is_dir() and not p.name.startswith(".")):
            if folder.name in known:
                yield folder, folder.name


def _files(folder: Path):
    for p in sorted(folder.rglob("*")):
        rel = p.relative_to(folder).parts
        if rel[0] == NOT_SENT or any(part.startswith(".") for part in rel):
            continue
        if p.is_file() and not p.is_symlink():
            yield p


def _refuse(folder: Path, path: Path, reason: str) -> None:
    dest_dir = folder / NOT_SENT
    dest_dir.mkdir(exist_ok=True)
    dest = dest_dir / path.name
    if dest.exists():
        dest = dest_dir / f"{path.stem} ({time.strftime('%Y-%m-%d %H%M%S')}){path.suffix}"
    path.rename(dest)
    dest.with_name(dest.name + ".txt").write_text(f"{reason}\n", encoding="utf-8")


def _reason(path: Path, size: int) -> str | None:
    if path.suffix.lower() not in ACCEPTED:
        return ("Not sent: the hub keeps 3MF, STL, STEP, OBJ and G-code files from OrcaSlicer, "
                f"and this is a {path.suffix or 'file without an extension'}.")
    if size == 0:
        return "Not sent: the file is empty."
    if size > MAX_BYTES:
        return f"Not sent: the file is {size // 1048576} MB, over the hub's {MAX_BYTES // 1048576} MB limit."
    return None


def take_in(db, now: float | None = None) -> dict:
    """One pass over the outbox. Returns counts for the job's log."""
    base = root()
    if base is None or not base.is_dir():
        return {"skipped": True}
    now = time.time() if now is None else now
    imported = refused = waiting = 0
    staging_root = Path(settings().data_dir) / "orcaslicer-import"
    for folder, uid in _senders(db, base):
        who = uid or SHARED_LABEL
        for path in _files(folder):
            st = path.stat()
            if now - st.st_mtime < SETTLE_SECONDS:
                waiting += 1
                continue
            reason = _reason(path, st.st_size)
            if reason:
                _refuse(folder, path, reason)
                refused += 1
                continue
            staging = staging_root / uuid.uuid4().hex
            staging.mkdir(parents=True)
            moved = staging / path.name
            shutil.move(str(path), moved)
            try:
                with db.begin_nested():
                    model = importer.import_generic_folder(
                        db, staging, {"name": Path(path.name.removesuffix(".gcode.3mf")).stem,
                                      "license": "UNKNOWN", "source": "own"})
                    db.flush()
            except Exception as e:  # noqa: BLE001  (a file the hub cannot read goes back with the reason)
                log.warning("outbox file %s not taken in: %s", path.name, e)
                shutil.move(str(moved), path)
                _refuse(folder, path, "Not sent: the hub could not read this file. "
                                      "Save it again from OrcaSlicer, or upload it to the hub from your computer.")
                refused += 1
            else:
                actor = None if uid is None else uid
                event(db, f"model:{model.id}", f"Sent from OrcaSlicer in the browser by {who}", actor=actor)
                enqueue(db, "dam.sync_model", ref=f"model:{model.id}:dam", payload={"model_id": model.id}, delay=2)
                imported += 1
            finally:
                shutil.rmtree(staging, ignore_errors=True)
    if imported:
        enqueue(db, "manyfold.rescan", ref="manyfold:rescan", delay=60)
    return {"imported": imported, "refused": refused, "waiting": waiting}
