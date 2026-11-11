"""#16 relaunch tracking."""
import json

import pytest

from schemas import BrandDNA, ForgeState, Suspect


@pytest.fixture()
def cache(tmp_path, monkeypatch):
    import prarthana.relaunch as relaunch
    monkeypatch.setattr(relaunch, "CACHE", tmp_path)
    return tmp_path


def _dna():
    return BrandDNA(store_url="https://brand.example/", brand_name="Brand", photo_sha256s=["brandphoto"],
                    logo_sha256="brandlogo")


def _old(**kw):
    return Suspect(url="https://old-fake.example/", domain="old-fake.example", status="confirmed", **kw)


def test_shared_contact_is_a_relaunch(cache):
    from prarthana.relaunch import detect
    from prarthana.scorer import score_suspect
    old = _old(contact_hashes=["upi1"], contacts_masked=["frXXXXbl"])
    new = Suspect(url="https://new-fake.example/", domain="new-fake.example", contact_hashes=["upi1", "x"],
                  contacts_masked=["frXXXXbl", "98XXXXXX10"])
    assert detect([new], [{"url": old.url, "domain": old.domain, "contact_hashes": old.contact_hashes}], _dna())
    assert new.relaunch_of == "old-fake.example" and "same 1 contact (frXXXXbl)" in new.relaunch_evidence[0]
    score_suspect(new, _dna())
    assert any(r.startswith("+10: relaunch of a fake you already confirmed (old-fake.example)") for r in new.reasons)


def test_brand_images_and_private_ips_do_not_count(cache):
    from prarthana.relaunch import match
    old = {"url": "u", "domain": "old.example", "image_sha256s": ["brandphoto", "qr-code"], "ip": "10.0.0.1"}
    s = Suspect(url="https://n.example/", domain="n.example", image_sha256s=["brandphoto"], ip="10.0.0.1")
    assert match(s, old, _dna()) == []
    s.image_sha256s = ["brandphoto", "qr-code"]
    assert match(s, old, _dna()) == ["same 1 non-brand image (sha qr-code)"]
    s2 = Suspect(url="https://m.example/", domain="m.example", ip="104.21.9.9")
    assert match(s2, {**old, "ip": "104.21.9.9"}, _dna()) == ["same IP address 104.21.9.9"]


def test_confirm_remembers_for_later_runs(cache):
    from prarthana.alert import confirm
    from prarthana.relaunch import load_history
    s = Suspect(url="https://gone.example/", domain="gone.example", contact_hashes=["h"], ip="104.1.1.1")
    confirm(s, True)
    confirm(Suspect(url="https://legit.example/", domain="legit.example"), False)
    hist = load_history()
    assert [h["domain"] for h in hist] == ["gone.example"] and hist[0]["contact_hashes"] == ["h"]
    raw = json.loads((cache / "confirmed_fakes.json").read_text())
    assert "contacts" not in json.dumps(raw).replace("contact_hashes", "").replace("contacts_masked", "")


def test_planner_investigates_ip_relaunch_first_and_scores_it(cache, monkeypatch):
    import prarthana.planner as planner
    monkeypatch.setattr(planner, "gemini_json", lambda *a, **k: None)
    old = _old(ip="104.21.3.4", contact_hashes=["c-old"])
    a = Suspect(url="https://a.example/", domain="a.example", found_by="name_net")
    b = Suspect(url="https://b.example/", domain="b.example", found_by="urlscan_title", ip="104.21.3.4")
    st = ForgeState(brand_dna=_dna(), suspects=[old, a, b], round_new=[a.url, b.url])
    out = planner.node_prioritise(st)
    assert out["round_new"][0] == b.url and out["cloak_queue"][0] == b.url
    b.contact_hashes = ["c-old"]
    st2 = ForgeState(brand_dna=_dna(), suspects=[old, a, b], round_new=[a.url, b.url])
    planner.node_score(st2)
    assert b.relaunch_of == "old-fake.example" and a.relaunch_of == ""
    assert any("relaunch of a fake you already confirmed" in r for r in b.reasons)
