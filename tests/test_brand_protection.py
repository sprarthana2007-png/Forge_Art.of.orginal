"""Combined workflow (NameNet + Brand DNA), website fetching, storage and FORGE planner integration."""
import http.server
import threading
from functools import partial

import pytest

import schemas
from surabhi import brand_dna, protection, store, web
from tests.surabhi_helpers import (CORRUPT, LOGO, PHOTO_A, PHOTO_A_RESIZED, PHOTO_B, UNRELATED, brand_db,  # noqa: F401
                                   fake_resolver)


@pytest.fixture
def site(tmp_path):
    """A local fake shop: exact logo copy, resized product photo, unrelated photo, corrupt file, 404."""
    (tmp_path / "logo.png").write_bytes(LOGO)
    (tmp_path / "p1.jpg").write_bytes(PHOTO_A_RESIZED)
    (tmp_path / "other.jpg").write_bytes(UNRELATED)
    (tmp_path / "broken.jpg").write_bytes(CORRUPT)
    (tmp_path / "index.html").write_text(
        '<html><head><title>Northstar Sale - 90% off</title><meta property="og:image" content="/p1.jpg">'
        '</head><body><img class="site-logo" src="logo.png"><img data-src="other.jpg"><img src="broken.jpg">'
        '<img src="missing.png"></body></html>')
    handler = partial(http.server.SimpleHTTPRequestHandler, directory=str(tmp_path))
    handler.log_message = lambda *a: None
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/"
    srv.shutdown()


@pytest.fixture
def northstar(brand_db):
    return brand_dna.register_brand("Northstar", "https://northstar.com", logo=LOGO,
                                    reference_images=[PHOTO_A, PHOTO_B])["brand"]


LIVE = fake_resolver({"northstar.com": ["1.1.1.1"], "northstar-sale.shop": ["6.6.6.6"],
                      "summer-deals.shop": ["6.6.6.7"]})


def via(url):
    """Fetch the local test site whatever domain is being investigated."""
    return lambda target: web.fetch_site(url)


def test_fetch_site_collects_images_and_reports_errors(site):
    out = web.fetch_site(site)
    assert out["ok"] and out["title"] == "Northstar Sale - 90% off"
    roles = {i["name"]: i["role"] for i in out["images"]}
    assert roles == {"p1.jpg": "og_image", "logo.png": "logo", "other.jpg": "image", "broken.jpg": "image"}
    assert [e["error"] for e in out["image_errors"]] == ["HTTP 404"]


def test_fetch_site_unreachable():
    out = web.fetch_site("http://127.0.0.1:9/")  # discard port: connection refused
    assert out["ok"] is False and out["error"] and out["images"] == []
    assert web.fetch_site("ftp://example.com")["error"] == "only http(s) URLs can be fetched"


def test_lookalike_domain_with_copied_assets_is_high_risk(northstar, site):
    r = protection.investigate("https://northstar-sale.shop/", resolver=LIVE, age_lookup=lambda d: 9,
                               fetcher=via(site))
    assert r["brand"]["id"] == northstar["id"]
    assert r["name_net"]["classification"] == "suspicious"
    assert "keyword_added" in [t["type"] for t in r["name_net"]["techniques"]]
    dna = r["brand_dna"]
    assert (dna["images_analysed"], dna["exact_match_count"], dna["similar_match_count"]) == (3, 1, 1)
    assert [i["name"] for i in dna["invalid_images"]] == ["broken.jpg"]
    risk = r["risk"]
    assert risk["level"] == "high" and risk["label"] == "likely_fake" and risk["malicious_evidence"] is True
    assert risk["components"]["content"] == 50 and risk["score"] >= 70
    assert any("exact copy of the registered logo" in x for x in risk["reasons"])


def test_name_similarity_alone_is_capped(northstar):
    r = protection.investigate("northstar-sale.shop", resolver=LIVE, age_lookup=lambda d: 9,
                               fetcher=lambda t: {"ok": True, "images": [], "url": t, "final_url": t,
                                                  "status_code": 200, "title": "", "error": "", "image_errors": []})
    risk = r["risk"]
    assert risk["level"] == "medium" and risk["score"] <= protection.NAME_ONLY_CAP
    assert risk["malicious_evidence"] is False and risk["basis"] == "name similarity only"
    assert any("not evidence of malicious behaviour" in x for x in risk["reasons"])


def test_unrelated_domain_with_copied_images_is_flagged_but_not_high(northstar, site):
    r = protection.investigate("summer-deals.shop", resolver=LIVE, fetcher=via(site))
    assert r["name_net"]["classification"] == "unrelated"
    assert r["risk"]["level"] == "medium" and r["risk"]["components"]["name"] == 0
    assert any("reseller" in x for x in r["risk"]["reasons"])


def test_unreachable_website_and_uploaded_images(northstar):
    down = lambda t: web.fetch_site("http://127.0.0.1:9/")  # noqa: E731
    r = protection.investigate("n0rthstar.com", images=[("upload.jpg", PHOTO_A_RESIZED)], check_network=False,
                               fetcher=down)
    assert r["website"]["ok"] is False
    assert r["brand_dna"]["similar_match_count"] == 1
    assert any("could not be loaded" in x for x in r["risk"]["reasons"])
    assert r["risk"]["level"] in ("medium", "high")


def test_official_domain_is_not_flagged(northstar, site):
    r = protection.investigate("https://www.northstar.com/", fetcher=via(site), check_network=False)
    assert r["risk"]["level"] == "official" and r["risk"]["score"] == 0


def test_investigate_validates_input(brand_db):
    with pytest.raises(ValueError, match="Register a brand"):
        protection.investigate("northstar.com", fetch_website=False)
    brand_dna.register_brand("Northstar", "northstar.com", logo=LOGO)
    with pytest.raises(ValueError):
        protection.investigate("not a domain", fetch_website=False)
    with pytest.raises(ValueError, match="No registered brand"):
        protection.investigate("northstar.net", brand_id=999, fetch_website=False)


def test_picks_the_brand_the_evidence_points_to(brand_db, site):
    brand_dna.register_brand("Acme", "acme.com", logo=PHOTO_B)
    ns = brand_dna.register_brand("Northstar", "northstar.com", logo=LOGO)["brand"]
    r = protection.investigate("acme-store.shop", fetcher=via(site), check_network=False)
    assert r["brand"]["id"] == ns["id"]  # the copied logo outweighs the closer name


def test_reports_and_findings_are_stored(northstar, site):
    protection.investigate("northstar-sale.shop", resolver=LIVE, age_lookup=lambda d: 9, fetcher=via(site))
    with store.connect() as conn:
        inv = store.list_investigations(conn)
        st = store.stats(conn)
        findings = store.list_findings(conn)
    assert len(inv) == 1 and inv[0]["risk_level"] == "high" and inv[0]["report"]["domain"] == "northstar-sale.shop"
    assert st["brands"] == 1 and st["fingerprints"] == 3 and st["images_analysed"] == 3
    assert st["exact_matches"] == 1 and st["similar_matches"] == 1 and st["suspicious_domains"] == 1
    assert findings[0]["domain"] == "northstar-sale.shop"


def test_report_converts_to_shared_suspect(northstar, site):
    r = protection.investigate("northstar-sale.shop", resolver=LIVE, age_lookup=lambda d: 9, fetcher=via(site),
                               save=False)
    s = protection.to_suspect(r)
    assert isinstance(s, schemas.Suspect)
    assert s.label == "likely_fake" and s.logo_match  # the copied logo, as the engine records it
    assert s.image_exact_matches == []
    assert [set(m) for m in s.image_similar_matches] == [{"url", "phash", "path"}]  # engine format
    assert s.image_similar_matches[0]["url"].endswith("/p1.jpg")
    assert s.domain_age_days == 9 and s.ip == "6.6.6.6"


# ------------------------------------------------------------------ FORGE planner integration
def test_planner_uses_surabhi_modules():
    from prarthana.teammate import get_build_brand_dna, get_name_net
    assert get_build_brand_dna()[1] == "surabhi.brand_dna.build_brand_dna"
    assert get_name_net()[1] == "surabhi.name_net.name_net"


def test_build_brand_dna_merges_registered_fingerprints(northstar, monkeypatch):
    import prarthana.stubs.brand_dna_stub as stub
    crawled_logo = schemas.file_sha256(UNRELATED)
    monkeypatch.setattr(stub, "build_brand_dna", lambda url: schemas.BrandDNA(
        store_url=url, brand_name="Crawled", logo_sha256=crawled_logo, logo_phash=schemas.photo_phash(UNRELATED),
        allow_list=["northstar.com"]))
    dna = brand_dna.build_brand_dna("https://northstar.com/")
    assert dna.brand_name == "Northstar" and dna.logo_sha256 == schemas.file_sha256(LOGO)
    assert crawled_logo in dna.photo_sha256s  # the crawled logo is kept as a photo
    assert schemas.file_sha256(PHOTO_A) in dna.photo_sha256s and len(dna.photo_sha256s) == len(dna.photo_phashes)


def test_build_brand_dna_falls_back_to_registry_when_site_is_down(northstar, monkeypatch):
    import prarthana.stubs.brand_dna_stub as stub

    def down(url):
        raise TimeoutError("site down")
    monkeypatch.setattr(stub, "build_brand_dna", down)
    dna = brand_dna.build_brand_dna("https://northstar.com/")
    assert dna.logo_sha256 == schemas.file_sha256(LOGO)
    with pytest.raises(RuntimeError, match="no brand is registered"):
        brand_dna.build_brand_dna("https://unknown-brand.com/")


def test_investigator_uses_surabhi_scam_phrases():
    from prarthana.investigator import _find_scam_phrase_fn, scam_phrases
    from surabhi.scam_phrases import find_scam_phrases
    assert _find_scam_phrase_fn() is find_scam_phrases
    found = scam_phrases("Payment by UPI only. Offer ends in 09:59! 90% off. No returns.")
    assert {"UPI only", "Countdown pressure", "Huge discount", "No returns"} <= set(found)
    assert scam_phrases("Free shipping on orders above Rs 999.") == []
