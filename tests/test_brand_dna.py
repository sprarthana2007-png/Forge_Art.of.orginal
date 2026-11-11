"""Brand DNA: fingerprinting, registration, storage and comparison (no network)."""
import pytest

import schemas
from surabhi import brand_dna, store
from tests.surabhi_helpers import (CORRUPT, LOGO, PHOTO_A, PHOTO_A_RESIZED, PHOTO_B, SVG, UNRELATED, brand_db,  # noqa: F401
                                   encode, picture)


def test_fingerprint_uses_shared_schema_helpers():
    fp = brand_dna.fingerprint_image(LOGO, "logo.png")
    assert fp["sha256"] == schemas.file_sha256(LOGO)
    assert fp["phash"] == schemas.photo_phash(LOGO)
    assert (fp["width"], fp["height"], fp["format"]) == (200, 200, "PNG")


def test_module_does_not_reimplement_hashing():
    src = open(brand_dna.__file__, encoding="utf-8").read()
    assert "hashlib" not in src and "imagehash.phash" not in src
    assert brand_dna.file_sha256 is schemas.file_sha256 and brand_dna.photo_phash is schemas.photo_phash
    assert brand_dna.is_similar is schemas.is_similar


@pytest.mark.parametrize("data, msg", [
    (b"", "empty"), (CORRUPT, "damaged|not a recognised"), (b"plain text", "not a recognised image"),
    (b"<svg xmlns='http://www.w3.org/2000/svg'><rect", "SVG could not be rendered"),
    (LOGO[: len(LOGO) // 2], "damaged"), (b"x" * (brand_dna.MAX_IMAGE_BYTES + 1), "larger than"),
])
def test_invalid_images_are_rejected_with_a_reason(data, msg):
    with pytest.raises(brand_dna.ImageError, match=msg):
        brand_dna.fingerprint_image(data)


def test_register_brand_stores_fingerprints_and_rejects_bad_files(brand_db, tmp_path):
    ref = tmp_path / "photo-b.jpg"
    ref.write_bytes(PHOTO_B)
    out = brand_dna.register_brand("Northstar", "www.northstar.com", logo=("logo.png", LOGO),
                                   reference_images=[("a.jpg", PHOTO_A), ref, ("bad.jpg", CORRUPT),
                                                     tmp_path / "missing.png"])
    b = out["brand"]
    assert (b["name"], b["official_domain"], b["official_url"]) == ("Northstar", "northstar.com",
                                                                     "https://www.northstar.com")
    assert out["fingerprint"] == {"status": "complete", "logos": 1, "references": 2}
    assert sorted(r["filename"] for r in out["rejected"]) == ["bad.jpg", "missing.png"]
    with store.connect() as conn:
        rows = conn.execute("SELECT kind, sha256 FROM bp_brand_images ORDER BY id").fetchall()
    assert [(r["kind"], r["sha256"]) for r in rows] == [("logo", schemas.file_sha256(LOGO)),
                                                        ("reference", schemas.file_sha256(PHOTO_A)),
                                                        ("reference", schemas.file_sha256(PHOTO_B))]
    for img in b["images"]:
        assert open(img["stored_path"], "rb").read()  # a copy is kept for the dashboard


def test_registering_again_updates_the_brand_without_duplicates(brand_db):
    brand_dna.register_brand("Northstar", "https://northstar.com", logo=LOGO)
    again = brand_dna.register_brand("Northstar Outdoors", "http://www.northstar.com/", logo=LOGO,
                                     reference_images=[PHOTO_A])
    assert len(again["duplicates"]) == 1 and len(again["added"]) == 1
    brands = brand_dna.list_brands()
    assert len(brands) == 1 and brands[0]["name"] == "Northstar Outdoors"
    assert brands[0]["fingerprint"]["status"] == "complete"


def test_fingerprint_status():
    assert brand_dna.fingerprint_status({"images": []})["status"] == "missing"
    assert brand_dna.fingerprint_status({"images": [{"kind": "logo"}]})["status"] == "partial"


@pytest.mark.parametrize("name, url", [("", "northstar.com"), ("Northstar", ""), ("Northstar", "not a url")])
def test_register_requires_name_and_valid_url(brand_db, name, url):
    with pytest.raises(ValueError):
        brand_dna.register_brand(name, url)


def test_compare_finds_exact_and_similar_and_reports_invalid(brand_db):
    brand_dna.register_brand("Northstar", "northstar.com", logo=LOGO, reference_images=[PHOTO_A, PHOTO_B])
    res = brand_dna.compare_images({"logo-copy.png": LOGO, "resized.jpg": PHOTO_A_RESIZED,
                                    "unrelated.jpg": UNRELATED, "broken.jpg": CORRUPT})
    by = {r["name"]: r for r in res["results"]}
    assert [m["kind"] for m in by["logo-copy.png"]["exact_matches"]] == ["logo"]
    assert by["resized.jpg"]["exact_matches"] == []
    sim = by["resized.jpg"]["similar_matches"]
    assert len(sim) == 1 and sim[0]["kind"] == "reference" and sim[0]["distance"] <= schemas.PHASH_MATCH
    assert by["unrelated.jpg"]["exact_matches"] == by["unrelated.jpg"]["similar_matches"] == []
    assert by["broken.jpg"]["status"] == "invalid"
    assert (res["images_analysed"], res["exact_match_count"], res["similar_match_count"]) == (3, 1, 1)
    agg = res["brands_matched"][0]
    assert agg["logo_exact"] and agg["exact"] == 1 and agg["similar"] == 1


def test_tiny_images_only_match_exactly(brand_db):
    tiny = encode(picture(5, (16, 16)))
    brand_dna.register_brand("Northstar", "northstar.com", reference_images=[tiny])
    res = brand_dna.compare_images([("tiny-copy.png", tiny), ("tiny-other.png", encode(picture(5, (16, 16)), "BMP"))])
    assert res["exact_match_count"] == 1 and res["similar_match_count"] == 0


def test_compare_with_no_brands_registered(brand_db):
    res = brand_dna.compare_images({"x.png": LOGO})
    assert res["images_analysed"] == 1 and res["brands_matched"] == []


def test_to_brand_dna_maps_registry_to_shared_dataclass(brand_db):
    b = brand_dna.register_brand("Northstar", "northstar.com", logo=LOGO, reference_images=[PHOTO_A])["brand"]
    dna = brand_dna.to_brand_dna(b)
    assert isinstance(dna, schemas.BrandDNA)
    assert dna.logo_sha256 == schemas.file_sha256(LOGO) and dna.photo_sha256s == [schemas.file_sha256(PHOTO_A)]
    assert dna.allow_list == ["northstar.com"] and dna.brand_name == "Northstar"


def test_svg_is_fingerprinted_on_its_rendering():
    real = open(brand_dna.__file__.replace("brand_dna.py", "demo_sites/assets/jacket.svg"), "rb").read()
    fp = brand_dna.fingerprint_image(real, "jacket.svg")
    assert fp["sha256"] == schemas.file_sha256(real)            # SHA-256 on the raw SVG bytes
    assert (fp["format"], fp["width"], fp["height"]) == ("SVG", 400, 400)
    png = schemas.svg_to_png(real)                              # the same artwork as a PNG export
    assert schemas.is_similar(fp["phash"], schemas.photo_phash(png))
    assert brand_dna.fingerprint_image(SVG)["format"] == "SVG"   # a tiny valid SVG is accepted too


@pytest.mark.parametrize("name", ["jacket", "pack", "shoe", "flask", "tent"])
def test_chromium_fallback_matches_cairo(name):
    """Without cairo the SVG is rendered in Chromium; photo-like artwork gets a matching pHash either way.
    (Flat, symmetric marks such as the logo can differ by a few bits between renderers; both sides of a
    comparison use the same renderer, and exact copies always match by SHA-256.)"""
    real = open(brand_dna.__file__.replace("brand_dna.py", f"demo_sites/assets/{name}.svg"), "rb").read()
    chromium = schemas._svg_to_png_chromium(real, schemas.SVG_RENDER_WIDTH)
    assert schemas.is_similar(schemas.photo_phash(chromium), schemas.photo_phash(real))


def test_svg_detection_and_raster_passthrough():
    real = open(brand_dna.__file__.replace("brand_dna.py", "demo_sites/assets/logo.svg"), "rb").read()
    assert schemas.is_svg(real) and schemas.is_svg(b'<?xml version="1.0"?>\n<svg/>') and not schemas.is_svg(LOGO)
    assert schemas.raster_bytes(LOGO) is LOGO                   # raster images are untouched
    assert schemas.photo_phash(real) == schemas.photo_phash(real)  # deterministic


@pytest.mark.parametrize("title, site, name", [
    ("Northstar — Gear for the long way up", "", "Northstar"), ("Mitti & Dhaaga | Handmade", "", "Mitti & Dhaaga"),
    ("Acme: outdoor gear", "", "Acme"), ("Anything — at all", "North Star Co", "North Star Co"),
    ("A very long title without any separator", "", ""), ("", "", ""),
])
def test_brand_name_from_title(title, site, name):
    assert brand_dna.brand_name_from_title(title, site) == name
