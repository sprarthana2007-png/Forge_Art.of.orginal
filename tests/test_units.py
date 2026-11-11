from schemas import BrandDNA, Suspect, hash_contact, mask_contact
from prarthana.scorer import score_suspect, group_campaigns
from prarthana.investigator import hashed_contacts, extract_prices


def dna():
    return BrandDNA(store_url="https://realbrand.in", brand_name="Real", prices={"Kurta": 1000},
                    official_contact_hashes=[hash_contact("+91 98765 43210")], allow_list=["partner.in"])


# ---------------- masking / hashing
def test_mask_phone():
    assert mask_contact("+91 98765 43221") == "98XXXXXX21"
    assert mask_contact("9876543221") == "98XXXXXX21"


def test_mask_upi_and_email():
    assert mask_contact("Shop@OkAxis") == "shXXXXXXXis"
    assert mask_contact("ab@x.in").startswith("ab") and mask_contact("ab@x.in").endswith("in")


def test_hash_normalises():
    assert hash_contact("+91 98765-43210") == hash_contact("09876543210") == hash_contact("9876543210")
    assert hash_contact("A@B.COM") == hash_contact("a@b.com")
    assert len(hash_contact("x@y")) == 64


def test_no_raw_contacts_kept():
    h, m = hashed_contacts("call 98765 43221 or pay fake@ybl or mail a.b@c.com")
    assert len(h) == 3 and all(len(x) == 64 for x in h)
    assert "9876543221" not in " ".join(m) and "fake@ybl" not in m


def test_extract_prices():
    p = extract_prices("Kurta ₹1,299 Saree Rs. 499 Bag INR 99", ["Kurta", "Saree", "Bag"])
    assert p == {"Kurta": 1299.0, "Saree": 499.0, "Bag": 99.0}


# ---------------- scorer math
def test_full_score():
    s = Suspect(url="https://fake.shop", image_exact_matches=[{"sha256": "a"}], logo_match=True,
                prices_found={"Kurta": 600}, contact_hashes=[hash_contact("9999988888")],
                contacts_masked=["99XXXXXX88"], domain_age_days=5)
    score_suspect(s, dna())
    assert s.score == 90 and s.label == "likely_fake" and len(s.reasons) == 5


def test_price_threshold_exactly_40():
    s = Suspect(url="https://x.shop", prices_found={"Kurta": 601})
    score_suspect(s, dna())
    assert s.score == 0
    s = Suspect(url="https://x.shop", prices_found={"Kurta": 600})
    score_suspect(s, dna())
    assert s.score == 15


def test_labels_boundaries():
    s = Suspect(url="https://x.shop", image_similar_matches=[{"phash": "a"}], logo_match=True)
    score_suspect(s, dna())
    assert s.score == 50 and s.label == "suspicious"
    s = Suspect(url="https://x.shop", logo_match=True, contact_hashes=[hash_contact("+91 98765 43210")],
                domain_age_days=30)
    score_suspect(s, dna())
    assert s.score == 20 and s.label == "ignored"  # official contact, 30 days is not "younger than 30"


def test_allow_list_always_ignored():
    for url in ("https://partner.in/x", "https://realbrand.in/p", "https://www.realbrand.in"):
        s = Suspect(url=url, image_exact_matches=[1], logo_match=True, prices_found={"Kurta": 1},
                    contact_hashes=["zz"], domain_age_days=1)
        score_suspect(s, dna())
        assert s.label == "ignored" and s.score == 0


def test_scam_phrases_no_points():
    s = Suspect(url="https://x.shop", scam_phrases=["UPI only"])
    score_suspect(s, dna())
    assert s.score == 0 and any("UPI only" in r for r in s.reasons)


def test_campaign_grouping():
    a = Suspect(url="https://a.shop", label="likely_fake", contact_hashes=["h1"])
    b = Suspect(url="https://b.shop", label="suspicious", contact_hashes=["h2"], image_sha256s=["img"])
    c = Suspect(url="https://c.shop", label="likely_fake", contact_hashes=["h1", "h3"], image_sha256s=["img"])
    d = Suspect(url="https://d.shop", label="likely_fake", contact_hashes=["h9"])
    e = Suspect(url="https://e.shop", label="ignored", contact_hashes=["h1"])
    for s in (a, b, c, d, e):
        s.domain = s.url.split("//")[1]
    group_campaigns([a, b, c, d, e])
    assert a.campaign_id and a.campaign_id == b.campaign_id == c.campaign_id
    assert d.campaign_id is None and e.campaign_id is None
