"""#1 Planner — LangGraph StateGraph over ForgeState.

brand_dna -> hunt -> prioritise -> investigate -> score -> verdict -> decide -+-> hunt   (new campaign, round < 3)
                                                                              +-> alert  (new suspects found)
                                                                              +-> END    (nothing new)
"""
from __future__ import annotations

import json

from langgraph.graph import END, StateGraph

from schemas import ForgeState, Suspect, state_from_dict, state_to_dict
from prarthana.common import CACHE, domain_of, log
from prarthana.teammate import get_build_brand_dna, get_name_net
from prarthana.urlscan_nets import urlscan_nets
from prarthana.investigator import investigate
from prarthana.scorer import score_all, campaign_shared_image_hashes, is_allowed
from prarthana.verdict import gemini_verdict
from prarthana.gemini import gemini_json
from prarthana.alert import send_alert

MAX_ROUNDS = 3
MAX_INVESTIGATE_PER_ROUND = 12   # keeps a run bounded (each visit can take up to 20 s)
CLOAK_BUDGET_S = 300             # time budget per round for cloaking checks (env FORGE_CLOAK_BUDGET)
CLOAK_URLSCAN_MIN_S = 100        # visit 3 (urlscan) needs up to ~100 s; skipped when less budget is left
CLOAK_FIRST = ("manual", "urlscan_hash", "urlscan_hotlink")  # strongest evidence first for the cloaking check
STATE_FILE = CACHE / "state.json"
FOUND_BY_RANK = {"manual": 0, "urlscan_hash": 1, "urlscan_hotlink": 2, "urlscan_title": 3, "name_net": 4}


# ------------------------------------------------------------------ persistence
def load_state(store_url: str) -> ForgeState:
    try:
        st = state_from_dict(json.loads(STATE_FILE.read_text()))
        if st.brand_dna and st.brand_dna.store_url == store_url:
            return st
    except Exception:
        pass
    return ForgeState()


def save_state(st: ForgeState) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state_to_dict(st), indent=1, ensure_ascii=False))


def _new(st: ForgeState) -> list:
    return [s for s in st.suspects if s.url in st.round_new]


# ------------------------------------------------------------------ nodes
def node_brand_dna(st: ForgeState) -> dict:
    """Brand DNA is built (or loaded from cache/state.json) by run_forge before the graph starts, because
    the store URL is not part of ForgeState. This node only checks it and logs what we know."""
    dna = st.brand_dna
    log(st.log, f"brand_dna: {dna.brand_name!r} {len(dna.photo_sha256s)} photos, {len(dna.prices)} prices, "
                f"logo={'yes' if dna.logo_sha256 else 'no'}")
    return {"next_step": "hunt", "log": st.log}


def node_hunt(st: ForgeState) -> dict:
    dna = st.brand_dna
    seen = set(st.seen_domains)
    found = []
    if st.round == 0:
        known = {s.url for s in st.suspects}
        for u in st.manual_urls:
            if u not in known:
                found.append(Suspect(url=u, domain=domain_of(u), found_by="manual"))
                seen.add(domain_of(u))
        fn, where = get_name_net()
        try:
            for s in fn(dna) or []:
                s.domain = s.domain or domain_of(s.url)
                if s.domain not in seen and not is_allowed(s.domain, dna):
                    seen.add(s.domain)
                    found.append(s)
        except Exception as e:
            log(st.log, f"hunt: name_net ({where}) failed: {type(e).__name__}")
        found += urlscan_nets(dna, seen, st.log)
    else:
        log(st.log, f"hunt: round {st.round} re-hunt with {len(st.pending_hashes)} campaign image hashes")
        found += urlscan_nets(dna, seen, st.log, extra_hashes=st.pending_hashes, only_hashes=True)
    log(st.log, f"hunt: round {st.round} found {len(found)} new suspects")
    return {"suspects": st.suspects + found, "seen_domains": seen, "round_new": [s.url for s in found],
            "pending_hashes": [], "next_step": "prioritise"}


def heuristic_rank(suspects: list) -> list:
    return sorted(suspects, key=lambda s: (FOUND_BY_RANK.get(s.found_by, 9),
                                           s.domain_age_days if s.domain_age_days is not None else 10**6))


def node_prioritise(st: ForgeState) -> dict:
    new = _new(st)
    order = None
    if len(new) > 1:
        items = [{"url": s.url, "domain": s.domain, "found_by": s.found_by} for s in new]
        data = gemini_json(
            "Rank these possible fake online stores for investigation, most dangerous first. Prefer the newest "
            f"domains and those found by more image matches (urlscan_hash > urlscan_hotlink > urlscan_title > "
            f"name_net; 'manual' was reported by the owner, rank it first). Brand: {st.brand_dna.brand_name}.\n"
            'Reply as JSON: {"order": ["url", ...]} containing every url exactly once.\n' + json.dumps(items),
            st.log)
        if isinstance(data, dict) and isinstance(data.get("order"), list):
            rank = {u: i for i, u in enumerate(data["order"])}
            order = sorted(new, key=lambda s: rank.get(s.url, len(rank)))
            log(st.log, "prioritise: ranked by Gemini")
    if order is None:
        order = heuristic_rank(new)
        log(st.log, "prioritise: ranked by heuristic")
    # manual suspects always come first, then cap the round
    order = [s for s in order if s.found_by == "manual"] + [s for s in order if s.found_by != "manual"]
    keep = order[:MAX_INVESTIGATE_PER_ROUND]
    if len(order) > len(keep):
        log(st.log, f"prioritise: {len(order) - len(keep)} lower-priority suspects left for the next run")
    keep_urls = {s.url for s in keep}
    # suspects not investigated this round are dropped from seen_domains so a later run picks them up
    rest = [s for s in new if s.url not in keep_urls]
    # cloaking check order: hotlink / hash matches (and owner reports) first, in case the time budget runs out
    cloak_queue = [s.url for s in keep if s.found_by in CLOAK_FIRST] + \
                  [s.url for s in keep if s.found_by not in CLOAK_FIRST]
    return {"round_new": [s.url for s in keep], "cloak_queue": cloak_queue,
            "suspects": [s for s in st.suspects if s not in rest],
            "seen_domains": set(st.seen_domains) - {s.domain for s in rest},
            "next_step": "investigate"}


def node_investigate(st: ForgeState) -> dict:
    """Cloaking checks first (in cloak_queue order, within a time budget), then the evidence visits."""
    import os
    import time
    from prarthana.cloaking import check_cloaking
    try:
        budget = float(os.environ.get("FORGE_CLOAK_BUDGET", CLOAK_BUDGET_S))
    except ValueError:
        budget = CLOAK_BUDGET_S
    by_url = {s.url: s for s in _new(st)}
    cloak = {}
    start = time.time()
    for url in st.cloak_queue or list(by_url):
        s = by_url.get(url)
        if s is None or is_allowed(s.domain or domain_of(s.url), st.brand_dna):
            continue
        left = budget - (time.time() - start)
        if left <= 0:
            cloak[url] = {"cloaking": False, "visits": [], "differences": [], "comparisons": [],
                          "notes": ["Cloaking check skipped: time budget for this round was used up."],
                          "visit3_status": "skipped: time budget for this round was used up"}
            log(st.log, f"cloaking: skipped {url} (time budget used up)")
            continue
        try:
            cloak[url] = check_cloaking(s, use_urlscan=left >= CLOAK_URLSCAN_MIN_S, state_log=st.log)
        except Exception as e:
            log(st.log, f"cloaking: failed for {url} ({type(e).__name__})")
    for s in _new(st):
        investigate(s, st.brand_dna, st.log, cloak=cloak.get(s.url, {}))
    return {"suspects": st.suspects, "next_step": "score"}


def node_score(st: ForgeState) -> dict:
    score_all(st.suspects, st.brand_dna)
    after = {s.campaign_id for s in st.suspects if s.campaign_id}
    fresh = sorted(after - set(st.handled_campaigns))
    hashes = []
    for cid in fresh:
        hashes += campaign_shared_image_hashes(st.suspects, cid, st.brand_dna)
        log(st.log, f"score: campaign {cid} groups "
                    f"{sum(1 for s in st.suspects if s.campaign_id == cid)} sites")
    return {"suspects": st.suspects, "pending_hashes": sorted(set(hashes)),
            "handled_campaigns": st.handled_campaigns + fresh, "next_step": "verdict",
            "log": st.log}


def node_verdict(st: ForgeState) -> dict:
    gemini_verdict(_new(st), st.brand_dna, st.log)
    return {"suspects": st.suspects, "next_step": "decide"}


def node_decide(st: ForgeState) -> dict:
    unalerted = [s for s in st.suspects if s.label in ("likely_fake", "suspicious") and s.url not in st.alerted
                 and s.status in ("new", "investigated")]
    if st.pending_hashes and st.round < MAX_ROUNDS - 1:
        nxt = "hunt"
    elif not st.round_new and not unalerted:
        nxt = "stop"
    else:
        nxt = "alert"
    log(st.log, f"decide: round {st.round}, new={len(st.round_new)}, campaign_hashes={len(st.pending_hashes)} -> {nxt}")
    out = {"next_step": nxt}
    if nxt == "hunt":
        out["round"] = st.round + 1
    return out


def node_alert(st: ForgeState) -> dict:
    todo = [s for s in st.suspects if s.label in ("likely_fake", "suspicious") and s.url not in st.alerted
            and s.status in ("new", "investigated")]
    from prarthana.campaign_graph import export
    export(st.suspects, st.brand_dna, state_log=st.log)   # output/campaign_graph.png + .html for the dashboard
    how = send_alert(todo, st.brand_dna, st.log, peers=st.suspects) if todo else "none"
    log(st.log, f"alert: {len(todo)} suspects -> {how}")
    return {"alerted": st.alerted + [s.url for s in todo], "next_step": "done"}


# ------------------------------------------------------------------ graph
def _keep_log(fn):
    def wrapped(st: ForgeState) -> dict:
        out = fn(st)
        out.setdefault("log", st.log)
        return out
    wrapped.__name__ = fn.__name__
    return wrapped


def build_graph():
    g = StateGraph(ForgeState)
    _add = g.add_node
    g.add_node = lambda name, fn: _add(name, _keep_log(fn))
    g.add_node("brand_dna", node_brand_dna)
    g.add_node("hunt", node_hunt)
    g.add_node("prioritise", node_prioritise)
    g.add_node("investigate", node_investigate)
    g.add_node("score", node_score)
    g.add_node("verdict", node_verdict)
    g.add_node("decide", node_decide)
    g.add_node("alert", node_alert)
    g.set_entry_point("brand_dna")
    g.add_edge("brand_dna", "hunt")
    g.add_edge("hunt", "prioritise")
    g.add_edge("prioritise", "investigate")
    g.add_edge("investigate", "score")
    g.add_edge("score", "verdict")
    g.add_edge("verdict", "decide")
    g.add_conditional_edges("decide", lambda st: st.next_step, {"hunt": "hunt", "alert": "alert", "stop": END})
    g.add_edge("alert", END)
    return g.compile()


def run_forge(store_url: str, suspects: list | None = None, fresh: bool = False) -> ForgeState:
    st = ForgeState() if fresh else load_state(store_url)
    if st.brand_dna is None:
        fn, where = get_build_brand_dna()
        log(st.log, f"brand_dna: building with {where}")
        st.brand_dna = fn(store_url)
        st.seen_domains.add(domain_of(store_url))
    st.manual_urls = list(suspects or [])
    st.round, st.round_new, st.pending_hashes, st.next_step = 0, [], [], "hunt"
    result = build_graph().invoke(st, {"recursion_limit": 60})
    final = result if isinstance(result, ForgeState) else ForgeState(**result)
    save_state(final)
    return final
