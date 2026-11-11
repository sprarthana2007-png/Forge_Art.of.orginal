"""#14 campaign graph: edges + reasons, PNG + self-contained HTML export."""
from schemas import BrandDNA, Suspect
from prarthana.campaign_graph import BRAND, build_graph, export, links_of


def _dna():
    return BrandDNA(store_url="https://brand.example/", brand_name="Brand", photo_sha256s=["brandphoto"],
                    logo_sha256="brandlogo")


def _s(dom, **kw):
    kw.setdefault("label", "likely_fake")
    kw.setdefault("score", 80)
    return Suspect(url=f"https://{dom}/", domain=dom, **kw)


def test_edges_and_reasons():
    a = _s("a.example", ip="104.21.3.4", contact_hashes=["c1"], contacts_masked=["98XXXXXX10"],
           image_sha256s=["brandphoto", "qr"], image_exact_matches=[{"sha256": "brandphoto"}])
    b = _s("b.example", ip="104.21.3.4", contact_hashes=["c1"], image_sha256s=["brandphoto", "qr"])
    c = _s("c.example", ip="10.0.0.5", image_sha256s=["brandphoto"])          # private IP + brand image only
    d = _s("d.example", ip="10.0.0.5")
    ign = _s("ignored.example", label="ignored", contact_hashes=["c1"])
    G = build_graph([a, b, c, d, ign], _dna())
    assert BRAND in G and "ignored.example" not in G
    r = G.edges["a.example", "b.example"]["reasons"]
    assert any(x.startswith("same IP 104.21.3.4") for x in r)
    assert any("1 contact" in x and "98XXXXXX10" in x for x in r)
    assert any("1 image" in x for x in r)                 # 'qr' is shared; the brand photo is not counted
    assert not G.has_edge("c.example", "d.example")         # private IPs and brand photos never link sites
    assert G.edges[BRAND, "a.example"]["kind"] == "copies"
    assert [(x, y) for x, y, _ in links_of(G, "a.example")] == [("a.example", "b.example")]


def test_export_png_and_offline_html(tmp_path):
    a = _s("a.example", ip="104.21.3.4", contact_hashes=["c1"])
    b = _s("b.example", ip="104.21.3.4", contact_hashes=["c1"], label="suspicious", score=60)
    out = export([a, b], _dna(), out_dir=tmp_path)
    assert out["nodes"] == 3 and out["edges"] == 1
    assert (tmp_path / "campaign_graph.png").read_bytes()[:4] == b"\x89PNG"
    html = (tmp_path / "campaign_graph.html").read_text()
    assert "vis.Network" in html or "new vis" in html
    assert 'src="http' not in html and 'href="http' not in html     # self-contained: works offline / in an iframe
