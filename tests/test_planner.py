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


def test_prioritise_puts_hash_and_hotlink_first_for_cloaking(monkeypatch):
    import prarthana.planner as planner
    monkeypatch.setattr(planner, "gemini_json", lambda *a, **k: None)
    sus = [Suspect(url="t", domain="t.example", found_by="urlscan_title"),
           Suspect(url="n", domain="n.example", found_by="name_net"),
           Suspect(url="h", domain="h.example", found_by="urlscan_hotlink"),
           Suspect(url="x", domain="x.example", found_by="urlscan_hash")]
    out = planner.node_prioritise(st(suspects=sus, round_new=[s.url for s in sus]))
    q = out["cloak_queue"]
    assert set(q[:2]) == {"h", "x"} and set(q[2:]) == {"t", "n"}


def test_cloak_budget_skips_when_used_up(monkeypatch):
    import prarthana.planner as planner
    monkeypatch.setenv("FORGE_CLOAK_BUDGET", "0")
    monkeypatch.setattr(planner, "investigate", lambda s, dna, log, cloak=None: setattr(s, "cloaking_details", cloak))
    s = Suspect(url="https://a.example/", domain="a.example", found_by="urlscan_hash")
    planner.node_investigate(st(suspects=[s], round_new=[s.url], cloak_queue=[s.url]))
    assert "time budget" in s.cloaking_details["visit3_status"]


def test_verdict_rewritten_when_score_changes(monkeypatch):
    import prarthana.planner as planner
    seen = []
    monkeypatch.setattr(planner, "gemini_verdict", lambda sus, dna, log: seen.extend(s.url for s in sus))
    old_same = Suspect(url="same", label="likely_fake", score=80, verdict="v", verdict_score=80)
    old_changed = Suspect(url="changed", label="likely_fake", score=95, verdict="v", verdict_score=90)
    new = Suspect(url="new", label="suspicious", score=60)
    planner.node_verdict(st(suspects=[old_same, old_changed, new], round_new=["new"]))
    assert seen == ["new", "changed"]
