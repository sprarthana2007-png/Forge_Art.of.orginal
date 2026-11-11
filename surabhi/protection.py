"""Brand Protection workflow: NameNet + Brand DNA on one suspicious domain or website, one report.

    report = investigate("https://northstar-sale.shop/")

1. NameNet compares the domain with every registered brand (name similarity, lookalike techniques) and, for
   the closest brand, optionally checks DNS and registration age.
2. The website is fetched (if reachable) and its logo / favicon / page images, plus any images the user
   uploaded, are compared with the registered Brand DNA fingerprints.
3. Both are combined into one risk assessment. Without content evidence (copied brand images) the risk is
   capped at "medium": name similarity alone never makes a domain "high" risk or "malicious".
"""
from __future__ import annotations

from surabhi import brand_dna as bdna
from surabhi import name_net as nn
from surabhi import store, web
from schemas import Suspect

LEVEL_LABEL = {"high": "likely_fake", "medium": "suspicious"}  # schemas.Suspect.label; others -> ignored
NAME_ONLY_CAP = 49


def assess_risk(name: dict, content: dict | None, site: dict | None) -> dict:
    """Combine NameNet and Brand DNA results into a 0-100 score, a level and readable reasons."""
    content = content or {}
    reasons, cls = [], name.get("classification")
    if cls == "official":
        return {"score": 0, "level": "official", "label": "ignored", "basis": "official domain",
                "malicious_evidence": False, "components": {"name": 0, "infrastructure": 0, "content": 0},
                "summary": "This is the brand's official domain.", "reasons": list(name.get("reasons", []))}

    sim = name.get("similarity", 0)
    name_pts = 0 if cls in ("unrelated", None) else 30 if sim >= 85 else 20 if sim >= 70 else 10
    infra = 0
    if name_pts:
        sig = set(name.get("signals", []))
        live = "resolves" in sig or bool(site and site.get("ok"))
        age = name.get("domain_age_days")
        infra += 5 if live else 0
        infra += 15 if age is not None and age < 30 else 10 if age is not None and age < nn.RECENT_DAYS else 0
        infra += 5 if "idn_homograph" in sig else 0
        if cls == "likely_brand_owned":
            infra = 0
        infra = min(infra, 20)

    parts = []
    if content.get("logo_exact"):
        parts.append((50, "an exact copy of the registered logo"))
    elif content.get("logo_similar"):
        parts.append((40, "a visually similar version of the registered logo"))
    other_exact = content.get("exact", 0) - (1 if content.get("logo_exact") else 0)
    other_similar = content.get("similar", 0) - (1 if content.get("logo_similar") else 0)
    if other_exact > 0:
        parts.append((min(45, 30 + 5 * (other_exact - 1)), f"{other_exact} exact cop{'y' if other_exact == 1 else 'ies'} "
                                                           f"of registered brand images"))
    if other_similar > 0:
        parts.append((min(35, 20 + 5 * (other_similar - 1)), f"{other_similar} visually similar brand "
                                                             f"image{'s' * (other_similar != 1)}"))
    content_pts = min(50, max((p for p, _ in parts), default=0) + 5 * max(len(parts) - 1, 0))

    score = min(100, name_pts + infra + content_pts)
    if content_pts:
        level = "high" if score >= 70 else "medium" if score >= 40 else "low"
        basis = "content evidence" + (" + name similarity" if name_pts else "")
        reasons.append("Brand DNA: the site shows " + "; ".join(t for _, t in parts) + ".")
        if not name_pts:
            reasons.append("The domain name does not resemble the brand; the images could also be used by a "
                           "reseller, review or news site, so check the context.")
        summary = ("Evidence of brand impersonation: registered brand assets appear on a non-official domain."
                   if level == "high" else "Brand assets found on a non-official domain; review the site.")
    else:
        score = min(score, NAME_ONLY_CAP)
        level = ("medium" if infra >= 10 else "low") if name_pts else "none"
        if cls == "likely_brand_owned":
            level = "low"
        basis = "name similarity only" if name_pts else "no match"
        summary = ("Lookalike domain. No copied brand images were found, so there is no evidence of malicious "
                   "behaviour yet; keep monitoring." if name_pts else "No similarity to a registered brand found.")
    if name_pts:
        reasons += [r for r in name.get("reasons", []) if r != nn.EVIDENCE_NOTE]
    if site is not None and not site.get("ok"):
        reasons.append(f"Website could not be loaded ({site.get('error') or 'unknown error'}), "
                       "so its images were not checked.")
    if not content_pts and name_pts:
        reasons.append(nn.EVIDENCE_NOTE)
    return {"score": score, "level": level, "label": LEVEL_LABEL.get(level, "ignored"), "basis": basis,
            "malicious_evidence": bool(content_pts and level == "high"),
            "components": {"name": name_pts, "infrastructure": infra, "content": content_pts},
            "summary": summary, "reasons": reasons}


def investigate(target: str, brand_id: int | None = None, images=(), check_network: bool = True,
                fetch_website: bool = True, save: bool = True, resolver=None, age_lookup=None, fetcher=None,
                conn=None) -> dict:
    """Run NameNet + Brand DNA on a domain or URL. Raises ValueError for a malformed target or when no
    brand is registered; network problems are reported inside the result."""
    domain = nn.normalize_domain(target)
    own = conn is None
    conn = conn or store.connect()
    try:
        if brand_id is not None:
            b = store.get_brand(conn, brand_id)
            if not b:
                raise ValueError(f"No registered brand with id {brand_id}.")
            brands = [b]
        else:
            brands = store.list_brands(conn)
        if not brands:
            raise ValueError("Register a brand first: there are no Brand DNA fingerprints to compare against.")

        site = (fetcher or web.fetch_site)(target) if fetch_website else None
        submitted = bdna.normalise_images(images) + [dict(i) for i in (site or {}).get("images", [])]
        content = bdna.compare_images(submitted, brands)

        # pick the brand: strongest content evidence first, then the closest name
        offline = {b["id"]: nn.analyse_domain(domain, b["official_domain"], b["name"]) for b in brands}
        agg = {a["brand_id"]: a for a in content["brands_matched"]}
        brand = max(brands, key=lambda b: (offline[b["id"]]["classification"] == "official",
                                           bool(agg.get(b["id"])), agg.get(b["id"], {}).get("exact", 0),
                                           offline[b["id"]]["similarity"]))
        name = offline[brand["id"]]
        if check_network and name["classification"] not in ("official", "unrelated"):
            name = nn.analyse_domain(domain, brand["official_domain"], brand["name"], check_network=True,
                                     resolver=resolver, age_lookup=age_lookup)
        if save and name["classification"] not in ("official", "unrelated"):
            with conn:
                store.save_finding(conn, brand["id"], name)

        brand_content = agg.get(brand["id"])
        risk = assess_risk(name, brand_content, site)
        report = {
            "target": target, "domain": domain, "created_at": store.now(),
            "brand": {k: brand[k] for k in ("id", "name", "official_domain", "official_url")},
            "name_net": name,
            "website": None if site is None else {
                k: site.get(k) for k in ("url", "final_url", "ok", "status_code", "title", "error", "image_errors")
            } | {"images_found": len(site.get("images", []))},
            "brand_dna": {**{k: v for k, v in content.items() if k != "results"},
                          "matches_for_brand": brand_content or {},
                          "results": content["results"]},
            "risk": risk,
        }
        if save:
            with conn:
                report["id"] = store.save_investigation(conn, report)
        return report
    finally:
        if own:
            conn.close()


def to_suspect(report: dict) -> Suspect:
    """The report as the shared schemas.Suspect (for the FORGE state file / dashboard cases).

    Image matches use the engine's format (prarthana/investigator.compare_images): {url, sha256, path} for exact
    copies, {url, phash, path} for look-alikes; a matched logo only sets logo_match, as in the engine."""
    dna, brand_id = report["brand_dna"], (report.get("brand") or {}).get("id")
    exact, similar, logo = [], [], False
    for r in dna["results"]:
        hits = [(m, "exact") for m in r.get("exact_matches", []) if m["brand_id"] == brand_id] + \
               [(m, "similar") for m in r.get("similar_matches", []) if m["brand_id"] == brand_id]
        if not hits:
            continue
        if any(m["kind"] == "logo" for m, _ in hits):
            logo = True
            continue
        url = r.get("source") or r["name"]
        if any(k == "exact" for _, k in hits):
            exact.append({"url": url, "sha256": r["sha256"], "path": ""})
        else:
            similar.append({"url": url, "phash": r["phash"], "path": ""})
    name = report["name_net"]
    return Suspect(url=(report.get("website") or {}).get("final_url") or report["target"], domain=report["domain"],
                   found_by="manual", image_exact_matches=exact, image_similar_matches=similar, logo_match=logo,
                   domain_age_days=name.get("domain_age_days"), score=report["risk"]["score"],
                   label=report["risk"]["label"], reasons=list(report["risk"]["reasons"]),
                   verdict=report["risk"]["summary"], ip=(name.get("dns", {}).get("addresses") or [None])[0])
