"""Shared schemas + hashing helpers for FORGE (Team Shared 21).

Both teammates import from here. Only ADD fields; never rename or remove.
"""
from __future__ import annotations

import hashlib
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
    return str(imagehash.phash(Image.open(BytesIO(b)).convert("RGB")))


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


@dataclass
class ForgeState:
    brand_dna: Optional[BrandDNA] = None
    suspects: list = field(default_factory=list)
    seen_domains: set = field(default_factory=set)
    round: int = 0
    next_step: str = ""
    log: list = field(default_factory=list)


# ---------------------------------------------------------------- (de)serialise
def state_to_dict(st: ForgeState) -> dict:
    return {
        "brand_dna": asdict(st.brand_dna) if st.brand_dna else None,
        "suspects": [asdict(s) for s in st.suspects],
        "seen_domains": sorted(st.seen_domains),
        "round": st.round,
        "next_step": st.next_step,
        "log": list(st.log),
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
    )
