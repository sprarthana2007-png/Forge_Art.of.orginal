"""What the FORGE engine already found, in the shape the Brand protection page shows.

The Brand protection page keeps its own SQLite registry (brands registered through its form). The FORGE engine
writes its results to cache/state.json instead. This module turns that state into numbers and rows so the page
can show both together, instead of zeros when only the engine has run.
"""
from __future__ import annotations

from urllib.parse import urlparse


def _domain(url: str) -> str:
    return (urlparse(url or "").netloc or url or "").lower()


def brand_images(dna) -> int:
    """Logo + favicon + product photos the engine fingerprinted for the brand."""
    if dna is None:
        return 0
    return len(getattr(dna, "photo_sha256s", None) or []) + sum(
        1 for h in (getattr(dna, "logo_sha256", ""), getattr(dna, "favicon_sha256", "")) if h)


def engine_summary(state) -> dict:
    """Counts and rows from a ForgeState (or None). Never raises on missing fields."""
    empty = {"has_engine": False, "brand": None, "brand_domain": "", "stats": {
        "brands": 0, "images_analysed": 0, "exact_matches": 0, "similar_matches": 0, "fingerprints": 0,
        "suspicious_domains": 0, "investigations": 0, "high_risk": 0}, "suspects": [], "name_net": []}
    if state is None:
        return empty
    dna = getattr(state, "brand_dna", None)
    suspects = list(getattr(state, "suspects", None) or [])
    if dna is None and not suspects:
        return empty
    exact = sum(len(getattr(s, "image_exact_matches", None) or []) for s in suspects)
    similar = sum(len(getattr(s, "image_similar_matches", None) or []) for s in suspects)
    suspect_images = exact + similar
    name_net = [s for s in suspects if getattr(s, "found_by", "") == "name_net"]
    rows = [{
        "domain": getattr(s, "domain", "") or _domain(getattr(s, "url", "")),
        "url": getattr(s, "url", ""),
        "found_by": getattr(s, "found_by", "") or "manual",
        "score": int(getattr(s, "score", 0) or 0),
        "label": getattr(s, "label", "") or "",
        "exact": len(getattr(s, "image_exact_matches", None) or []),
        "similar": len(getattr(s, "image_similar_matches", None) or []),
        "cloaking": bool(getattr(s, "cloaking", False)),
        "status": getattr(s, "status", "") or "",
    } for s in suspects]
    rows.sort(key=lambda r: r["score"], reverse=True)
    fingerprints = brand_images(dna) * 2 if dna is not None else 0  # SHA-256 + pHash per image
    return {
        "has_engine": True,
        "brand": dna,
        "brand_domain": _domain(getattr(dna, "store_url", "")) if dna is not None else "",
        "stats": {
            "brands": 1 if dna is not None else 0,
            "images_analysed": brand_images(dna) + suspect_images,
            "exact_matches": exact,
            "similar_matches": similar,
            "fingerprints": fingerprints,
            "suspicious_domains": len(name_net),
            "investigations": len(suspects),
            "high_risk": sum(r["label"] == "likely_fake" for r in rows),
        },
        "suspects": rows,
        "name_net": [r for r in rows if r["found_by"] == "name_net"],
    }


def combined_stats(sqlite_stats: dict, sqlite_high_risk: int, registered_domains, summary: dict) -> dict:
    """Page stat cards: the SQLite registry plus the engine, without counting the same brand twice."""
    e = summary["stats"]
    same_brand = bool(summary["brand_domain"]) and summary["brand_domain"] in {d.lower() for d in registered_domains}
    return {
        "brands": sqlite_stats.get("brands", 0) + (0 if same_brand else e["brands"]),
        "images_analysed": sqlite_stats.get("images_analysed", 0) + e["images_analysed"],
        "exact_matches": sqlite_stats.get("exact_matches", 0) + e["exact_matches"],
        "similar_matches": sqlite_stats.get("similar_matches", 0) + e["similar_matches"],
        "fingerprints": sqlite_stats.get("fingerprints", 0) + (0 if same_brand else e["fingerprints"]),
        "suspicious_domains": sqlite_stats.get("suspicious_domains", 0) + e["suspicious_domains"],
        "investigations": sqlite_stats.get("investigations", 0) + e["investigations"],
        "high_risk": sqlite_high_risk + e["high_risk"],
    }
