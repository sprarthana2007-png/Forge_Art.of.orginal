"""Shared schemas + hashing helpers for FORGE (Team Shared 21).

Both teammates import from here. Only ADD fields; never rename or remove.
"""
from __future__ import annotations

import base64
import functools
import hashlib
import os
import re
from dataclasses import dataclass, field, asdict
from io import BytesIO
from typing import Optional

from PIL import Image
import imagehash

PHASH_MATCH = 8


# ---------------------------------------------------------------- hashing
def file_sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def photo_phash(b: bytes) -> str:
    """Perceptual hash. SVG images are rasterized first (see raster_bytes); SHA-256 stays on the raw bytes."""
    return str(imagehash.phash(Image.open(BytesIO(raster_bytes(b))).convert("RGB")))


# ---------------------------------------------------------------- SVG support (added by surabhi)
SVG_RENDER_WIDTH = 600


def is_svg(b: bytes) -> bool:
    head = bytes(b[:4096]).lstrip(b"\xef\xbb\xbf \t\r\n").lower()
    return head.startswith(b"<svg") or (head.startswith((b"<?xml", b"<!doctype svg", b"<!--")) and b"<svg" in head)


def raster_bytes(b: bytes, width: Optional[int] = SVG_RENDER_WIDTH) -> bytes:
    """PNG bytes for an SVG image (width=None: the SVG's own size); any other image is returned unchanged."""
    return svg_to_png(bytes(b), width) if is_svg(b) else b


@functools.lru_cache(maxsize=512)
def svg_to_png(b: bytes, width: Optional[int] = SVG_RENDER_WIDTH) -> bytes:
    """Render an SVG to PNG on a white background, `width` px wide (None: its own size). Uses cairosvg; without it
    (or without the cairo library, common on Windows) it is rendered in headless Chromium instead."""
    try:
        import cairosvg
    except (ImportError, OSError):
        return _svg_to_png_chromium(b, width)
    return cairosvg.svg2png(bytestring=b, output_width=width, background_color="white")


def _svg_natural_width(b: bytes) -> int:
    """The SVG's own width: its width attribute in px, else the viewBox width, else 300 (the browser default)."""
    tag = re.search(rb"<svg\b[^>]*>", b, re.I)
    tag = tag.group(0) if tag else b""
    m = re.search(rb'\swidth\s*=\s*["\']\s*([\d.]+)\s*(px)?\s*["\']', tag)
    if m:
        return max(1, round(float(m.group(1))))
    m = re.search(rb'viewBox\s*=\s*["\']\s*[-\d.]+[\s,]+[-\d.]+[\s,]+([\d.]+)', tag)
    return max(1, round(float(m.group(1)))) if m else 300


def _svg_to_png_chromium(b: bytes, width: Optional[int]) -> bytes:
    from playwright.sync_api import sync_playwright
    width = width or _svg_natural_width(b)
    uri = "data:image/svg+xml;base64," + base64.b64encode(b).decode("ascii")
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception:
            browser = p.chromium.launch(executable_path=os.environ.get("CHROMIUM_PATH") or "/opt/pw-browsers/chromium")
        try:
            page = browser.new_page(viewport={"width": width, "height": width})
            page.set_content(f'<body style="margin:0"><img id="i" src="{uri}" style="width:{width}px;display:block">')
            return page.locator("#i").screenshot()  # on the page's white background, like cairo
        finally:
            browser.close()


def is_similar(a: str, b: str) -> bool:
    return imagehash.hex_to_hash(a) - imagehash.hex_to_hash(b) <= PHASH_MATCH


def normalise_contact(s: str) -> str:
    """Digits only for phones (last 10 digits, so +91/0 prefixes match), lowercase for UPI/email."""
    s = (s or "").strip()
    if "@" in s:
        return s.lower()
    digits = re.sub(r"\D", "", s)
    return digits[-10:] if len(digits) >= 10 else digits


def hash_contact(s: str) -> str:
    return hashlib.sha256(normalise_contact(s).encode("utf-8")).hexdigest()


def mask_contact(s: str) -> str:
    s = normalise_contact(s)
    if len(s) <= 4:
        return "X" * len(s)
    return s[:2] + "X" * (len(s) - 4) + s[-2:]


# ---------------------------------------------------------------- dataclasses
@dataclass
class BrandDNA:
    store_url: str
    brand_name: str = ""
    logo_sha256: str = ""
    favicon_sha256: str = ""
    photo_sha256s: list = field(default_factory=list)
    photo_phashes: list = field(default_factory=list)
    product_names: list = field(default_factory=list)
    prices: dict = field(default_factory=dict)  # product name -> price (float)
    official_contact_hashes: list = field(default_factory=list)
    verified_owner: bool = False
    allow_list: list = field(default_factory=list)  # domains
    # added by prarthana (optional extras)
    logo_phash: str = ""
    favicon_phash: str = ""
    # added by surabhi (dashboard extras)
    screenshot_path: str = ""                  # screenshot of the official store, shown beside each suspect
    domain_age_days: Optional[int] = None      # age of the official domain, for comparison


@dataclass
class Suspect:
    url: str
    domain: str = ""
    found_by: str = "name_net"  # name_net | urlscan_hash | urlscan_hotlink | urlscan_title | manual
    screenshot_path: str = ""
    image_exact_matches: list = field(default_factory=list)
    image_similar_matches: list = field(default_factory=list)
    logo_match: bool = False
    prices_found: dict = field(default_factory=dict)
    contact_hashes: list = field(default_factory=list)
    contacts_masked: list = field(default_factory=list)
    domain_age_days: Optional[int] = None
    scam_phrases: list = field(default_factory=list)
    score: int = 0
    label: str = "ignored"  # likely_fake | suspicious | ignored
    reasons: list = field(default_factory=list)
    verdict: str = ""
    campaign_id: Optional[str] = None
    status: str = "new"  # new | investigated | confirmed | not_fake | reported
    # added by prarthana (optional extras)
    ip: Optional[str] = None
    image_sha256s: list = field(default_factory=list)
    pdf_path: str = ""
    abuse_email: str = ""
    evidence_sha256: str = ""  # SHA-256 of the final signed evidence PDF ("evidence fingerprint")
    cloaking: bool = False     # #13: shows a different page to an Instagram-ad visitor than to a normal visitor
    cloaking_details: dict = field(default_factory=dict)  # check_cloaking() result (visits, differences, ...)
    relaunch_of: str = ""      # #16: domain of an earlier confirmed/reported fake this site relaunches
    relaunch_evidence: list = field(default_factory=list)  # #16: why (same contact / image / IP)
    signals_fired: list = field(default_factory=list)      # #19: scorer signal keys that fired (photos, logo, ...)
    signal_weights: dict = field(default_factory=dict)     # #19: owner-feedback weights used when scoring
    # added by surabhi (dashboard extras)
    page_title: str = ""
    page_text: str = ""                        # visible text excerpt, sent to Gemini and searched for phrases
    matched_image_pairs: list = field(default_factory=list)  # [official_image_path, suspect_image_path, match_kind]
    verdict_score: int = -1    # the score the current verdict text was written for (re-written if it changes)


@dataclass
class ForgeState:
    brand_dna: Optional[BrandDNA] = None
    suspects: list = field(default_factory=list)
    seen_domains: set = field(default_factory=set)
    round: int = 0
    next_step: str = ""
    log: list = field(default_factory=list)
    # added by prarthana (planner bookkeeping)
    manual_urls: list = field(default_factory=list)       # --suspect URLs to investigate directly
    round_new: list = field(default_factory=list)         # URLs found in the current round
    pending_hashes: list = field(default_factory=list)    # campaign image hashes to re-hunt with
    handled_campaigns: list = field(default_factory=list)
    alerted: list = field(default_factory=list)           # URLs already included in an owner alert
    cloak_queue: list = field(default_factory=list)       # URLs in the order the cloaking check should run
    signal_weights: dict = field(default_factory=dict)    # #19: per-signal weights learned from owner answers


# ---------------------------------------------------------------- (de)serialise
def state_to_dict(st: ForgeState) -> dict:
    return {
        "brand_dna": asdict(st.brand_dna) if st.brand_dna else None,
        "suspects": [asdict(s) for s in st.suspects],
        "seen_domains": sorted(st.seen_domains),
        "round": st.round,
        "next_step": st.next_step,
        "log": list(st.log),
        "manual_urls": list(st.manual_urls),
        "round_new": list(st.round_new),
        "pending_hashes": list(st.pending_hashes),
        "handled_campaigns": list(st.handled_campaigns),
        "alerted": list(st.alerted),
        "cloak_queue": list(st.cloak_queue),
        "signal_weights": dict(st.signal_weights),
    }


def state_from_dict(d: dict) -> ForgeState:
    def _mk(cls, data):
        names = cls.__dataclass_fields__.keys()
        return cls(**{k: v for k, v in data.items() if k in names})
    return ForgeState(
        brand_dna=_mk(BrandDNA, d["brand_dna"]) if d.get("brand_dna") else None,
        suspects=[_mk(Suspect, s) for s in d.get("suspects", [])],
        seen_domains=set(d.get("seen_domains", [])),
        round=d.get("round", 0),
        next_step=d.get("next_step", ""),
        log=list(d.get("log", [])),
        manual_urls=list(d.get("manual_urls", [])),
        round_new=list(d.get("round_new", [])),
        pending_hashes=list(d.get("pending_hashes", [])),
        handled_campaigns=list(d.get("handled_campaigns", [])),
        alerted=list(d.get("alerted", [])),
        cloak_queue=list(d.get("cloak_queue", [])),
        signal_weights=dict(d.get("signal_weights", {})),
    )
