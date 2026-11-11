"""#13 cloaking check: a page that shows bots a clean page and Instagram-ad shoppers the fake sale must be caught;
a page that shows everyone the same content must not. Local only (visit 3 / urlscan is skipped for localhost)."""
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SERVER = ROOT / "tests" / "fixtures" / "cloaking_server.py"


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _start(args, port):
    p = subprocess.Popen([sys.executable, *args], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(50):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            return p
        except OSError:
            time.sleep(0.1)
    p.kill()
    raise RuntimeError("server did not start")


@pytest.fixture()
def isolated_cache(tmp_path, monkeypatch):
    import prarthana.cloaking as cloaking
    import prarthana.common as common
    import prarthana.investigator as investigator
    for mod in (common, cloaking, investigator):
        monkeypatch.setattr(mod, "CACHE", tmp_path / "cache")
    monkeypatch.setenv("GEMINI_API_KEY", "")
    return tmp_path


@pytest.fixture()
def servers():
    cp, sp = _free_port(), _free_port()
    procs = [_start([str(SERVER), "--port", str(cp), "--mode", "cloak"], cp),
             _start([str(SERVER), "--port", str(sp), "--mode", "same"], sp)]
    yield f"http://localhost:{cp}/", f"http://localhost:{sp}/"
    for p in procs:
        p.kill()


def test_detects_cloaking(isolated_cache, servers):
    from schemas import Suspect
    from prarthana.cloaking import check_cloaking
    cloak_url, _ = servers
    r = check_cloaking(Suspect(url=cloak_url), use_cache=False)
    assert r["cloaking"] is True
    assert [v["label"] for v in r["visits"]] == ["bot", "Instagram-ad shopper"]
    assert "fbclid=" in r["visits"][1]["final_url"] and "utm_medium=paid" in r["visits"][1]["final_url"]
    assert all(Path(v["screenshot_path"]).exists() for v in r["visits"])
    assert any("different text" in d for d in r["differences"])
    assert r["visit3_status"].startswith("skipped: local address")
    assert "text" not in r["visits"][0]                       # full page text is not kept in the result


def test_same_content_is_not_cloaking(isolated_cache, servers):
    from schemas import Suspect
    from prarthana.cloaking import check_cloaking
    _, same_url = servers
    r = check_cloaking(Suspect(url=same_url), use_cache=False)
    assert r["cloaking"] is False and r["differences"] == []
    assert r["comparisons"][0]["text_similarity"] == 1.0
    # phone vs desktop screenshots differ visually, but identical text means layout, not cloaking
    assert r["comparisons"][0]["different"] is False


def test_cloaked_clone_scores_with_shopper_evidence(isolated_cache, servers):
    """Investigator switches to the Instagram-shopper view when cloaking is found, so the copied photos,
    prices and contacts hidden from bots are still collected; cloaking adds +10."""
    from schemas import BrandDNA, Suspect, file_sha256, photo_phash, hash_contact
    from prarthana.investigator import investigate
    from prarthana.scorer import score_all
    img = ROOT / "tests" / "fixtures" / "store" / "img"
    photos = [(img / f"p{i}.png").read_bytes() for i in (1, 2, 3)]
    dna = BrandDNA(store_url="http://localhost:1/", brand_name="Mitti & Dhaaga",
                   logo_sha256=file_sha256((img / "logo.png").read_bytes()),
                   favicon_sha256=file_sha256((img / "favicon.png").read_bytes()),
                   photo_sha256s=[file_sha256(b) for b in photos], photo_phashes=[photo_phash(b) for b in photos],
                   product_names=["Indigo Block Print Kurta", "Terracotta Clay Earrings", "Handloom Cotton Saree"],
                   prices={"Indigo Block Print Kurta": 2499, "Terracotta Clay Earrings": 899,
                           "Handloom Cotton Saree": 4999},
                   official_contact_hashes=[hash_contact("hello@mittidhaaga.example"), hash_contact("9876543210")])
    cloak_url, same_url = servers
    cloaked, same = Suspect(url=cloak_url), Suspect(url=same_url)
    for s in (cloaked, same):
        investigate(s, dna, use_cache=False)
    score_all([cloaked, same], dna)
    assert cloaked.cloaking is True and same.cloaking is False
    assert cloaked.score == same.score + 10 == 90
    assert any("Instagram-ad visitor" in r for r in cloaked.reasons)
    assert cloaked.image_exact_matches  # evidence came from the shopper view, not the maintenance page


def test_scorer_cloaking_points():
    from schemas import BrandDNA, Suspect
    from prarthana.scorer import score_suspect
    dna = BrandDNA(store_url="https://real.example")
    s = Suspect(url="https://x.example", cloaking=True,
                cloaking_details={"differences": ["The Instagram-ad shopper saw different text than the bot."]})
    score_suspect(s, dna)
    assert s.score == 10 and s.label == "ignored"     # cloaking alone never flags a site
    assert "different page to an Instagram-ad visitor than to a normal visitor" in s.reasons[0]


def test_compare_redirect_to_other_domain():
    from prarthana.cloaking import compare
    a = {"label": "bot", "final_url": "https://shop.example/", "text": "same words here", "phash": None}
    b = {"label": "Instagram-ad shopper", "final_url": "https://pay-now.example/x", "text": "same words here",
         "phash": None}
    c = compare(a, b)
    assert c["different"] and "pay-now.example" in c["reasons"][0]


def test_visit3_defaults_to_singapore(monkeypatch):
    import prarthana.cloaking as cloaking
    monkeypatch.delenv("FORGE_CLOAK_COUNTRY", raising=False)
    assert cloaking.country() == "sg" and cloaking.country_name() == "Singapore"
    assert cloaking.network_label() == "Other country (urlscan)"
    monkeypatch.setenv("FORGE_CLOAK_COUNTRY", "JP")
    assert cloaking.country() == "jp" and cloaking.country_name() == "Japan"
    v = {"label": "Other country (urlscan)", "phrase": "visit from Japan (urlscan)", "final_url": "https://a.example/",
         "text": "one two three", "phash": None}
    b = {"label": "bot", "final_url": "https://a.example/", "text": "totally different words here", "phash": None}
    assert "visit from Japan (urlscan)" in cloaking.compare(b, v)["reasons"][0]
