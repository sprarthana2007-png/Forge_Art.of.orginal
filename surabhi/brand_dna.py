"""Brand DNA - a stored fingerprint of a legitimate brand.

A brand is registered with its name, official website, logo and reference images. Every image is
fingerprinted with the shared helpers in schemas.py:

    file_sha256(bytes)  -> exact-duplicate fingerprint (byte-identical copies)
    photo_phash(bytes)  -> perceptual fingerprint (resized / re-encoded / lightly edited copies)
    is_similar(a, b)    -> the team's perceptual-match rule (Hamming distance <= PHASH_MATCH)

Submitted images are then compared against every registered fingerprint. No hashing is reimplemented here.

The FORGE planner calls `build_brand_dna(url)`: it crawls the store (Prarthana's crawler, reused as-is)
and merges in the owner-registered logo / reference fingerprints for that domain.
"""
from __future__ import annotations

import mimetypes
import sqlite3
import warnings
from io import BytesIO
from pathlib import Path

import imagehash
from PIL import Image, UnidentifiedImageError

from schemas import BrandDNA, file_sha256, is_similar, photo_phash
from surabhi import store
from surabhi.name_net import normalize_domain

MAX_IMAGE_BYTES = 15 * 1024 * 1024
MIN_SIMILAR_PX = 32  # tiny icons have near-blank perceptual hashes; they are only matched exactly
PHASH_BITS = 64
KINDS = ("logo", "reference")


class ImageError(ValueError):
    """An upload or downloaded file that cannot be fingerprinted."""


# ------------------------------------------------------------------ fingerprinting
def fingerprint_image(data: bytes, filename: str = "image") -> dict:
    """Validate one image and return its fingerprint. Raises ImageError with a readable reason."""
    if not isinstance(data, (bytes, bytearray)) or not data:
        raise ImageError("the file is empty")
    data = bytes(data)
    if len(data) > MAX_IMAGE_BYTES:
        raise ImageError(f"the file is larger than {MAX_IMAGE_BYTES // (1024 * 1024)} MB")
    head = data[:2048].lstrip().lower()
    if head.startswith(b"<svg") or (head.startswith(b"<?xml") and b"<svg" in head):
        raise ImageError("SVG vector images cannot be fingerprinted; upload a PNG, JPEG or WebP export")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as im:
                im.verify()  # structure / checksum check
            with Image.open(BytesIO(data)) as im:
                fmt, (w, h) = im.format or "unknown", im.size
                im.load()  # full decode: catches truncated files that verify() lets through
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise ImageError("the image has too many pixels to process safely") from None
    except UnidentifiedImageError:
        raise ImageError("not a recognised image format") from None
    except Exception as e:  # truncated / corrupt data, unsupported mode, ...
        raise ImageError(f"the image is damaged or unreadable ({type(e).__name__})") from None
    try:
        ph = photo_phash(data)
    except Exception as e:
        raise ImageError(f"could not compute a perceptual hash ({type(e).__name__})") from None
    return {"filename": filename, "sha256": file_sha256(data), "phash": ph,
            "width": w, "height": h, "format": fmt, "bytes": len(data)}


def phash_distance(a: str, b: str) -> int:
    """Hamming distance between two stored perceptual hashes (for display; matching uses is_similar)."""
    return abs(imagehash.hex_to_hash(a) - imagehash.hex_to_hash(b))


def _load_input(item, default_name: str) -> tuple[str, bytes]:
    """Accept bytes, a file path, or a (filename, bytes) pair."""
    if isinstance(item, tuple) and len(item) == 2:
        return str(item[0] or default_name), item[1]
    if isinstance(item, (bytes, bytearray)):
        return default_name, bytes(item)
    if isinstance(item, (str, Path)):
        p = Path(item)
        try:
            return p.name, p.read_bytes()
        except OSError as e:
            raise ImageError(f"cannot read {p}: {e.strerror or e}") from None
    raise ImageError(f"unsupported image input {type(item).__name__}")


def _save_copy(fp: dict, data: bytes) -> str:
    ext = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp", "GIF": ".gif"}.get(fp["format"]) \
        or mimetypes.guess_extension(f"image/{fp['format'].lower()}") or ".img"
    d = store.images_dir()
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{fp['sha256']}{ext}"
    if not path.exists():
        path.write_bytes(data)
    return str(path)


# ------------------------------------------------------------------ registration
def fingerprint_status(brand: dict) -> dict:
    logos = sum(i["kind"] == "logo" for i in brand.get("images", []))
    refs = sum(i["kind"] == "reference" for i in brand.get("images", []))
    status = "complete" if logos and refs else "partial" if logos or refs else "missing"
    return {"status": status, "logos": logos, "references": refs}


def register_brand(name: str, official_url: str, logo=None, reference_images=(), keep_copies: bool = True,
                   conn: sqlite3.Connection | None = None) -> dict:
    """Register (or update) a brand and fingerprint its images.

    Invalid images are rejected one by one with a reason; valid ones are still stored. Registering the same
    official domain again adds new images to the existing brand. Returns a summary dict.
    """
    name = (name or "").strip()
    if not name:
        raise ValueError("Brand name is required.")
    domain = normalize_domain(official_url)  # raises ValueError for an empty / malformed URL
    url = official_url.strip() if "://" in official_url else f"https://{official_url.strip()}"

    uploads = ([("logo", logo)] if logo is not None else []) + [("reference", r) for r in reference_images or ()]
    accepted, rejected = [], []
    for n, (kind, item) in enumerate(uploads, 1):
        fname = Path(item).name if isinstance(item, (str, Path)) else f"{kind}-{n}"
        try:
            fname, data = _load_input(item, fname)
            fp = fingerprint_image(data, fname)
            fp["stored_path"] = _save_copy(fp, data) if keep_copies else ""
            accepted.append((kind, fp))
        except ImageError as e:
            rejected.append({"filename": fname, "kind": kind, "error": str(e)})

    own = conn is None
    conn = conn or store.connect()
    try:
        with conn:
            brand_id = store.upsert_brand(conn, name, url, domain)
            added, duplicates = [], []
            for kind, fp in accepted:
                _, created = store.add_image(conn, brand_id, kind, fp, fp["stored_path"])
                (added if created else duplicates).append({"filename": fp["filename"], "kind": kind,
                                                           "sha256": fp["sha256"], "phash": fp["phash"]})
        brand = store.get_brand(conn, brand_id)
    finally:
        if own:
            conn.close()
    return {"brand": brand, "fingerprint": fingerprint_status(brand), "added": added,
            "duplicates": duplicates, "rejected": rejected}


def list_brands(conn: sqlite3.Connection | None = None) -> list[dict]:
    own = conn is None
    conn = conn or store.connect()
    try:
        brands = store.list_brands(conn)
    finally:
        if own:
            conn.close()
    for b in brands:
        b["fingerprint"] = fingerprint_status(b)
    return brands


# ------------------------------------------------------------------ comparison
def normalise_images(images) -> list[dict]:
    """images: {name: bytes} | [(name, bytes)] | [{"name", "data", "source"?, "role"?}]"""
    if isinstance(images, dict):
        return [{"name": k, "data": v} for k, v in images.items()]
    out = []
    for n, it in enumerate(images or (), 1):
        if isinstance(it, dict):
            out.append({**it, "name": it.get("name") or f"image-{n}"})
            continue
        try:
            name, data = _load_input(it, f"image-{n}")
            out.append({"name": name, "data": data})
        except ImageError as e:
            out.append({"name": str(it) if isinstance(it, (str, Path)) else f"image-{n}", "load_error": str(e)})
    return out


def compare_images(images, brands: list[dict] | None = None, conn: sqlite3.Connection | None = None) -> dict:
    """Compare submitted images with registered brand fingerprints.

    An exact match means byte-identical SHA-256. A similar match means is_similar() on the perceptual hashes
    (both images at least MIN_SIMILAR_PX on the short side), excluding exact matches.
    """
    if brands is None:
        brands = list_brands(conn)
    refs = [(b, img) for b in brands for img in b.get("images", [])]
    results, matched = [], {}
    for item in normalise_images(images):
        row = {"name": item["name"], "source": item.get("source", ""), "role": item.get("role", ""),
               "exact_matches": [], "similar_matches": []}
        if item.get("load_error"):
            row.update(status="invalid", error=item["load_error"])
            results.append(row)
            continue
        try:
            fp = fingerprint_image(item["data"], item["name"])
        except ImageError as e:
            row.update(status="invalid", error=str(e))
            results.append(row)
            continue
        row.update(status="ok", sha256=fp["sha256"], phash=fp["phash"], width=fp["width"], height=fp["height"])
        big = min(fp["width"], fp["height"]) >= MIN_SIMILAR_PX
        for brand, ref in refs:
            hit = {"brand_id": brand["id"], "brand_name": brand["name"], "image_id": ref["id"],
                   "kind": ref["kind"], "filename": ref["filename"]}
            if ref["sha256"] == fp["sha256"]:
                row["exact_matches"].append({**hit, "distance": 0, "similarity": 100})
            elif big and min(ref["width"], ref["height"]) >= MIN_SIMILAR_PX and is_similar(fp["phash"], ref["phash"]):
                d = phash_distance(fp["phash"], ref["phash"])
                row["similar_matches"].append({**hit, "distance": d,
                                               "similarity": round(100 * (1 - d / PHASH_BITS))})
        row["similar_matches"].sort(key=lambda m: m["distance"])
        for kind, hits in (("exact", row["exact_matches"]), ("similar", row["similar_matches"])):
            for m in hits:
                agg = matched.setdefault(m["brand_id"], {"brand_id": m["brand_id"], "brand_name": m["brand_name"],
                                                         "exact": 0, "similar": 0, "logo_exact": False,
                                                         "logo_similar": False})
                agg[kind] += 1
                if m["kind"] == "logo":
                    agg[f"logo_{kind}"] = True
        results.append(row)
    ok = [r for r in results if r["status"] == "ok"]
    return {
        "images_submitted": len(results),
        "images_analysed": len(ok),
        "invalid_images": [{"name": r["name"], "error": r["error"]} for r in results if r["status"] == "invalid"],
        "exact_match_count": sum(bool(r["exact_matches"]) for r in ok),
        "similar_match_count": sum(bool(r["similar_matches"]) and not r["exact_matches"] for r in ok),
        "brands_matched": sorted(matched.values(), key=lambda a: (-a["exact"], -a["similar"])),
        "results": results,
    }


# ------------------------------------------------------------------ pipeline (FORGE planner)
def to_brand_dna(brand: dict) -> BrandDNA:
    """Registered fingerprints as the shared BrandDNA dataclass."""
    dna = BrandDNA(store_url=brand["official_url"], brand_name=brand["name"],
                   allow_list=[brand["official_domain"]], verified_owner=True)  # registered by the owner
    images = brand.get("images", [])
    logo = next((i for i in images if i["kind"] == "logo"), None)
    if logo:
        dna.logo_sha256, dna.logo_phash = logo["sha256"], logo["phash"]
    for i in images:  # every other registered image (references, extra logos) is a brand photo
        if i is not logo:
            dna.photo_sha256s.append(i["sha256"])
            dna.photo_phashes.append(i["phash"])
    return dna


def registered_brand_for(url: str) -> dict | None:
    """The registered brand whose official domain is this URL's domain (None if none / no database yet)."""
    if not store.db_path().exists():
        return None
    try:
        domain = normalize_domain(url)
        with store.connect() as conn:
            return store.find_brand_by_domain(conn, domain)
    except (ValueError, sqlite3.Error):
        return None


def build_brand_dna(url: str) -> BrandDNA:
    """Planner entry point: crawl the store, then add the owner-registered fingerprints for its domain."""
    crawled, crawl_error = None, None
    try:
        from prarthana.stubs.brand_dna_stub import build_brand_dna as crawl  # reuse the existing crawler
        crawled = crawl(url)
    except Exception as e:
        crawl_error = f"{type(e).__name__}: {e}"
    brand = registered_brand_for(url)
    if crawled is None and brand is None:
        raise RuntimeError(f"Brand DNA: could not load {url} ({crawl_error}) and no brand is registered for it")
    if brand is None:
        return crawled
    reg = to_brand_dna(brand)
    if crawled is None:
        return reg
    crawled.brand_name = reg.brand_name or crawled.brand_name
    if reg.logo_sha256:  # the owner-uploaded logo is authoritative
        if crawled.logo_sha256 and crawled.logo_sha256 != reg.logo_sha256:
            crawled.photo_sha256s.append(crawled.logo_sha256)
            crawled.photo_phashes.append(crawled.logo_phash)
        crawled.logo_sha256, crawled.logo_phash = reg.logo_sha256, reg.logo_phash
    for sha, ph in zip(reg.photo_sha256s, reg.photo_phashes):
        if sha not in crawled.photo_sha256s and sha != crawled.logo_sha256:
            crawled.photo_sha256s.append(sha)
            crawled.photo_phashes.append(ph)
    crawled.verified_owner = True
    for d in reg.allow_list:
        if d not in crawled.allow_list:
            crawled.allow_list.append(d)
    return crawled
