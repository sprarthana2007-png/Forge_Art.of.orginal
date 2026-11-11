"""NameNet: normalisation, technique detection, similarity, network evidence and classification."""
import pytest

from schemas import BrandDNA, Suspect
from surabhi import name_net as nn
from tests.surabhi_helpers import fake_resolver

OFFICIAL = "northstar.com"


def types(domain, brand="Northstar"):
    return [t["type"] for t in nn.analyse_domain(domain, OFFICIAL, brand)["techniques"]]


@pytest.mark.parametrize("raw, norm", [
    ("HTTPS://WWW.NorthStar.com:8443/path?q=1", "northstar.com"), ("northstar.com.", "northstar.com"),
    ("xn--nrthstar-nbh.com", "nоrthstar.com"), ("http://127.0.0.1:8000/", "127.0.0.1"),
])
def test_normalize_domain(raw, norm):
    assert nn.normalize_domain(raw) == norm


@pytest.mark.parametrize("bad", ["", "   ", "http://", "exa mple.com", "-bad-.com", "a..b"])
def test_normalize_domain_rejects_garbage(bad):
    with pytest.raises(ValueError):
        nn.normalize_domain(bad)


def test_normalize_brand_and_split():
    assert nn.normalize_brand("North Star Outdoors®") == "northstaroutdoors"
    assert nn.normalize_brand("Café Noir") == "cafenoir"
    assert nn.split_domain("shop.northstar.co.in") == ("shop", "northstar", "co.in")
    assert nn.split_domain("northstar.com") == ("", "northstar", "com")


@pytest.mark.parametrize("domain, expected", [
    ("northstarr.com", "repetition"), ("rnorthstar.com", "insertion"), ("nortstar.com", "deletion"),
    ("northsrar.com", "substitution"), ("northtsar.com", "transposition"), ("n0rthstar.com", "lookalike_characters"),
    ("nоrthstar.com", "homoglyph"), ("north-star.com", "hyphenation"), ("northstar-sale.com", "keyword_added"),
    ("northstaroutlet.in", "keyword_added"), ("northstar.net", "tld_swap"),
    ("northstar.com.secure-login.xyz", "subdomain_brand"), ("northstardust.com", "brand_embedded"),
])
def test_detects_each_technique(domain, expected):
    assert expected in types(domain)


def test_combined_typo_and_keyword():
    t = types("nothstar-sale.shop")
    assert "keyword_added" in t and "deletion" in t


def test_multi_character_lookalikes():
    r = nn.analyse_domain("comer.com", "corner.com")
    assert r["similarity"] == 100 and r["techniques"][0]["type"] == "lookalike_characters"
    assert "'m' looks like 'rn'" in r["techniques"][0]["detail"]
    assert "'vv' looks like 'w'" in nn.analyse_domain("vvidget.com", "widget.com")["techniques"][0]["detail"]


def test_substitution_reason_names_the_kind():
    assert "neighbouring key" in nn.analyse_domain("northsrar.com", OFFICIAL)["techniques"][0]["detail"]


@pytest.mark.parametrize("domain", ["google.com", "starnorth.com", "shopify.com", "example.org"])
def test_unrelated_domains_are_not_flagged(domain):
    r = nn.analyse_domain(domain, OFFICIAL, "Northstar")
    assert r["classification"] == "unrelated" and r["similarity"] < 70


def test_official_and_allow_listed():
    assert nn.analyse_domain("https://shop.northstar.com/x", OFFICIAL)["classification"] == "official"
    assert nn.analyse_domain("northstar.in", OFFICIAL, allow_list=["northstar.in"])["classification"] == "official"


def test_similarity_orders_closer_names_higher():
    s = {d: nn.analyse_domain(d, OFFICIAL)["similarity"] for d in ("northstar.net", "northstarr.com", "nrthstr.com",
                                                                   "google.com")}
    assert s["northstar.net"] == 100 > s["northstarr.com"] > s["nrthstr.com"] > s["google.com"]


def test_string_metrics():
    assert nn.edit_ops("northstar", "northtsar")[0] == 1
    assert nn.edit_ops("kitten", "sitting")[0] == 3
    assert round(nn.jaro_winkler("martha", "marhta"), 3) == 0.961
    assert nn.jaro_winkler("abc", "abc") == 1.0 and nn.jaro_winkler("", "x") == 0.0


def test_name_similarity_alone_is_never_malicious():
    r = nn.analyse_domain("n0rthstar.com", OFFICIAL)
    assert r["malicious_evidence"] is False and r["classification"] == "lookalike"
    assert nn.EVIDENCE_NOTE in r["reasons"]


def test_network_evidence_classification():
    live = fake_resolver({OFFICIAL: ["1.1.1.1"], "northstar-sale.com": ["6.6.6.6"], "northstar.in": ["1.1.1.1"],
                          "northstarr.com": ["7.7.7.7"]})
    young = nn.analyse_domain("northstar-sale.com", OFFICIAL, check_network=True, resolver=live, age_lookup=lambda d: 12)
    assert young["classification"] == "suspicious" and "recently_registered" in young["signals"]
    assert young["malicious_evidence"] is False
    old = nn.analyse_domain("northstarr.com", OFFICIAL, check_network=True, resolver=live, age_lookup=lambda d: 4000)
    assert old["classification"] == "lookalike"
    owned = nn.analyse_domain("northstar.in", OFFICIAL, check_network=True, resolver=live, age_lookup=lambda d: None)
    assert owned["classification"] == "likely_brand_owned"
    gone = nn.analyse_domain("n0rthstar.com", OFFICIAL, check_network=True, resolver=live)
    assert gone["classification"] == "not_registered"


def test_dns_outage_is_not_reported_as_unregistered():
    dead = fake_resolver({})  # nothing resolves, not even the official domain
    r = nn.analyse_domain("n0rthstar.com", OFFICIAL, check_network=True, resolver=dead)
    assert r["classification"] == "lookalike" and r["dns"]["status"] == "error"
    assert any("could not be verified" in x for x in r["reasons"])


def test_dns_errors_and_missing_age_are_handled():
    def broken(d):
        return {"status": "error", "addresses": [], "error": "DNS lookup timed out after 4s"}
    r = nn.analyse_domain("northstar-sale.com", OFFICIAL, check_network=True, resolver=broken)
    assert any("timed out" in x for x in r["reasons"]) and r["classification"] == "lookalike"
    assert nn.resolve_dns("definitely-not-a-real-host.invalid")["status"] in ("no_such_domain", "error")


def test_generate_variants():
    v = nn.generate_variants("northstar.com")
    for d in ("northstar.in", "north-star.com", "northstar-sale.com", "n0rthstar.com", "northstarr.com",
              "nortstar.com", "nrothstar.com", "northstar.co.in"):
        assert d in v
    assert "northstar.com" not in v and len(v) == len(set(v))
    assert all(nn.normalize_domain(d) == d for d in v)
    assert len(nn.generate_variants("northstar.com", limit=10)) == 10


def test_check_domains_stores_findings(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_BRAND_DB", str(tmp_path / "b.db"))
    from surabhi import store
    with store.connect() as conn:
        bid = store.upsert_brand(conn, "Northstar", "https://northstar.com", "northstar.com")
    brand = {"id": bid, "name": "Northstar", "official_domain": "northstar.com"}
    out = nn.check_domains(brand, ["northstar-sale.com", "google.com", "bad domain", "northstar.com"],
                           check_network=False)
    assert [o["classification"] for o in out] == ["lookalike", "unrelated", "invalid", "official"]
    with store.connect() as conn:
        saved = store.list_findings(conn)
    assert sorted(f["domain"] for f in saved) == ["google.com", "northstar-sale.com"]
    assert saved[0]["reasons"] and isinstance(saved[0]["techniques"], list)


def test_name_net_planner_hook_returns_live_lookalikes_only():
    live = fake_resolver({OFFICIAL: ["1.1.1.1"], "northstar-sale.com": ["6.6.6.6"], "northstar.in": ["1.1.1.1"]})
    out = nn.name_net(BrandDNA(store_url="https://northstar.com/", brand_name="Northstar"),
                      resolver=live, age_lookup=lambda d: 20)
    assert [s.domain for s in out] == ["northstar-sale.com"]  # northstar.in shares the official IP
    s = out[0]
    assert isinstance(s, Suspect) and s.found_by == "name_net" and s.ip == "6.6.6.6" and s.domain_age_days == 20
    assert s.reasons


def test_name_net_skips_local_stores_and_dns_outage():
    assert nn.name_net(BrandDNA(store_url="http://localhost:8000/")) == []
    assert nn.name_net(BrandDNA(store_url="https://northstar.com/"), resolver=fake_resolver({})) == []
