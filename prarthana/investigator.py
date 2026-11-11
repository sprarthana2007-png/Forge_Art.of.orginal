"""#3 Investigator — visit a suspect site, collect evidence, compare to Brand DNA.

Never stores raw contacts: only SHA256 hashes (hash_contact) and masked forms (mask_contact).
"""
from __future__ import annotations

import datetime as dt
import importlib
import re
import socket
import warnings
from dataclasses import asdict
from urllib.parse import urljoin

import requests

from schemas import (BrandDNA, Suspect, file_sha256, photo_phash, is_similar,
                     hash_contact, mask_contact)
from prarthana.common import (CACHE, cache_get, cache_set, domain_of, registrable, log, launch_chromium)

warnings.filterwarnings("ignore", category=UserWarning, module="PIL")
TIMEOUT_MS = 20_000
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36 FORGE-research"

PRICE_RE = re.compile(r"(?:₹|Rs\.?|INR)\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)", re.I)
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}")
UPI_RE = re.compile(r"\b[A-Za-z0-9.\-_]{2,64}@[A-Za-z]{2,32}\b(?!\.)")
PHONE_RE = re.compile(r"(?<!\d)(?:\+?91[\s\-]?|0)?[6-9]\d{4}[\s\-]?\d{5}(?!\d)")


# ------------------------------------------------------------------ text extraction
def extract_prices(text: str, product_names: list) -> dict:
    """Return {product name: price}. Price = first ₹/Rs/INR amount after the name (within 300 chars).
    Unmatched prices are returned as price_1, price_2 ..."""
    out = {}
    low = text.lower()
    for name in product_names or []:
        i = low.find(name.lower())
        if i < 0:
            continue
        m = PRICE_RE.search(text, i, i + len(name) + 300)
        if m:
            out[name] = float(m.group(1).replace(",", ""))
    if not out:
        for n, m in enumerate(PRICE_RE.finditer(text), 1):
            out[f"price_{n}"] = float(m.group(1).replace(",", ""))
            if n >= 20:
                break
    return out


def extract_contacts(text: str) -> list:
    """Return raw contact strings found in text (caller must hash/mask and discard them)."""
    found = []
    emails = EMAIL_RE.findall(text)
    found += emails
    rest = EMAIL_RE.sub(" ", text)
    found += UPI_RE.findall(rest)
    found += [p for p in PHONE_RE.findall(rest)]
    return found


def hashed_contacts(text: str):
    hashes, masked = [], []
    for raw in extract_contacts(text):
        h = hash_contact(raw)
        if h not in hashes:
            hashes.append(h)
            masked.append(mask_contact(raw))
    return hashes, masked


# ------------------------------------------------------------------ scam phrases (teammate #10)
def _find_scam_phrase_fn():
    candidates = [("surabhi.scam_phrases", "find_scam_phrases"),
                  ("surabhi.scam_phrases", "check_scam_phrases"),
                  ("surabhi.checklist", "find_scam_phrases"),
                  ("surabhi.scam_checklist", "find_scam_phrases"),
                  ("surabhi", "find_scam_phrases")]
    for mod, fn in candidates:
        try:
            f = getattr(importlib.import_module(mod), fn)
            if callable(f):
                return f
        except Exception:
            continue
    return None


def scam_phrases(text: str) -> list:
    f = _find_scam_phrase_fn()
    if not f:
        return []
    try:
        return list(f(text) or [])
    except Exception:
        return []


# ------------------------------------------------------------------ RDAP / DNS
def rdap(domain: str):
    d = registrable(domain)
    cached = cache_get("rdap", d)
    if cached is not None:
        return cached
    if d in ("localhost",) or re.fullmatch(r"[\d.]+", d):
        return None
    try:
        r = requests.get(f"https://rdap.org/domain/{d}", timeout=15,
                         headers={"Accept": "application/rdap+json"})
        if r.status_code != 200:
            return None
        data = r.json()
        cache_set("rdap", d, data)
        return data
    except Exception:
        return None


def domain_age_days(domain: str, state_log=None):
    data = rdap(domain)
    if not data:
        log(state_log, f"RDAP: no data for {domain}; domain_age_days=None")
        return None
    for ev in data.get("events", []):
        if ev.get("eventAction") == "registration":
            try:
                when = dt.datetime.fromisoformat(ev["eventDate"].replace("Z", "+00:00"))
                return (dt.datetime.now(dt.timezone.utc) - when).days
            except Exception:
                break
    log(state_log, f"RDAP: no registration event for {domain}")
    return None


def resolve_ip(domain: str):
    try:
        return socket.gethostbyname(registrable(domain))
    except Exception:
        return None


# ------------------------------------------------------------------ page fetch
def fetch_page(url: str, shot_path: str) -> dict:
    """Load url in headless Chromium. Returns {text, html, images: {abs_url: bytes}, favicon_url}."""
    from playwright.sync_api import sync_playwright

    images = {}
    with sync_playwright() as p:
        browser = launch_chromium(p)
        try:
            ctx = browser.new_context(user_agent=UA, viewport={"width": 1280, "height": 900})
            page = ctx.new_page()
            page.set_default_timeout(TIMEOUT_MS)

            def on_response(resp):
                try:
                    if resp.request.resource_type == "image" and resp.ok:
                        images[resp.url] = resp.body()
                except Exception:
                    pass

            page.on("response", on_response)
            page.goto(url, timeout=TIMEOUT_MS, wait_until="load")
            try:
                page.wait_for_load_state("networkidle", timeout=5000)
            except Exception:
                pass
            page.screenshot(path=shot_path, full_page=True)
            text = page.inner_text("body")
            html = page.content()
            srcs = page.eval_on_selector_all("img", "els => els.map(e => e.currentSrc || e.src).filter(Boolean)")
            fav = page.eval_on_selector_all("link[rel*='icon']", "els => els.map(e => e.href)")
            favicon_url = fav[0] if fav else urljoin(url, "/favicon.ico")
            links = page.eval_on_selector_all(
                "a[href^='mailto:'], a[href^='tel:'], a[href^='upi:']", "els => els.map(e => e.getAttribute('href'))")
            # Download <img> sources not captured by the response listener (lazy / cached), plus favicon.
            for src in list(srcs) + [favicon_url]:
                if src in images or src.startswith("data:"):
                    continue
                try:
                    r = ctx.request.get(src, timeout=TIMEOUT_MS)
                    if r.ok:
                        images[src] = r.body()
                except Exception:
                    pass
        finally:
            browser.close()
    # contact-ish link targets (mailto:, tel:, upi://pay?pa=...) — kept only in memory, hashed by caller
    link_text = []
    for h in links:
        h = re.sub(r"^(mailto:|tel:)", "", h)
        m = re.search(r"pa=([^&]+)", h)
        link_text.append(m.group(1) if m else h.split("?")[0])
    return {"text": text, "html": html, "images": images, "favicon_url": favicon_url,
            "link_text": "\n".join(link_text)}


# ------------------------------------------------------------------ main entry
def _save_evidence_image(b: bytes, sha: str) -> str:
    """Keep a copy of a matched image (public site content, not personal data) for the evidence PDF."""
    d = CACHE / "images"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{sha[:24]}.png"
    if not p.exists():
        try:
            from io import BytesIO
            from PIL import Image
            im = Image.open(BytesIO(b)).convert("RGB")
            im.thumbnail((300, 300))
            im.save(p)
        except Exception:
            return ""
    return str(p)


def compare_images(images: dict, favicon_url: str, dna: BrandDNA):
    exact, similar, all_sha = [], [], []
    logo_match = False
    dna_sha = set(dna.photo_sha256s or [])
    brand_marks_sha = {h for h in (dna.logo_sha256, dna.favicon_sha256) if h}
    brand_marks_ph = [h for h in (getattr(dna, "logo_phash", ""), getattr(dna, "favicon_phash", "")) if h]
    for img_url, b in images.items():
        sha = file_sha256(b)
        all_sha.append(sha)
        try:
            ph = photo_phash(b)
        except Exception:
            ph = None
        if sha in brand_marks_sha or (ph and any(is_similar(ph, x) for x in brand_marks_ph)):
            logo_match = True
            continue
        if sha in dna_sha:
            exact.append({"url": img_url, "sha256": sha, "path": _save_evidence_image(b, sha)})
        elif ph and any(is_similar(ph, x) for x in dna.photo_phashes or []):
            similar.append({"url": img_url, "phash": ph, "path": _save_evidence_image(b, sha)})
    return exact, similar, logo_match, all_sha


def investigate(suspect: Suspect, dna: BrandDNA, state_log=None, use_cache: bool = True) -> Suspect:
    """Fill in evidence fields on suspect. Never raises; on failure logs and returns suspect as-is."""
    suspect.domain = suspect.domain or domain_of(suspect.url)
    key = suspect.url + "|" + (dna.store_url if dna else "")
    cached = cache_get("investigator", key) if use_cache else None
    if cached:
        for k, v in cached.items():
            setattr(suspect, k, v)
        if suspect.status == "new":
            suspect.status = "investigated"
        log(state_log, f"investigate: cache hit {suspect.url}")
        return suspect

    shots = CACHE / "screenshots"
    shots.mkdir(parents=True, exist_ok=True)
    shot = shots / (re.sub(r"[^A-Za-z0-9]+", "_", suspect.url)[:80] + ".png")
    try:
        page = fetch_page(suspect.url, str(shot))
    except Exception as e:
        log(state_log, f"investigate: could not load {suspect.url}: {type(e).__name__}: {str(e).splitlines()[0][:120]}")
        return suspect

    suspect.screenshot_path = str(shot)
    exact, similar, logo, all_sha = compare_images(page["images"], page["favicon_url"], dna)
    suspect.image_exact_matches = exact
    suspect.image_similar_matches = similar
    suspect.logo_match = logo
    suspect.image_sha256s = all_sha
    text = page["text"]
    suspect.prices_found = extract_prices(text, dna.product_names)
    suspect.contact_hashes, suspect.contacts_masked = hashed_contacts(text + "\n" + page["link_text"])
    suspect.scam_phrases = scam_phrases(text)
    suspect.domain_age_days = domain_age_days(suspect.domain, state_log)
    suspect.ip = resolve_ip(suspect.domain)
    if suspect.status == "new":
        suspect.status = "investigated"

    fields = ["screenshot_path", "image_exact_matches", "image_similar_matches", "logo_match",
              "image_sha256s", "prices_found", "contact_hashes", "contacts_masked", "scam_phrases",
              "domain_age_days", "ip", "domain"]
    d = asdict(suspect)
    cache_set("investigator", key, {k: d[k] for k in fields})
    log(state_log, f"investigate: {suspect.url} exact={len(exact)} similar={len(similar)} logo={logo} "
                   f"prices={len(suspect.prices_found)} contacts={len(suspect.contact_hashes)}")
    return suspect
