"""#11 Gemini verdict — one batched Gemini call -> a 3-sentence plain-English verdict per suspect.

Falls back to a template verdict built from the scorer's reasons if Gemini is unavailable. Never crashes.
"""
from __future__ import annotations

import json
import re

from schemas import BrandDNA, Suspect
from prarthana.gemini import gemini_json

LABEL_TEXT = {"likely_fake": "likely a fake copy of", "suspicious": "suspicious and may be imitating",
              "ignored": "probably not a threat to"}


def _clean_reason(r: str) -> str:
    return re.sub(r"^\+\d+:\s*", "", r).rstrip(".")


def template_verdict(s: Suspect, dna: BrandDNA) -> str:
    brand = dna.brand_name or "your store"
    pts = [_clean_reason(r) for r in s.reasons if r.startswith("+")]
    because = "; ".join(pts[:3]) if pts else "no copied photos, logo, prices or contacts were found"
    first = f"This site ({s.domain}) scored {s.score}/100 because it {because[0].lower() + because[1:]}."
    second = f"FORGE rates it as {LABEL_TEXT.get(s.label, s.label)} {brand}."
    if getattr(s, "cloaking", False):
        second = (f"FORGE rates it as {LABEL_TEXT.get(s.label, s.label)} {brand}, and it shows a different page "
                  f"to shoppers arriving from an Instagram ad than to normal visitors (cloaking).")
    if s.label == "likely_fake":
        third = "Please review the evidence and confirm in your FORGE dashboard so an abuse report can be sent."
    elif s.label == "suspicious":
        third = "Please take a look at the evidence before deciding whether to report it."
    else:
        third = "No action is needed unless you recognise it as a copy."
    return " ".join([first, second, third])


def gemini_verdict(suspects: list, brand_dna: BrandDNA, state_log=None) -> dict:
    """Return {url: verdict}; also stores each verdict in suspect.verdict."""
    if not suspects:
        return {}
    items = [{"url": s.url, "domain": s.domain, "score": s.score, "label": s.label, "reasons": s.reasons,
              "domain_age_days": s.domain_age_days, "cloaking": bool(getattr(s, "cloaking", False)),
              "cloaking_differences": ((getattr(s, "cloaking_details", None) or {}).get("differences") or [])[:3],
              "relaunch_of": getattr(s, "relaunch_of", "") or None}
             for s in suspects]
    prompt = (
        "You help a small Indian online brand spot fake copies of its store. "
        f"Brand: {brand_dna.brand_name!r}, real store: {brand_dna.store_url}.\n"
        "For EACH suspect below write exactly 3 short plain-English sentences for the shop owner: "
        "(1) the score and what it means, (2) the strongest evidence, (3) what they should do next. "
        "Use only the facts given; do not invent anything. Do not include any phone numbers or IDs. "
        "If cloaking is true, say clearly that the site shows a different page to shoppers arriving from an "
        "Instagram ad than to normal visitors (it hides the scam from checks). "
        "If relaunch_of is set, say it looks like a relaunch of that fake the owner already confirmed.\n"
        'Reply as JSON: {"verdicts": [{"url": "...", "verdict": "..."}]}\n\n'
        "Suspects:\n" + json.dumps(items, ensure_ascii=False, indent=1))
    data = gemini_json(prompt, state_log)
    got = {}
    if isinstance(data, dict):
        for v in data.get("verdicts", []) or []:
            if isinstance(v, dict) and v.get("url") and isinstance(v.get("verdict"), str):
                got[v["url"]] = v["verdict"].strip()
    out = {}
    for s in suspects:
        s.verdict = got.get(s.url) or template_verdict(s, brand_dna)
        out[s.url] = s.verdict
    return out
