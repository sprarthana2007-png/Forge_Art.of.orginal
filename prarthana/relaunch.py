"""#16 Relaunch tracking — is a new suspect the same operator as a fake the owner already confirmed?

History = suspects marked "confirmed" or "reported" in cache/state.json, plus cache/confirmed_fakes.json
(written by confirm(yes) / send_abuse_report, so the history survives `--fresh` runs).

A new suspect is a relaunch of an old fake when it shares any of:
  * a contact hash (phone / UPI / e-mail),
  * an exact image SHA-256 that is NOT one of the brand's own photos / logo (every clone copies those),
  * a public IP address (private / loopback IPs never count).
Matched suspects get suspect.relaunch_of = <old domain> (+10 in the scorer) and are investigated first.
"""
from __future__ import annotations

import datetime as dt
import ipaddress
import json
from dataclasses import asdict

from schemas import BrandDNA, Suspect
from prarthana.common import CACHE, log, plural

HISTORY_STATUSES = ("confirmed", "reported")
KEEP = ("url", "domain", "status", "ip", "contact_hashes", "contacts_masked", "image_sha256s")


def _file():
    return CACHE / "confirmed_fakes.json"


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


def remember(s: Suspect) -> None:
    """Add a confirmed / reported fake to cache/confirmed_fakes.json (no raw contacts: hashes + masked only)."""
    try:
        p = _file()
        p.parent.mkdir(parents=True, exist_ok=True)
        data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else []
        d = asdict(s)
        rec = {k: d.get(k) for k in KEEP}
        rec["status"] = s.status
        rec["confirmed_at"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        data = [x for x in data if x.get("url") != s.url] + [rec]
        p.write_text(json.dumps(data, indent=1), encoding="utf-8")
    except Exception:
        pass


def load_history(state_suspects: list | None = None) -> list:
    """Confirmed / reported fakes from the given state suspects, cache/state.json and cache/confirmed_fakes.json."""
    out = {}
    sources = []
    if state_suspects:
        sources.append([asdict(s) if isinstance(s, Suspect) else s for s in state_suspects])
    for p in (CACHE / "state.json", _file()):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            sources.append(d.get("suspects", []) if isinstance(d, dict) else d)
        except Exception:
            pass
    for src in sources:
        for x in src:
            if x.get("status") in HISTORY_STATUSES and x.get("url"):
                out[x["url"]] = {k: x.get(k) for k in KEEP}
    return list(out.values())


def match(s: Suspect, old: dict, dna: BrandDNA | None = None) -> list:
    """Plain-English reasons why `s` looks like a relaunch of `old` (empty = no match)."""
    reasons = []
    shared_c = set(s.contact_hashes or []) & set(old.get("contact_hashes") or [])
    if shared_c:
        masked = [m for h, m in zip(s.contact_hashes, s.contacts_masked or []) if h in shared_c]
        reasons.append(f"same {plural(len(shared_c), 'contact')}" + (f" ({', '.join(masked[:2])})" if masked else ""))
    shared_i = (set(s.image_sha256s or []) & set(old.get("image_sha256s") or [])) - _brand_images(dna)
    if shared_i:
        reasons.append(f"same {plural(len(shared_i), 'non-brand image')} (sha {sorted(shared_i)[0][:8]})")
    if s.ip and s.ip == old.get("ip") and _public_ip(s.ip):
        reasons.append(f"same IP address {s.ip}")
    return reasons


def detect(suspects: list, history: list, dna: BrandDNA | None = None, state_log=None) -> list:
    """Set relaunch_of / relaunch_evidence on suspects that match a confirmed fake. Returns the matched ones."""
    found = []
    for s in suspects:
        for old in history:
            if not old.get("domain") or old.get("url") == s.url or old.get("domain") == s.domain:
                continue
            why = match(s, old, dna)
            if why:
                s.relaunch_of = old["domain"]
                s.relaunch_evidence = why
                found.append(s)
                log(state_log, f"relaunch: {s.domain} looks like a relaunch of {old['domain']} ({'; '.join(why)})")
                break
    return found


def likely_relaunch_by_ip(s: Suspect, history: list) -> bool:
    """Cheap pre-check before investigation (urlscan already gives an IP): used to investigate these first."""
    return bool(s.ip and _public_ip(s.ip) and any(s.ip == h.get("ip") for h in history))


def history_image_hashes(history: list, dna: BrandDNA | None = None) -> list:
    """Non-brand image hashes of confirmed fakes: hunted on urlscan to find their relaunches."""
    brand = _brand_images(dna)
    return sorted({h for x in history for h in (x.get("image_sha256s") or [])} - brand)
