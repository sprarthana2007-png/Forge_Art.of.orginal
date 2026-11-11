"""#8 Scorer — rule-based points, labels and campaign grouping (plain Python, no NetworkX)."""
from __future__ import annotations

import hashlib
import ipaddress

from schemas import BrandDNA, Suspect
from prarthana.common import domain_of, plural, registrable

PTS_PHOTO = 30
PTS_LOGO = 20
PTS_PRICE = 15
PTS_CONTACT = 15
PTS_YOUNG = 10
PRICE_DROP = 0.40      # at least 40% below the brand price
YOUNG_DAYS = 30
LIKELY_FAKE = 75
SUSPICIOUS = 50


def is_allowed(domain: str, dna: BrandDNA) -> bool:
    d = registrable(domain).lower()
    own = registrable(domain_of(dna.store_url)).lower()
    allowed = {own} | {registrable(domain_of(a)).lower() for a in (dna.allow_list or [])}
    # the brand's own store is always allowed; for localhost compare host:port so the demo clone isn't hidden
    if d in ("localhost", "127.0.0.1"):
        return domain.lower() in {domain_of(dna.store_url)} | {domain_of(a) for a in dna.allow_list or []}
    return d in allowed or any(d.endswith("." + a) for a in allowed if a)


def label_for(score: int) -> str:
    if score >= LIKELY_FAKE:
        return "likely_fake"
    if score >= SUSPICIOUS:
        return "suspicious"
    return "ignored"


def score_suspect(s: Suspect, dna: BrandDNA) -> Suspect:
    s.domain = s.domain or domain_of(s.url)
    score, reasons = 0, []
    if is_allowed(s.domain, dna):
        s.score, s.label = 0, "ignored"
        s.reasons = [f"{s.domain} is the brand's own store or on the allow-list, so it is never flagged."]
        return s

    if s.image_exact_matches or s.image_similar_matches:
        score += PTS_PHOTO
        reasons.append(f"+{PTS_PHOTO}: uses the brand's product photos "
                       f"({plural(len(s.image_exact_matches), 'exact copy')}, {plural(len(s.image_similar_matches), 'look-alike')}).")
    if s.logo_match:
        score += PTS_LOGO
        reasons.append(f"+{PTS_LOGO}: shows the brand's logo or favicon.")

    cheap = []
    brand_prices = {k.lower(): v for k, v in (dna.prices or {}).items()}
    for name, price in (s.prices_found or {}).items():
        bp = brand_prices.get(name.lower())
        if bp and price and price <= bp * (1 - PRICE_DROP):
            cheap.append(f"{name} at ₹{price:,.0f} vs ₹{bp:,.0f} ({round(100 * (1 - price / bp))}% off)")
    if cheap:
        score += PTS_PRICE
        reasons.append(f"+{PTS_PRICE}: prices at least 40% below the real store: " + "; ".join(cheap) + ".")

    official = set(dna.official_contact_hashes or [])
    foreign = [h for h in s.contact_hashes or [] if h not in official]
    if foreign:
        idx = [i for i, h in enumerate(s.contact_hashes) if h not in official]
        shown = ", ".join(s.contacts_masked[i] for i in idx if i < len(s.contacts_masked))
        score += PTS_CONTACT
        reasons.append(f"+{PTS_CONTACT}: lists {plural(len(foreign), 'phone/UPI/email contact')} that "
                       f"{'is' if len(foreign) == 1 else 'are'} not the brand's "
                       f"official ones ({shown}).")
    if s.domain_age_days is not None and s.domain_age_days < YOUNG_DAYS:
        score += PTS_YOUNG
        reasons.append(f"+{PTS_YOUNG}: domain registered only {plural(s.domain_age_days, 'day')} ago.")
    if s.scam_phrases:
        reasons.append("Note (no points): scam-style phrases found: " + ", ".join(map(str, s.scam_phrases)) + ".")

    s.score = min(score, 100)
    s.label = label_for(s.score)
    s.reasons = reasons or ["No evidence of copying was found."]
    return s


# ------------------------------------------------------------------ campaigns
def _public_ip(ip):
    try:
        a = ipaddress.ip_address(ip)
        return not (a.is_private or a.is_loopback or a.is_reserved or a.is_link_local)
    except Exception:
        return False


def group_campaigns(suspects: list, dna: BrandDNA | None = None) -> list:
    """Union-find: suspects sharing any contact hash, any exact image SHA256 (other than the brand's own
    logo/favicon/photos, which every clone copies) or the same public IP get one campaign_id.
    Only non-ignored suspects are grouped; singletons get campaign_id=None."""
    brand_imgs = set()
    if dna:
        brand_imgs = set(dna.photo_sha256s or []) | {dna.logo_sha256, dna.favicon_sha256}
    live = [s for s in suspects if s.label != "ignored"]
    parent = list(range(len(live)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    owner = {}
    for i, s in enumerate(live):
        keys = [("c", h) for h in s.contact_hashes or []]
        keys += [("i", h) for h in (getattr(s, "image_sha256s", None) or []) if h not in brand_imgs]
        if getattr(s, "ip", None) and _public_ip(s.ip):
            keys.append(("ip", s.ip))
        for k in keys:
            if k in owner:
                parent[find(i)] = find(owner[k])
            else:
                owner[k] = i
    groups = {}
    for i in range(len(live)):
        groups.setdefault(find(i), []).append(live[i])
    for s in suspects:
        if s.label == "ignored":
            s.campaign_id = None
    for members in groups.values():
        if len(members) < 2:
            members[0].campaign_id = None
            continue
        cid = "C-" + hashlib.sha256("|".join(sorted(m.domain for m in members)).encode()).hexdigest()[:8]
        for m in members:
            m.campaign_id = cid
    return suspects


def campaign_shared_image_hashes(suspects: list, campaign_id: str, dna: BrandDNA | None = None) -> list:
    brand_imgs = set()
    if dna:
        brand_imgs = set(dna.photo_sha256s or []) | {dna.logo_sha256, dna.favicon_sha256}
    seen, shared = {}, set()
    for s in suspects:
        if s.campaign_id != campaign_id:
            continue
        for h in set(getattr(s, "image_sha256s", None) or []):
            if h in brand_imgs:
                continue
            seen[h] = seen.get(h, 0) + 1
            if seen[h] >= 2:
                shared.add(h)
    return sorted(shared)


def score_all(suspects: list, dna: BrandDNA) -> list:
    for s in suspects:
        score_suspect(s, dna)
    return group_campaigns(suspects, dna)
