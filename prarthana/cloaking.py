"""#13 Cloaking check — does the suspect show bots a clean page and Instagram-ad shoppers the scam page?

check_cloaking(suspect) visits the suspect three ways and compares the visits:
  1. "bot"                   desktop headless Chromium, default user agent, no referrer
  2. "Instagram-ad shopper"  Android phone emulation, Instagram in-app browser user agent,
                             Referer https://l.instagram.com/, URL + ?fbclid=<random>&utm_source=ig&utm_medium=paid
  3. "India network"         urlscan.io Scan API from another network location (country FORGE_CLOAK_COUNTRY, default
                             "in"). Skipped, with the reason recorded, if the country / scan is not available.
Every pair of visits is compared on: screenshot pHash distance (> 18 = visually different), visible-text similarity
(difflib ratio < 0.6 = different text) and the final domain after redirects.

Never raises: on any failure the visit is marked failed and the check continues with what it has.
"""
from __future__ import annotations

import difflib
import hashlib
import html
import ipaddress
import re
import secrets
import time
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import requests

from schemas import Suspect, photo_phash
from prarthana.common import CACHE, cache_get, cache_set, domain_of, env, launch_chromium, log, registrable

TIMEOUT_MS = 20_000
VISUAL_DIFF = 18          # screenshot pHash distance above this = visually different
TEXT_DIFF = 0.60          # text similarity below this = different text
LAYOUT_TOLERANCE = 0.85   # visual-only differences with text this similar are treated as responsive layout
URLSCAN_POLL_S = 90       # max time to wait for a urlscan result
COUNTRY_NAMES = {"in": "India", "sg": "Singapore", "us": "US", "gb": "UK", "de": "Germany", "jp": "Japan",
                 "au": "Australia", "nl": "Netherlands", "fr": "France", "ca": "Canada"}
IG_SUFFIX = (" Instagram 312.0.0.34.111 Android (33/13; 420dpi; 1080x2400; Google/google; Pixel 7; panther; "
             "panther; en_IN; 548323754)")
VISIT_PROFILES = {
    "bot": "Desktop Chromium, default (headless) user agent, no referrer",
    "Instagram-ad shopper": "Pixel 7 phone, Instagram in-app browser, Referer l.instagram.com, fbclid + utm_medium=paid",
}


def country() -> str:
    return (env("FORGE_CLOAK_COUNTRY") or "in").lower()


def network_label() -> str:
    return f"{COUNTRY_NAMES.get(country(), country().upper())} network"


def ad_url(url: str) -> str:
    """URL as a shopper clicking an Instagram ad would open it."""
    p = urlparse(url)
    q = parse_qsl(p.query, keep_blank_values=True)
    q += [("fbclid", "IwAR" + secrets.token_urlsafe(24)), ("utm_source", "ig"), ("utm_medium", "paid")]
    return urlunparse(p._replace(query=urlencode(q)))


def norm_text(t: str) -> str:
    return re.sub(r"\s+", " ", (t or "").lower()).strip()


def text_similarity(a: str, b: str) -> float:
    a, b = norm_text(a)[:4000], norm_text(b)[:4000]
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()


def html_to_text(doc: str) -> str:
    doc = re.sub(r"(?is)<(script|style|noscript|template|svg)[^>]*>.*?</\1>", " ", doc or "")
    doc = re.sub(r"(?s)<!--.*?-->", " ", doc)
    doc = re.sub(r"(?i)<br\s*/?>|</(p|div|li|h\d|tr|section|article)>", "\n", doc)
    return html.unescape(re.sub(r"<[^>]+>", " ", doc))


def is_local(url: str) -> bool:
    host = (urlparse(url if "://" in url else "http://" + url).hostname or "").lower()
    if host in ("localhost", "") or host.endswith(".local"):
        return True
    try:
        a = ipaddress.ip_address(host)
        return a.is_private or a.is_loopback or a.is_link_local
    except ValueError:
        return False


def _shot_path(url: str, label: str):
    d = CACHE / "cloaking"
    d.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(url.encode()).hexdigest()[:16]
    return d / f"{key}_{re.sub(r'[^a-z0-9]+', '_', label.lower()).strip('_')}.png"


def _phash_file(path):
    try:
        with open(path, "rb") as f:
            return photo_phash(f.read())
    except Exception:
        return None


# ------------------------------------------------------------------ visits 1 + 2 (Playwright)
def browser_visits(url: str) -> list:
    """Visit 1 (bot) and visit 2 (Instagram-ad shopper) in one browser, separate contexts."""
    from playwright.sync_api import sync_playwright

    visits = []
    with sync_playwright() as p:
        browser = launch_chromium(p)
        try:
            phone = dict(p.devices.get("Pixel 7") or p.devices["Pixel 5"])
            phone["user_agent"] = phone["user_agent"] + IG_SUFFIX
            plans = [("bot", {"viewport": {"width": 1280, "height": 900}}, url, None),
                     ("Instagram-ad shopper", {**phone, "locale": "en-IN", "timezone_id": "Asia/Kolkata"},
                      ad_url(url), "https://l.instagram.com/")]
            for label, ctx_args, target, referer in plans:
                v = {"label": label, "profile": VISIT_PROFILES[label], "requested_url": target, "final_url": "",
                     "screenshot_path": "", "text_length": 0, "text": "", "phash": None, "error": ""}
                ctx = browser.new_context(**ctx_args)
                try:
                    page = ctx.new_page()
                    page.set_default_timeout(TIMEOUT_MS)
                    page.goto(target, timeout=TIMEOUT_MS, wait_until="load", referer=referer)
                    try:
                        page.wait_for_load_state("networkidle", timeout=4000)
                    except Exception:
                        pass
                    shot = _shot_path(url, label)
                    page.screenshot(path=str(shot), full_page=False)
                    v["final_url"] = page.url
                    v["text"] = page.inner_text("body")
                    v["screenshot_path"] = str(shot)
                except Exception as e:
                    v["error"] = f"{type(e).__name__}: {str(e).splitlines()[0][:120]}"
                finally:
                    ctx.close()
                visits.append(v)
        finally:
            browser.close()
    return visits


# ------------------------------------------------------------------ visit 3 (urlscan Scan API)
def available_countries() -> list:
    cached = cache_get("cloaking", "urlscan-available-countries")
    if cached is not None:
        return cached
    r = requests.get("https://urlscan.io/api/v1/availableCountries/", headers={"API-Key": env("URLSCAN_API_KEY")},
                     timeout=20)
    r.raise_for_status()
    out = [c.lower() for c in r.json().get("countries", [])]
    cache_set("cloaking", "urlscan-available-countries", out)
    return out


def urlscan_visit(url: str) -> tuple:
    """Returns (visit dict or None, status string: 'ok' | 'skipped: <reason>')."""
    label = network_label()
    if is_local(url):
        return None, "skipped: local address, urlscan cannot reach it"
    key = env("URLSCAN_API_KEY")
    if not key:
        return None, "skipped: URLSCAN_API_KEY not set"
    cc = country()
    try:
        avail = available_countries()
        if avail and cc not in avail:
            return None, (f"skipped: urlscan offers no {COUNTRY_NAMES.get(cc, cc.upper())} scanning location "
                          f"(available: {', '.join(avail)})")
    except Exception:
        pass  # list unavailable: let the scan request itself decide
    hdr = {"API-Key": key}
    try:
        r = requests.post("https://urlscan.io/api/v1/scan/", headers=hdr, timeout=20,
                          json={"url": url, "visibility": "unlisted", "country": cc})
    except Exception as e:
        return None, f"skipped: urlscan scan request failed ({type(e).__name__})"
    if r.status_code != 200:
        try:
            msg = r.json().get("message") or r.json().get("description") or r.text
        except Exception:
            msg = r.text
        return None, f"skipped: urlscan refused the scan (HTTP {r.status_code}: {str(msg)[:140]})"
    uuid = r.json().get("uuid")
    deadline = time.time() + URLSCAN_POLL_S
    time.sleep(10)  # urlscan asks clients to wait before polling
    data = None
    while time.time() < deadline:
        try:
            res = requests.get(f"https://urlscan.io/api/v1/result/{uuid}/", headers=hdr, timeout=20)
        except Exception:
            res = None
        if res is not None and res.status_code == 200:
            data = res.json()
            break
        if res is not None and res.status_code not in (404, 429):
            return None, f"skipped: urlscan result error (HTTP {res.status_code})"
        time.sleep(5)
    if data is None:
        return None, f"skipped: urlscan result not ready within {URLSCAN_POLL_S}s"
    v = {"label": label, "profile": f"urlscan.io scanner in {COUNTRY_NAMES.get(cc, cc.upper())} (scan {uuid})",
         "requested_url": url, "final_url": (data.get("page") or {}).get("url") or url, "screenshot_path": "",
         "text_length": 0, "text": "", "phash": None, "error": "", "urlscan_uuid": uuid}
    try:
        s = requests.get(f"https://urlscan.io/screenshots/{uuid}.png", headers=hdr, timeout=20)
        if s.ok and s.content[:4] == b"\x89PNG":
            shot = _shot_path(url, label)
            shot.write_bytes(s.content)
            v["screenshot_path"] = str(shot)
    except Exception:
        pass
    try:
        d = requests.get(f"https://urlscan.io/dom/{uuid}/", headers=hdr, timeout=20)
        if d.ok:
            v["text"] = html_to_text(d.text)
    except Exception:
        pass
    if not v["screenshot_path"] and not v["text"]:
        return None, "skipped: urlscan returned no screenshot or page content"
    return v, "ok"


# ------------------------------------------------------------------ comparison
def compare(a: dict, b: dict) -> dict:
    """Compare two visits. 'different' = different text, different final domain, or a visual difference that
    the text does not explain (pure layout differences between phone and desktop are not counted)."""
    out = {"pair": f"{a['label']} vs {b['label']}", "phash_distance": None, "text_similarity": None,
           "domains": [domain_of(a["final_url"]) if a["final_url"] else "", domain_of(b["final_url"]) if b["final_url"] else ""],
           "different": False, "reasons": [], "notes": []}
    if a.get("error") or b.get("error"):
        bad = a if a.get("error") else b
        out["notes"].append(f"Could not compare with the {bad['label']} visit (it failed to load).")
        return out
    sim = text_similarity(a["text"], b["text"])
    out["text_similarity"] = round(sim, 2)
    if a.get("phash") and b.get("phash"):
        import imagehash
        out["phash_distance"] = int(imagehash.hex_to_hash(a["phash"]) - imagehash.hex_to_hash(b["phash"]))
    da, db = (registrable(x) for x in out["domains"])
    if da and db and da != db:
        out["different"] = True
        out["reasons"].append(f"The {a['label']} ended up on {da}, but the {b['label']} was sent to {db}.")
    if sim < TEXT_DIFF:
        out["different"] = True
        out["reasons"].append(f"The {b['label']} saw different text than the {a['label']} "
                              f"(only {round(sim * 100)}% the same).")
    d = out["phash_distance"]
    if d is not None and d > VISUAL_DIFF:
        if sim < LAYOUT_TOLERANCE:
            out["different"] = True
            out["reasons"].append(f"The {b['label']}'s page also looks different from the {a['label']}'s "
                                  f"(screenshot difference {d}, threshold {VISUAL_DIFF}).")
        else:
            out["notes"].append(f"Screenshots of {a['label']} and {b['label']} differ (difference {d}) but the "
                                f"text is {round(sim * 100)}% the same: layout / screen-size difference, not counted.")
    return out


# ------------------------------------------------------------------ public entry
def check_cloaking(suspect: Suspect, use_urlscan: bool = True, state_log=None, use_cache: bool = True) -> dict:
    url = suspect.url
    ck = f"{url}|urlscan={use_urlscan}|{country()}"
    if use_cache:
        cached = cache_get("cloaking", ck)
        if cached:
            return cached
    result = {"cloaking": False, "visits": [], "differences": [], "notes": [], "comparisons": [],
              "visit3_status": "", "checked_at": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())}
    try:
        visits = browser_visits(url)
    except Exception as e:
        visits = []
        result["notes"].append(f"Browser visits failed: {type(e).__name__}")
    if use_urlscan:
        try:
            v3, status = urlscan_visit(url)
        except Exception as e:
            v3, status = None, f"skipped: {type(e).__name__}"
    else:
        v3, status = None, "skipped: time budget for this round was used up"
    result["visit3_status"] = status
    if v3:
        visits.append(v3)
    for v in visits:
        if v.get("screenshot_path"):
            v["phash"] = _phash_file(v["screenshot_path"])
        v["text_length"] = len(norm_text(v.get("text", "")))
    for i in range(len(visits)):
        for j in range(i + 1, len(visits)):
            c = compare(visits[i], visits[j])
            result["comparisons"].append(c)
            result["differences"] += c["reasons"]
            result["notes"] += c["notes"]
    result["cloaking"] = any(c["different"] for c in result["comparisons"])
    # keep the saved result small: no full page text, only what the report needs
    result["visits"] = [{k: v.get(k) for k in ("label", "profile", "requested_url", "final_url", "screenshot_path",
                                               "text_length", "error", "urlscan_uuid") if v.get(k) is not None}
                        for v in visits]
    if sum(1 for v in visits if not v.get("error")) < 2:
        result["notes"].append("Fewer than two visits loaded, so cloaking could not be checked.")
    log(state_log, f"cloaking: {url} -> {'DETECTED' if result['cloaking'] else 'none'} "
                   f"({len(visits)} visits; visit 3 {status})")
    cache_set("cloaking", ck, result)
    return result
