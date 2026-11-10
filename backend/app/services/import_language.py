"""English and a category for every imported model (a "finish" step after any import).

Language
- The phone that imports translates the name, summary, description and tags with its own AI and sends them as
  overrides; the hub uses them as they are.
- Anything still not in English (an import from the website, a phone without an AI model, and always the file
  names, which are only known once downloaded and unzipped) is translated with the team's own AI connection chosen in
  Settings ("import_language": {"translate": bool, "connection": "ai_ollama" | ... | "order"}). Nothing is tied to a
  particular machine: a team with no AI connection keeps the original text and gets a note.
- A cheap check picks the text worth sending (non-Latin script, or Latin text with almost no common English words),
  so English imports cost nothing.
- Files and the model's folder are renamed on disk and in the hub; the original wording is kept in the model's notes.

Category
- A category chosen at import is used (made if new) and remembered for that source category, so the next import
  from e.g. Printables "Christmas Decorations" suggests the same one.
- Otherwise: the remembered choice, else an existing category whose name matches; never a new category made from
  the source site's name.
"""
import json
import logging
import re
import shutil
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..kv import set_setting, setting

log = logging.getLogger("hub.import_language")

SETTINGS_KEY = "import_language"
MAP_KEY = "category_map"
EN_WORDS = set("""a an and are as at be by for from has have in is it its of on or that the this to was were will with
you your print printed printing model models part parts set kit holder stand box case cover tool toy fidget base lid
top bottom left right small large mini big new easy no support supports without one two three four five six inch
color colour version plate test""".split())
# Common words of the languages imports usually arrive in (German, Spanish, French, Italian, Dutch, Portuguese,
# Czech, Polish), used only to tip the balance against English.
OTHER_WORDS = set("""der die das und mit ein eine einer einem für von zu ist nicht auf im den dem des oder aus bei
el la los las del y con para por una es al se sin lo le les des du et avec pour un une est dans sur au il gli di
per che della delle het een en met voor van op te ist da em com para os ao na no do dos z na do je se pro jak nebo
ze sie oraz w""".split()) - EN_WORDS - {"do", "no", "pro", "die", "per", "con", "den", "the", "a"}
NON_LATIN = re.compile(r"[\u0400-\u04FF\u0590-\u06FF\u0E00-\u0E7F\u3040-\u30FF\u3400-\u9FFF\uAC00-\uD7AF\uFF00-\uFFEF]")
WORD = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿĀ-ž]{2,}")


def options(db: Session) -> dict:
    o = dict(setting(db, SETTINGS_KEY) or {})
    return {"translate": o.get("translate", True), "connection": o.get("connection") or "order"}


def save_options(db: Session, translate: bool, connection: str) -> dict:
    set_setting(db, SETTINGS_KEY, {"translate": bool(translate), "connection": connection or "order"})
    return options(db)


def needs_english(text: str | None) -> bool:
    """Probably not English: non-Latin script, or 3+ Latin words with almost none of the common English ones."""
    t = (text or "").strip()
    if not t:
        return False
    if NON_LATIN.search(t):
        return True
    words = [w.lower() for w in WORD.findall(t)]
    accented = sum(1 for w in words if re.search(r"[À-ÖØ-öø-ÿĀ-ž]", w))
    if accented >= 2:
        return True
    if len(words) < 3:
        return False
    en = sum(1 for w in words if w in EN_WORDS)
    other = sum(1 for w in words if w in OTHER_WORDS)
    if other and other > en:
        return True
    # Short English names rarely contain "the" or "and"; only longer text is judged by how few English words it has.
    return len(words) >= 12 and en / len(words) < 0.05


def _ai(db: Session, connection: str):
    """(kind, cfg) of the team's AI connection to translate with, or None."""
    from . import connections as conns, vision
    kinds = [connection] if connection and connection != "order" else vision.order(db)
    for k in kinds:
        cfg = conns.get(k, required=False)
        if cfg:
            return k, cfg
    return None


def translate(db: Session, items: dict[str, str], what: str = "3D printing model text") -> tuple[dict, str | None]:
    """{key: English} for the items that need it (others are left out), and a note when nothing could translate."""
    want = {k: v for k, v in items.items() if v and needs_english(v)}
    if not want:
        return {}, None
    opt = options(db)
    if not opt["translate"]:
        return {}, "translation is off in Settings"
    ai = _ai(db, opt["connection"])
    if not ai:
        return {}, "no AI connection to translate with (Settings, Connected services)"
    from . import vision
    kind, cfg = ai
    prompt = (f"Translate this {what} into natural English. Answer with ONE JSON object and nothing else, with the same "
              "keys. Keep numbers, sizes, version tags (v2, v12), product and brand names, and punctuation. Keep it "
              "the same length or shorter. If a value is already English, return it unchanged.\n\n"
              + json.dumps(want, ensure_ascii=False))
    try:
        out = vision.parse_json(vision.ask(kind, cfg, prompt, None, timeout=180))
    except Exception as e:  # noqa: BLE001
        log.info("translate with %s: %s", kind, e)
        return {}, f"translation with {kind} failed: {str(e)[:120]}"
    return {k: str(out[k]).strip() for k in want if isinstance(out.get(k), str) and str(out[k]).strip()}, None


def english_filenames(db: Session, names: list[str]) -> tuple[dict, str | None]:
    """{old file name: new file name} for names in another language; extensions and numbering kept."""
    stems = {}
    for n in set(names):
        stem, dot, ext = n.rpartition(".")
        stem = stem if dot else n
        if needs_english(re.sub(r"[_\-.]+", " ", stem)):
            stems[n] = stem
    if not stems:
        return {}, None
    keyed = {f"f{i}": s for i, s in enumerate(stems.values())}
    got, note = translate(db, keyed, "list of 3D model file names (keep obj_10_ style prefixes and .step_5 suffixes)")
    out = {}
    for (n, stem), key in zip(stems.items(), keyed):
        new_stem = re.sub(r'[<>:"/\\|?*]+', "_", got.get(key, "")).strip()
        if new_stem and new_stem != stem:
            ext = n[len(stem):]
            out[n] = f"{new_stem}{ext}"
    return out, note


# ---------------------------------------------------------------- categories

# Words that say nothing about what a category holds ("Other Art & Designs" is about art, not "other").
GENERIC = {"other", "others", "and", "the", "for", "design", "designs", "stuff", "things", "thing", "misc", "various",
           "accessories", "useful", "models", "model", "general", "items"}


def _key(platform: str, source_category: str | None) -> str:
    return f"{platform}:{(source_category or '').strip().lower()}"


def suggest_category(db: Session, platform: str, source_category: str | None) -> str | None:
    from ..models import Category
    if not source_category:
        return None
    remembered = (setting(db, MAP_KEY) or {}).get(_key(platform, source_category))
    names = [c.name for c in db.execute(select(Category)).scalars()]
    if remembered and remembered in names:
        return remembered
    want = {w for w in re.findall(r"[a-z]{3,}", source_category.lower())} - GENERIC
    best, score = None, 0
    for n in names:
        have = {w for w in re.findall(r"[a-z]{3,}", n.lower())} - GENERIC
        s = len(want & have) + (2 if n.lower() == source_category.lower() else 0)
        if s > score:
            best, score = n, s
    return best


def remember_category(db: Session, platform: str, source_category: str | None, chosen: str):
    if not source_category or not chosen:
        return
    m = dict(setting(db, MAP_KEY) or {})
    m[_key(platform, source_category)] = chosen
    set_setting(db, MAP_KEY, m)


# ---------------------------------------------------------------- the finish step

def _move_folder(model, lib: Path, new_folder: str):
    old = model.library_rel
    if not old or old == new_folder:
        return
    if (lib / old).exists() and not (lib / new_folder).exists():
        (lib / new_folder).parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(lib / old), str(lib / new_folder))
    for f in model.files:
        if f.library_rel and f.library_rel.startswith(old + "/"):
            f.library_rel = new_folder + f.library_rel[len(old):]
    model.library_rel = new_folder


def finish(db: Session, model, platform: str, source_category: str | None = None, overrides: dict | None = None,
           category: str | None = None) -> dict:
    """English text, files and folder, and the category, for a model that was just imported."""
    from ..config import settings
    from . import importer
    report = {"translated": [], "renamed_files": 0, "category": None, "notes": []}
    before = {"name": model.name, "summary": model.summary or "", "description": model.description or "",
              "tags": ", ".join(model.tags or [])}
    o = {k: v for k, v in (overrides or {}).items() if v not in (None, "")}
    if o:
        for k in ("name", "summary", "description"):
            if k in o:
                setattr(model, k, str(o[k]).strip()[:300] if k == "name" else str(o[k]).strip())
        if "tags" in o:
            tags = o["tags"] if isinstance(o["tags"], list) else str(o["tags"]).split(",")
            model.tags = sorted({t.strip() for t in tags if t and t.strip()})[:40]
        report["translated"] = sorted(k for k in o if k in before)
    rest = {k: v for k, v in before.items() if k not in o}
    got, note = translate(db, rest)
    if note:
        report["notes"].append(note)
    for k, v in got.items():
        if k == "tags":
            model.tags = sorted({t.strip() for t in v.split(",") if t.strip()})[:40]
        elif k == "name":
            model.name = v[:300]
        else:
            setattr(model, k, v)
        report["translated"].append(k)
    changed = [k for k in before if (getattr(model, k) if k != "tags" else ", ".join(model.tags or [])) != before[k]]
    # The draft product made from the model at import follows it (only where it still has the model's wording).
    prod = getattr(model, "product", None)
    if prod is not None and changed:
        if "name" in changed and (prod.title or "").strip() == before["name"].strip():
            prod.title = model.name
        for pk, mk in (("short_description", "summary"), ("description", "description")):
            cur = (getattr(prod, pk) or "").strip()
            if mk in changed and cur and (cur == before[mk].strip() or before[mk].strip().startswith(cur[:200])):
                setattr(prod, pk, getattr(model, mk))
    if changed:
        orig = "\n".join(f"{k}: {before[k]}" for k in changed if before[k])
        model.notes = ((model.notes or "") + f"\n\nOriginal wording ({platform}):\n{orig}").strip()

    # category
    chosen = (category or "").strip() or None
    if chosen:
        model.category = importer.get_or_create_category(db, chosen)
        remember_category(db, platform, source_category, chosen)
    elif not model.category:
        s = suggest_category(db, platform, source_category)
        if s:
            model.category = importer.get_or_create_category(db, s)
    report["category"] = model.category.name if model.category else None

    # files, then the folder (it follows the English name)
    lib = Path(settings().library_root)
    names = [f.filename for f in model.files]
    renames, note = english_filenames(db, names)
    if note and names:
        report["notes"].append(note)
    for f in model.files:
        new = renames.get(f.filename)
        if not new or not f.library_rel:
            continue
        d = f.library_rel.rsplit("/", 1)[0]
        target = f"{d}/{new}"
        if (lib / f.library_rel).exists() and not (lib / target).exists():
            (lib / f.library_rel).rename(lib / target)
            f.filename, f.library_rel = new, target
            report["renamed_files"] += 1
    _move_folder(model, lib, importer.library_folder(model))
    db.flush()
    return report
