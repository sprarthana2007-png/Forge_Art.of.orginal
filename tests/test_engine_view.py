"""The Brand protection page shows what the FORGE engine found instead of zeros."""
from schemas import BrandDNA, ForgeState, Suspect
from surabhi import engine_view


def _state():
    dna = BrandDNA(store_url="http://localhost:8000/official/", brand_name="Northstar", logo_sha256="a" * 64,
                   favicon_sha256="", photo_sha256s=["b" * 64, "c" * 64, "d" * 64])
    s1 = Suspect(url="http://localhost:8001/case1/", domain="localhost:8001", found_by="manual", score=80,
                 label="likely_fake", image_exact_matches=["x", "y"], image_similar_matches=["z"])
    s2 = Suspect(url="http://northstar-sale.shop/", domain="northstar-sale.shop", found_by="name_net", score=60,
                 label="suspicious")
    return ForgeState(brand_dna=dna, suspects=[s1, s2])


def test_no_state_is_all_zero():
    s = engine_view.engine_summary(None)
    assert not s["has_engine"] and all(v == 0 for v in s["stats"].values())


def test_engine_counts():
    s = engine_view.engine_summary(_state())["stats"]
    assert s == {"brands": 1, "images_analysed": 4 + 3, "exact_matches": 2, "similar_matches": 1,
                 "fingerprints": 8, "suspicious_domains": 1, "investigations": 2, "high_risk": 1}


def test_rows_sorted_and_name_net_listed():
    out = engine_view.engine_summary(_state())
    assert [r["score"] for r in out["suspects"]] == [80, 60]
    assert [r["domain"] for r in out["name_net"]] == ["northstar-sale.shop"]


def test_combined_does_not_count_same_brand_twice():
    out = engine_view.engine_summary(_state())
    sql = {"brands": 1, "images_analysed": 2, "exact_matches": 0, "similar_matches": 0, "fingerprints": 2,
           "suspicious_domains": 0, "investigations": 0}
    same = engine_view.combined_stats(sql, 0, ["localhost:8000"], out)
    other = engine_view.combined_stats(sql, 0, ["northstar.com"], out)
    assert same["brands"] == 1 and other["brands"] == 2
    assert same["investigations"] == 2 and same["high_risk"] == 1
