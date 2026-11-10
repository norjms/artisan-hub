"""JSON shapes returned by the API."""
from .clients import manyfold
from .config import settings
from .models import Model, Image, ModelFile, Product, Variant


def img_url(img: Image | None, size="thumb") -> str | None:
    if not img:
        return None
    return f"/media/{img.id}/{size}?v={img.sha256[:8]}"


def hero(model: Model) -> Image | None:
    best = None
    for i in model.images:
        if i.role == "hero":
            return i
        if i.role == "gallery" and (best is None or i.sort < best.sort):
            best = i
    return best or (model.images[0] if model.images else None)


def image(i: Image) -> dict:
    return {"id": i.id, "role": i.role, "sort": i.sort, "filename": i.filename, "width": i.width, "height": i.height,
            "bytes": i.bytes, "caption": i.caption, "for_web": i.for_web, "dam_ref": i.dam_ref, "dam_error": i.dam_error,
            "woo_media_id": i.woo_media_id, "thumb": img_url(i, "thumb"), "large": img_url(i, "large"),
            "original": f"/media/{i.id}/original"}


def mfile(f: ModelFile) -> dict:
    return {"id": f.id, "filename": f.filename, "kind": f.kind, "bytes": f.bytes, "slicer": f.slicer,
            "printer": f.printer, "plates": f.plates, "print_seconds": f.print_seconds, "filament": f.filament,
            "settings": f.settings, "is_print_file": f.is_print_file, "watchtower_uuid": f.watchtower_uuid, "farm_kind": f.farm_kind,
            "watchtower_error": f.watchtower_error, "download": f"/api/files/{f.id}/download"}


def variant(v: Variant) -> dict:
    return {"id": v.id, "sku": v.sku, "color_name": v.color_name, "color_hex": v.color_hex, "material": v.material,
            "spoolman_filament_id": v.spoolman_filament_id, "price": v.price, "active": v.active, "sort": v.sort,
            "image_id": v.image_id, "woo_variation_id": v.woo_variation_id}


def product(p: Product | None) -> dict | None:
    if not p:
        return None
    from .clients import public_url
    woo_url = public_url("woo", "url") or ""
    return {
        "id": p.id, "sku": p.sku, "title": p.title, "price": p.price, "sale_price": p.sale_price,
        "short_description": p.short_description, "description": p.description, "weight_g": p.weight_g,
        "dims_cm": p.dims_cm, "color_mode": p.color_mode, "target_buffer": p.target_buffer, "stock_cap": p.stock_cap,
        "live": p.live, "woo_product_id": p.woo_product_id, "woo_status": p.woo_status,
        "woo_synced_at": p.woo_synced_at.isoformat() if p.woo_synced_at else None, "woo_error": p.woo_error,
        "woo_stock_pushed": p.woo_stock_pushed, "wt_mapping_ids": p.wt_mapping_ids, "wt_error": p.wt_error,
        "woo_admin_url": f"{woo_url}/wp-admin/post.php?post={p.woo_product_id}&action=edit" if p.woo_product_id and woo_url else None,
        "woo_view_url": f"{woo_url}/?p={p.woo_product_id}" if p.woo_product_id and woo_url else None,
        "variants": [variant(v) for v in p.variants],
        "filaments": [{"id": f.id, "slot": f.slot, "label": f.label, "follows_variant": f.follows_variant,
                       "material": f.material, "color_name": f.color_name, "color_hex": f.color_hex,
                       "spoolman_filament_id": f.spoolman_filament_id, "grams": f.grams, "waste_pct": f.waste_pct}
                      for f in p.filaments],
        "parts": [{"id": b.id, "inventree_part_id": b.inventree_part_id, "name": b.name, "qty": b.qty,
                   "variant_id": b.variant_id, "note": b.note} for b in p.parts],
    }


def model_card(m: Model, units: int | None = None) -> dict:
    h = hero(m)
    p = m.product
    return {"id": m.id, "code": m.code, "name": m.name, "category": m.category.name if m.category else None,
            "designer": m.designer.name if m.designer else None, "status": m.status, "favorite": m.favorite,
            "thumb": img_url(h, "thumb"), "large": img_url(h, "large"),
            "images": sum(1 for i in m.images if i.role != "archive"), "files": len(m.files),
            "sku": p.sku if p else None, "price": p.price if p else None, "live": p.live if p else False,
            "on_woo": bool(p and p.woo_product_id), "woo_status": p.woo_status if p else None,
            "license_ok": m.license_ok, "license_code": m.license_code, "units": units, "makerworld_id": m.makerworld_id, "source_platform": m.source_platform,
            "print_ready": any(f.kind in ("3mf_sliced", "gcode") for f in m.files)}


def designer(dz) -> dict | None:
    if not dz:
        return None
    return {"id": dz.id, "name": dz.name, "url": dz.url, "handle": dz.handle, "platform": dz.platform,
            "credit_template": dz.credit_template, "always_credit": dz.always_credit, "notes": dz.notes,
            "license_note": dz.license_note}


def model_detail(m: Model) -> dict:
    from .services import licensing
    from .clients import public_url
    dam_host = public_url("dam", "host")
    d = model_card(m)
    d["license"] = {**licensing.info(m.license_code), "note": m.license_note, "credit_line": licensing.credit_line(m),
                    "credit_override": m.credit_override, "needs_credit": licensing.needs_credit(m),
                    "problems": licensing.sale_problems(m),
                    "ip_flags": licensing.ip_flags(m.name, " ".join(m.tags or [])),
                    "checked_at": m.license_checked_at.isoformat() if m.license_checked_at else None,
                    "options": [{"code": k, "label": v["label"], "sell": v["sell"]} for k, v in licensing.LICENSES.items()]}
    if m.license_code == "COMMERCIAL":
        cov = licensing.commercial_cover(m)
        g = cov.get("grant")
        d["license"]["commercial"] = {"recorded": cov["recorded"], "active": cov["active"],
                                      "grant": {"id": g.id, "name": g.name, "kind": g.kind,
                                                "expires_on": g.expires_on.isoformat() if g.expires_on else None,
                                                **(cov.get("status") or {})} if g else None}
    d["designer_info"] = designer(m.designer)
    d["makerworld_id"] = m.makerworld_id
    d["source_platform"] = m.source_platform
    d["source_ext_id"] = m.source_ext_id
    d["makerworld"] = (m.extra or {}).get("makerworld")
    d.update({
        "slug": m.slug, "source": m.source, "source_url": m.source_url, "patreon_url": m.patreon_url,
        "guide_url": m.guide_url, "summary": m.summary, "description": m.description, "guide_md": m.guide_md,
        "model_dims": m.model_dims, "print_dims": m.print_dims, "print_settings": m.print_settings, "tags": m.tags,
        "license_note": m.license_note, "notes": m.notes, "source_folder": m.source_folder,
        "library_rel": m.library_rel, "category_id": m.category_id, "designer_id": m.designer_id,
        "published_at": m.published_at.isoformat() if m.published_at else None,
        "created_at": m.created_at.isoformat() if m.created_at else None,
        "manyfold_url": _manyfold_url(m),
        "dam_url": f"https://{dam_host}/pages/search.php?search=!collection{m.dam_collection_ref}"
        if m.dam_collection_ref and dam_host else None,
        "dam_collection_ref": m.dam_collection_ref,
        "images_all": [image(i) for i in sorted(m.images, key=lambda i: (i.role == "archive", i.sort, i.id))],
        "files_all": [mfile(f) for f in m.files],
        "product": product(m.product),
    })
    return d


def _manyfold_url(m: Model):
    if not m.manyfold_model_id:
        return None
    from .services.connections import NotConnected
    try:
        return manyfold().model_url(m.manyfold_model_id)
    except NotConnected:
        return None
