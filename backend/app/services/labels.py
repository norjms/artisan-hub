"""Scan labels for spools and locations (mobile control, 2026-10-23).

Every label carries a big QR code with https://<hub public host>/l/<code> (S<spool id>, L<6 chars>, P<product id>,
O<order id>), the name, what it is, and for spools the vendor, material, a colour swatch, the weight and the code.
Codes never change, so a reprinted label still works.

Outputs: PDF (Avery 5160 / L7160 sheets of 30, and 62x29 mm or 62x100 mm labels one per page), PNG (62x29 mm and
40x30 mm, for the phone to send to a Bluetooth label maker), and straight to a network label printer: Brother QL
raster (the brother_ql package) or ZPL, both over TCP 9100.

Label printers are the team's labelprinter connection: config.printers = [{id, name, kind: brother_ql | zpl,
host, port, model (QL-820NWB, ...), tape (62x29, 62, 62x100, 29x90 for Brother; WxH in mm for ZPL), dpi}].
"""
import io
import re
import socket
import zipfile

from PIL import Image, ImageDraw, ImageFont

MM = 1 / 25.4
FORMATS = {
    "pdf_avery5160": ("pdf", None), "pdf_62x29": ("pdf", (62, 29)), "pdf_62x100": ("pdf", (62, 100)),
    "png_62x29": ("png", (62, 29)), "png_40x30": ("png", (40, 30)), "printer": ("printer", None),
}
# Brother QL tapes: brother_ql label id -> printable pixels at 300 dpi (width, height); None = continuous
BROTHER_TAPES = {"62x29": ((696, 271), "62x29"), "62": ((696, 330), "62"), "62x100": ((696, 1109), "62x100"),
                 "29x90": ((991, 306), "29x90"), "29": ((306, 600), "29"), "38x90": ((991, 413), "38x90")}
PRINTER_KINDS = ("brother_ql", "zpl")


class LabelError(ValueError):
    pass


def public_base() -> str:
    """The hub's public address: the same one /pair links use."""
    from ..config import settings
    return settings().public_url.rstrip("/")


def code_url(code: str) -> str:
    return f"{public_base()}/l/{code}"


# ---------------------------------------------------------------- what goes on a label

def spool_item(s: dict) -> dict:
    lines = [x for x in [" ".join(x for x in [s.get("vendor"), s.get("material")] if x) or None,
                         f"{round(float(s['label_weight_g']))} g" if s.get("label_weight_g") else
                         (f"{round(float(s['remaining_g']))} g" if s.get("remaining_g") else None)] if x]
    return {"kind": "spool", "code": s.get("code") or f"S{s['id']}", "title": s.get("name") or "Spool",
            "subtitle": "Spool", "lines": lines, "color_hex": s.get("color_hex")}


def location_item(loc: dict) -> dict:
    kind = loc.get("kind") or "storage"
    sub = {"storage": "Storage", "drybox": "Dry box", "printer": "Printer", "ams": "AMS unit", "ams_slot": "AMS slot",
           "external": "External holder", "other": "Location"}.get(kind, kind.title())
    lines = [loc["printer_name"]] if loc.get("printer_name") and kind in ("ams", "ams_slot", "external") else []
    return {"kind": "location", "code": loc.get("code") or f"L{loc.get('id')}", "title": loc.get("name") or "Location",
            "subtitle": sub, "lines": lines, "color_hex": None}


# ---------------------------------------------------------------- drawing

def _font(px: int):
    try:
        return ImageFont.load_default(size=max(8, int(px)))
    except TypeError:            # very old Pillow
        return ImageFont.load_default()


def _qr(text: str, px: int) -> Image.Image:
    import segno
    q = segno.make(text, error="m")
    buf = io.BytesIO()
    q.save(buf, kind="png", scale=10, border=1, dark="#000000", light="#ffffff")
    buf.seek(0)
    return Image.open(buf).convert("L").resize((px, px), Image.NEAREST)


def _fit(draw, text: str, font_px: int, max_w: int):
    """The biggest font (down to 60%) that fits, then the text cut with an ellipsis if it still doesn't."""
    size = font_px
    while size > font_px * 0.6:
        f = _font(size)
        if draw.textlength(text, font=f) <= max_w:
            return text, f
        size -= 2
    f = _font(size)
    while text and draw.textlength(text + "...", font=f) > max_w:
        text = text[:-1]
    return text + "...", f


def render(item: dict, size_px: tuple[int, int], url: str | None = None, color: bool = False) -> Image.Image:
    """One label. Black and white for label printers (the colour swatch is a grey box); with color=True (PNG and
    PDF) the swatch shows the real colour."""
    w, h = size_px
    im = Image.new("RGB" if color else "L", (w, h), "white" if color else 255)
    ink = (0, 0, 0) if color else 0
    d = ImageDraw.Draw(im)
    m = max(4, int(min(w, h) * 0.04))
    url = url or code_url(item["code"])
    tall = h > w * 1.2
    if tall:
        q = min(w - 2 * m, int(h * 0.55))
        im.paste(_qr(url, q), ((w - q) // 2, m))
        x0, y, tw = m, m + q + m, w - 2 * m
    else:
        q = h - 2 * m
        im.paste(_qr(url, q), (m, m))
        x0, y, tw = m + q + m, m, w - q - 3 * m
    big = max(10, int((h if not tall else w) * (0.16 if not tall else 0.11)))
    small = max(9, int(big * 0.66))
    t, f = _fit(d, item.get("title") or "", big, tw)
    d.text((x0, y), t, fill=ink, font=f)
    y += int(big * 1.15)
    t, f = _fit(d, item.get("subtitle") or "", small, tw)
    d.text((x0, y), t, fill=ink, font=f)
    y += int(small * 1.2)
    for ln in item.get("lines") or []:
        if y + small > h - m - small:
            break
        t, f = _fit(d, ln, small, tw)
        d.text((x0, y), t, fill=ink, font=f)
        y += int(small * 1.2)
    code_f = _font(int(small * 1.15))
    cy = h - m - int(small * 1.2)
    code = item["code"]
    d.text((x0, cy), code, fill=ink, font=code_f)
    hx = item.get("color_hex")
    if hx and re.fullmatch(r"#?[0-9A-Fa-f]{6}", hx):
        rgb = tuple(int(hx.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
        fill = rgb if color else int(0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2])
        sw = int(small * 1.1)
        sx = x0 + int(d.textlength(code, font=code_f)) + m * 2
        if sx + sw <= w - m:
            d.rectangle((sx, cy, sx + sw, cy + sw), fill=fill, outline=ink, width=max(1, sw // 12))
    return im


def render_color(item: dict, size_px: tuple[int, int]) -> Image.Image:
    return render(item, size_px, color=True)


def px(mm_size: tuple[float, float], dpi: int = 300) -> tuple[int, int]:
    return int(round(mm_size[0] * MM * dpi)), int(round(mm_size[1] * MM * dpi))


# ---------------------------------------------------------------- files

def pdf(items: list[dict], fmt: str, copies: int = 1) -> bytes:
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.units import inch, mm
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    items = [it for it in items for _ in range(max(1, copies))]
    if fmt == "pdf_avery5160":
        c = canvas.Canvas(buf, pagesize=letter)
        lw, lh = 2.625 * inch, 1 * inch
        left, top, pitch_x = 0.1875 * inch, 0.5 * inch, 2.75 * inch
        for i, it in enumerate(items):
            if i and i % 30 == 0:
                c.showPage()
            n = i % 30
            col, row = n % 3, n // 3
            x = left + col * pitch_x
            y = letter[1] - top - (row + 1) * lh
            img = render_color(it, (788, 300))
            c.drawImage(ImageReader(img), x + 0.04 * inch, y + 0.04 * inch, lw - 0.08 * inch, lh - 0.08 * inch)
        c.showPage()
    else:
        size = FORMATS[fmt][1]
        c = canvas.Canvas(buf, pagesize=(size[0] * mm, size[1] * mm))
        for it in items:
            c.drawImage(ImageReader(render_color(it, px(size))), 0, 0, size[0] * mm, size[1] * mm)
            c.showPage()
    c.save()
    return buf.getvalue()


def png(items: list[dict], fmt: str, copies: int = 1) -> tuple[bytes, str]:
    """One PNG, or a ZIP of PNGs (label-1.png, ...) when there are several."""
    size = px(FORMATS[fmt][1])
    imgs = [render_color(it, size) for it in items for _ in range(max(1, copies))]

    def enc(im):
        b = io.BytesIO()
        im.save(b, "PNG", dpi=(300, 300))
        return b.getvalue()
    if len(imgs) == 1:
        return enc(imgs[0]), "image/png"
    z = io.BytesIO()
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
        for i, (im, it) in enumerate(zip(imgs, [it for it in items for _ in range(max(1, copies))]), 1):
            zf.writestr(f"label-{i}-{it['code']}.png", enc(im))
    return z.getvalue(), "application/zip"


# ---------------------------------------------------------------- label printers

def printers(team_id: int | None = None) -> list[dict]:
    from .connections import get
    c = get("labelprinter", team_id, required=False) or {}
    out = []
    for i, p in enumerate(c.get("printers") or [], 1):
        if isinstance(p, dict):
            out.append({"id": int(p.get("id") or i), "name": p.get("name") or f"Printer {i}",
                        "kind": p.get("kind") or "brother_ql", "host": p.get("host"), "port": int(p.get("port") or 9100),
                        "model": p.get("model") or ("QL-820NWB" if (p.get("kind") or "brother_ql") == "brother_ql" else None),
                        "tape": str(p.get("tape") or ("62x29" if (p.get("kind") or "brother_ql") == "brother_ql" else "50x25")),
                        "dpi": int(p.get("dpi") or (300 if (p.get("kind") or "brother_ql") == "brother_ql" else 203))})
    return out


def validate_printer(p: dict) -> dict:
    kind = p.get("kind") or "brother_ql"
    if kind not in PRINTER_KINDS:
        raise LabelError("kind is brother_ql or zpl")
    host = (p.get("host") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9.\-:\[\]]{1,253}", host):
        raise LabelError("host is the printer's IP address or host name")
    port = int(p.get("port") or 9100)
    if not 1 <= port <= 65535:
        raise LabelError("port is 1 to 65535")
    tape = str(p.get("tape") or ("62x29" if kind == "brother_ql" else "50x25"))
    if kind == "brother_ql" and tape not in BROTHER_TAPES:
        raise LabelError(f"Brother tape is one of {', '.join(BROTHER_TAPES)}")
    if kind == "zpl" and not re.fullmatch(r"\d{2,3}x\d{2,3}", tape):
        raise LabelError("ZPL tape is the label size in mm, WxH (for example 50x25)")
    dpi = int(p.get("dpi") or (300 if kind == "brother_ql" else 203))
    if dpi not in (203, 300, 600):
        raise LabelError("dpi is 203, 300 or 600")
    return {"name": (p.get("name") or host)[:80], "kind": kind, "host": host, "port": port,
            "model": (p.get("model") or ("QL-820NWB" if kind == "brother_ql" else ""))[:40] or None, "tape": tape,
            "dpi": dpi}


def _send(host: str, port: int, data: bytes, timeout: float = 10.0):
    with socket.create_connection((host, port), timeout=timeout) as s:
        s.sendall(data)


def brother_raster(images: list[Image.Image], model: str, tape: str) -> bytes:
    from brother_ql.conversion import convert
    from brother_ql.raster import BrotherQLRaster
    qlr = BrotherQLRaster(model or "QL-820NWB")
    qlr.exception_on_warning = False
    label = BROTHER_TAPES[tape][1]
    return convert(qlr, [im.convert("RGB") for im in images], label, rotate="auto", threshold=70.0, dither=False,
                   compress=False, red=False, dpi_600=False, hq=True, cut=True)


def zpl(images: list[Image.Image]) -> bytes:
    """Each image as one ZPL label: a ^GFA graphic field (1 bit, black = 1)."""
    out = []
    for im in images:
        bw = im.convert("L").point(lambda v: 0 if v < 128 else 255, "1")
        w, h = bw.size
        row = (w + 7) // 8
        data = bytearray()
        px_ = bw.load()
        for y in range(h):
            for xb in range(row):
                byte = 0
                for bit in range(8):
                    x = xb * 8 + bit
                    if x < w and px_[x, y] == 0:
                        byte |= 0x80 >> bit
                data.append(byte)
        total = len(data)
        out.append(f"^XA^PW{w}^LL{h}^FO0,0^GFA,{total},{total},{row},{data.hex().upper()}^FS^XZ")
    return "\n".join(out).encode()


def print_labels(printer: dict, items: list[dict], copies: int = 1) -> int:
    """Send labels to a network label printer; answers how many were sent."""
    items = [it for it in items for _ in range(max(1, copies))]
    if printer["kind"] == "brother_ql":
        size = BROTHER_TAPES[printer["tape"]][0]
        data = brother_raster([render(it, size) for it in items], printer.get("model"), printer["tape"])
    elif printer["kind"] == "zpl":
        wmm, hmm = (int(x) for x in printer["tape"].split("x"))
        data = zpl([render(it, px((wmm, hmm), printer.get("dpi") or 203)) for it in items])
    else:
        raise LabelError(f"unknown label printer kind {printer['kind']}")
    try:
        _send(printer["host"], int(printer.get("port") or 9100), data)
    except OSError as e:
        raise LabelError(f"{printer['name']} did not take the labels: {type(e).__name__}: {e}")
    return len(items)


def probe(printer: dict) -> tuple[bool, str]:
    try:
        with socket.create_connection((printer["host"], int(printer.get("port") or 9100)), timeout=3):
            return True, "reachable"
    except OSError as e:
        return False, f"{type(e).__name__}: {e}"
