"""#14 Campaign graph — who is connected to whom.

Nodes: the brand (sage) and every flagged suspect (orange = likely fake, light orange = suspicious).
Edges between suspects: shared public IP, shared contact hash, shared exact image SHA-256 (not counting the brand's
own photos / logo, which every clone copies). Edges brand -> suspect: "copies" (brand photos / logo found).
Every edge carries a plain-English `reasons` list.

Exports:
  * PNG  (matplotlib, bento palette) — used in the evidence PDF "Campaign map" card
  * HTML (pyvis, self-contained)     — output/campaign_graph.html, for the dashboard to embed (<iframe>)
"""
from __future__ import annotations

import ipaddress
from itertools import combinations
from pathlib import Path

import networkx as nx

from schemas import BrandDNA, Suspect
from prarthana.common import CACHE, OUTPUT, domain_of, plural

FONT_DIR = Path(__file__).resolve().parent / "assets" / "fonts"
INK, MUTED, BORDER, BONE, STONE = "#141414", "#5C564D", "#D3CBBC", "#F5F1EA", "#E8E2D6"
ORANGE, ORANGE_2, SAGE = "#FF4F1A", "#FFA27F", "#A7B39A"
BRAND = "__brand__"
MAP_SIZE = (8.0, 5.8)   # inches; the evidence PDF draws the map at this aspect ratio


def _public_ip(ip) -> bool:
    try:
        a = ipaddress.ip_address(ip)
        return not (a.is_private or a.is_loopback or a.is_reserved or a.is_link_local)
    except Exception:
        return False


def _brand_images(dna: BrandDNA | None) -> set:
    if not dna:
        return set()
    return set(dna.photo_sha256s or []) | {h for h in (dna.logo_sha256, dna.favicon_sha256) if h}


def link_reasons(a: Suspect, b: Suspect, dna: BrandDNA | None = None) -> list:
    """Why two suspects are connected (empty list = not connected)."""
    reasons = []
    if a.ip and a.ip == b.ip and _public_ip(a.ip):
        reasons.append(f"same IP {a.ip}")
    shared_c = set(a.contact_hashes or []) & set(b.contact_hashes or [])
    if shared_c:
        masked = [m for h, m in zip(a.contact_hashes, a.contacts_masked or []) if h in shared_c]
        reasons.append(f"shares {plural(len(shared_c), 'contact')}" + (f" ({', '.join(masked[:2])})" if masked else ""))
    brand = _brand_images(dna)
    shared_i = (set(a.image_sha256s or []) & set(b.image_sha256s or [])) - brand
    if shared_i:
        reasons.append(f"shares {plural(len(shared_i), 'image')} (sha {sorted(shared_i)[0][:8]})")
    return reasons


def build_graph(suspects: list, dna: BrandDNA | None = None, include: Suspect | None = None) -> nx.Graph:
    """Graph of flagged suspects (+ `include`, always). The brand's own / allow-listed domains are left out."""
    from prarthana.scorer import is_allowed
    G = nx.Graph()
    if dna:
        G.add_node(BRAND, kind="brand", name=dna.brand_name or domain_of(dna.store_url),
                   domain=domain_of(dna.store_url) if dna.store_url else "")
    nodes, seen = [], set()
    for s in suspects:
        s.domain = s.domain or domain_of(s.url)
        keep = s is include or s.label in ("likely_fake", "suspicious")
        if not keep or s.domain in seen or (dna and is_allowed(s.domain, dna)):
            continue
        seen.add(s.domain)
        nodes.append(s)
        G.add_node(s.domain, kind="suspect", name=s.domain, url=s.url, score=s.score, label=s.label,
                   campaign=s.campaign_id or "", relaunch_of=getattr(s, "relaunch_of", "") or "",
                   status=s.status)
        if dna:
            why = []
            n_img = len(s.image_exact_matches or []) + len(s.image_similar_matches or [])
            if n_img:
                why.append(f"copies {plural(n_img, 'brand photo')}")
            if s.logo_match:
                why.append("uses the brand logo")
            if why:
                G.add_edge(BRAND, s.domain, kind="copies", reasons=why)
    for a, b in combinations(nodes, 2):
        r = link_reasons(a, b, dna)
        if r:
            G.add_edge(a.domain, b.domain, kind="link", reasons=r)
    return G


def links_of(G: nx.Graph, domain: str) -> list:
    """[(a, b, reasons)] for every suspect-suspect edge in the connected component of `domain`."""
    if domain not in G:
        return []
    comp = nx.node_connected_component(G.subgraph([n for n in G if n != BRAND]), domain)
    return [(a, b, d["reasons"]) for a, b, d in G.edges(data=True)
            if d.get("kind") == "link" and a in comp and b in comp]


def _short(reasons: list) -> str:
    """Compact edge label: 'IP + 1 contact + 1 image'."""
    out = []
    for r in reasons:
        if r.startswith("same IP"):
            out.append("IP")
        elif r.startswith("shares"):
            out.append(r.split(" (")[0].replace("shares ", ""))
        else:
            out.append(r)
    return " + ".join(out)


def _fonts():
    """FontProperties for JetBrains Mono / Inter Tight (DejaVu fallback)."""
    from matplotlib.font_manager import FontProperties
    out = []
    for f, fallback in (("JetBrainsMono-Regular.ttf", "DejaVu Sans Mono"), ("InterTight-Medium.ttf", "DejaVu Sans")):
        p = FONT_DIR / f
        out.append(FontProperties(fname=str(p)) if p.exists() else FontProperties(family=fallback))
    return out


def render_png(G: nx.Graph, path, highlight: str | None = None, size=(8.0, 4.4)) -> str:
    """Bento-styled PNG of the graph. `highlight` gets an ink ring (the suspect the PDF is about)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    mono, sans = _fonts()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=size, dpi=200)
    fig.patch.set_facecolor(BONE)
    ax.set_facecolor(BONE)
    ax.axis("off")
    if len(G) == 0:
        ax.text(0.5, 0.5, "no flagged suspects", ha="center", va="center", fontproperties=mono, color=MUTED)
    else:
        fixed = [BRAND] if BRAND in G else None
        init = {BRAND: (0.0, 0.0)} if BRAND in G else None
        pos = nx.spring_layout(G, seed=21, pos=init, fixed=fixed, k=2.2 / max(1, len(G)) ** 0.5, iterations=200)
        if len(G) == 2 and BRAND in G:  # brand + one suspect: place side by side
            other = next(n for n in G if n != BRAND)
            pos = {BRAND: (-0.6, 0.0), other: (0.6, 0.0)}
        xs = [p[0] for p in pos.values()]
        ys = [p[1] for p in pos.values()]
        padx = max(0.5, (max(xs) - min(xs)) * 0.18)
        pady = max(0.4, (max(ys) - min(ys)) * 0.22)
        ax.set_xlim(min(xs) - padx, max(xs) + padx)
        ax.set_ylim(min(ys) - pady, max(ys) + pady)
        for a, b, d in G.edges(data=True):
            (x1, y1), (x2, y2) = pos[a], pos[b]
            copies = d.get("kind") == "copies"
            ax.plot([x1, x2], [y1, y2], color=SAGE if copies else INK, lw=1.2 if copies else 1.8,
                    ls=(0, (4, 3)) if copies else "-", zorder=1)
            mx, my = (x1 + x2) / 2, (y1 + y2) / 2
            ax.text(mx, my, "copies" if copies else _short(d["reasons"]).replace(" + ", "\n"), fontsize=6.5,
                    linespacing=1.25,
                    fontproperties=mono, color=MUTED if copies else INK, ha="center", va="center", zorder=3,
                    bbox=dict(boxstyle="round,pad=0.25", fc=BONE, ec="none"))
        for n, d in G.nodes(data=True):
            x, y = pos[n]
            if d["kind"] == "brand":
                col, sz, edge = SAGE, 1500, SAGE
            else:
                col = ORANGE if d.get("label") == "likely_fake" else (ORANGE_2 if d.get("label") == "suspicious"
                                                                      else BORDER)
                sz, edge = 260 + 9 * d.get("score", 0), col
            ax.scatter([x], [y], s=sz, c=col, edgecolors=INK if n == highlight else edge,
                       linewidths=2.6 if n == highlight else 0, zorder=4)
            r_pt = (sz ** 0.5) / 2  # marker radius in points
            box = dict(boxstyle="round,pad=0.2", fc=BONE, ec="none", alpha=0.9)
            if d["kind"] == "brand":
                ax.text(x, y, "brand", fontsize=7, fontproperties=mono, color=INK, ha="center", va="center", zorder=5)
                ax.annotate(d["name"], (x, y), xytext=(0, -(r_pt + 4)), textcoords="offset points", fontsize=8.5,
                            fontproperties=sans, color=INK, ha="center", va="top", zorder=6, bbox=box)
            else:
                ax.text(x, y, str(d.get("score", "")), fontsize=7.5, fontproperties=mono,
                        color=INK if d.get("label") != "likely_fake" else BONE, ha="center", va="center", zorder=5)
                lab = d["name"] + (f"\nrelaunch of {d['relaunch_of']}" if d.get("relaunch_of") else "")
                ax.annotate(lab, (x, y), xytext=(0, -(r_pt + 4)), textcoords="offset points", fontsize=7.4,
                            fontproperties=mono, color=INK, ha="center", va="top", zorder=6, bbox=box)
    fig.tight_layout(pad=0.3)
    fig.savefig(path, facecolor=BONE)
    plt.close(fig)
    return str(path)


def export_html(G: nx.Graph, path) -> str:
    """Self-contained interactive HTML (pyvis, vis.js inlined) for the dashboard to embed."""
    from pyvis.network import Network
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    net = Network(height="560px", width="100%", bgcolor=BONE, font_color=INK, cdn_resources="in_line")
    for n, d in G.nodes(data=True):
        if d["kind"] == "brand":
            net.add_node(n, label=d["name"], title=f"Brand: {d['name']} ({d.get('domain', '')})", color=SAGE,
                         size=34, shape="dot", font={"face": "monospace"})
        else:
            col = ORANGE if d.get("label") == "likely_fake" else (ORANGE_2 if d.get("label") == "suspicious"
                                                                  else BORDER)
            tip = (f"{d['name']}\nscore {d.get('score')}/100 · {d.get('label')}\nstatus {d.get('status')}"
                   + (f"\ncampaign {d['campaign']}" if d.get("campaign") else "")
                   + (f"\nrelaunch of {d['relaunch_of']}" if d.get("relaunch_of") else ""))
            net.add_node(n, label=f"{d['name']}\n{d.get('score')}", title=tip, color=col,
                         size=14 + d.get("score", 0) / 5, shape="dot", font={"face": "monospace"})
    for a, b, d in G.edges(data=True):
        copies = d.get("kind") == "copies"
        net.add_edge(a, b, title="; ".join(d["reasons"]), label="copies" if copies else _short(d["reasons"]),
                     color=SAGE if copies else INK, dashes=copies, width=1 if copies else 2)
    net.barnes_hut(gravity=-6000, central_gravity=0.15, spring_length=170, spring_strength=0.03)
    html = net.generate_html(notebook=False)
    # pyvis' template also links Bootstrap from a CDN for menus we do not use: drop it so the file is fully offline
    import re
    html = re.sub(r'<script[^>]+src="[^"]*bootstrap[^"]*"[^>]*>\s*</script>', "", html, flags=re.I)
    html = re.sub(r'<link[^>]+href="[^"]*bootstrap[^"]*"[^>]*/?>', "", html, flags=re.I)
    path.write_text(html, encoding="utf-8")
    return str(path)


def export(suspects: list, dna: BrandDNA | None = None, out_dir=None, state_log=None) -> dict:
    """Write output/campaign_graph.png + output/campaign_graph.html for the whole run. Never raises."""
    from prarthana.common import log
    out_dir = Path(out_dir or OUTPUT)
    try:
        G = build_graph(suspects, dna)
        png = render_png(G, out_dir / "campaign_graph.png")
        html = export_html(G, out_dir / "campaign_graph.html")
        log(state_log, f"campaign graph: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges -> {html}")
        return {"png": png, "html": html, "nodes": G.number_of_nodes(), "edges": G.number_of_edges()}
    except Exception as e:
        log(state_log, f"campaign graph: failed ({type(e).__name__}: {e})")
        return {}


def suspect_map(s: Suspect, peers: list | None, dna: BrandDNA | None) -> dict:
    """Per-suspect map for the evidence PDF: PNG with this suspect highlighted + the links in its component."""
    G = build_graph(peers or [s], dna, include=s)
    png = render_png(G, CACHE / "graphs" / f"{(s.domain or 'suspect').replace(':', '_')}.png", highlight=s.domain,
                     size=MAP_SIZE)
    comp = links_of(G, s.domain)
    members = sorted({x for a, b, _ in comp for x in (a, b)} | {s.domain})
    return {"png": png, "links": comp, "members": members,
            "nodes": {n: d for n, d in G.nodes(data=True) if n in members}}
