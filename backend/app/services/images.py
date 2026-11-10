"""Image storage: originals by content hash plus pre-rendered WebP sizes for a fast gallery."""
import hashlib
import shutil
from pathlib import Path

from PIL import Image as PILImage, ImageOps

from ..config import settings

try:
    # pi-heif: decode-only HEIF/HEIC support (no GPL x265 encoder bundled, unlike pillow-heif)
    from pi_heif import register_heif_opener
    register_heif_opener()
except Exception:  # noqa: BLE001
    pass

PILImage.MAX_IMAGE_PIXELS = 200_000_000
SIZES = {"thumb": 480, "large": 1800}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic", ".heif", ".tif", ".tiff", ".bmp"}


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def root() -> Path:
    return Path(settings().data_dir)


def original_path(sha: str, ext: str) -> Path:
    return root() / "originals" / sha[:2] / f"{sha}{ext.lower()}"


def variant_path(sha: str, size: str) -> Path:
    return root() / "img" / size / sha[:2] / f"{sha}.webp"


def store(src: str | Path, sha: str | None = None) -> dict:
    """Copy an image into the store and render sizes. Returns metadata."""
    src = Path(src)
    sha = sha or sha256_file(src)
    ext = src.suffix.lower() if src.suffix.lower() in IMAGE_EXT else ".jpg"
    dst = original_path(sha, ext)
    if not dst.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_suffix(dst.suffix + ".part")
        shutil.copyfile(src, tmp)
        tmp.replace(dst)
    meta = render(sha, dst)
    meta.update({"sha256": sha, "store_rel": str(dst.relative_to(root())), "bytes": dst.stat().st_size})
    return meta


def render(sha: str, original: Path) -> dict:
    with PILImage.open(original) as im:
        im = ImageOps.exif_transpose(im)
        w, h = im.size
        if im.mode not in ("RGB", "RGBA"):
            im = im.convert("RGBA" if "A" in im.getbands() else "RGB")
        for name, px in SIZES.items():
            out = variant_path(sha, name)
            if out.exists():
                continue
            out.parent.mkdir(parents=True, exist_ok=True)
            cp = im.copy()
            cp.thumbnail((px, px), PILImage.Resampling.LANCZOS)
            cp.save(out, "WEBP", quality=82 if name == "thumb" else 86, method=4)
    return {"width": w, "height": h}


def web_jpeg(sha: str, original: Path, max_px: int = 2000) -> Path:
    """JPEG copy for WooCommerce (WordPress handles JPEG best)."""
    out = root() / "img" / "web" / sha[:2] / f"{sha}.jpg"
    if out.exists():
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    with PILImage.open(original) as im:
        im = ImageOps.exif_transpose(im)
        if im.mode in ("RGBA", "LA", "P"):
            im = im.convert("RGBA")
            bg = PILImage.new("RGB", im.size, (255, 255, 255))
            bg.paste(im, mask=im.split()[-1])
            im = bg
        else:
            im = im.convert("RGB")
        im.thumbnail((max_px, max_px), PILImage.Resampling.LANCZOS)
        im.save(out, "JPEG", quality=90, optimize=True, progressive=True)
    return out
