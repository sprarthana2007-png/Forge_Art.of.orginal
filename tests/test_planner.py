from schemas import BrandDNA, ForgeState, Suspect
from prarthana.planner import node_decide, heuristic_rank, build_graph


def st(**kw):
    return ForgeState(brand_dna=BrandDNA(store_url="https://real.example"), **kw)


def test_decide_rehunts_on_new_campaign():
    out = node_decide(st(pending_hashes=["abc"], round=0, round_new=["u"]))
    assert out["next_step"] == "hunt" and out["round"] == 1


def test_decide_stops_after_round_limit():
    out = node_decide(st(pending_hashes=["abc"], round=2, round_new=["u"]))
    assert out["next_step"] == "alert"


def test_decide_stops_when_nothing_new():
    assert node_decide(st(round_new=[]))["next_step"] == "stop"


def test_decide_alerts_on_new_suspects():
    assert node_decide(st(round_new=["https://x"]))["next_step"] == "alert"


def test_heuristic_rank():
    a = Suspect(url="a", found_by="name_net")
    b = Suspect(url="b", found_by="urlscan_hash", domain_age_days=400)
    c = Suspect(url="c", found_by="urlscan_hash", domain_age_days=3)
    d = Suspect(url="d", found_by="manual")
    assert [s.url for s in heuristic_rank([a, b, c, d])] == ["d", "c", "b", "a"]


def test_graph_compiles():
    assert build_graph() is not None
