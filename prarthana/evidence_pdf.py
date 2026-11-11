"""Forensic-style evidence PDF for one suspect (A4, 3 pages: CASE FILE, EVIDENCE, ACTION).

Drawn directly on a ReportLab canvas so every section sits on a tinted panel and the last panel on each
page stretches down to the footer (no big empty white areas). Suspect contacts are only ever shown masked.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import io
import re
from pathlib import Path

import imagehash
from PIL import Image
from reportlab.lib.colors import HexColor, white
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.platypus import Paragraph

from schemas import BrandDNA, Suspect
from prarthana.common import CACHE, OUTPUT, cache_get, cache_set, domain_of, registrable

# ------------------------------------------------------------------ palette + page geometry
NAVY = HexColor("#0B1F3A")
NAVY_2 = HexColor("#16335C")
CORAL = HexColor("#FF6B57")
MINT = HexColor("#3DDC97")
RED = HexColor("#E5484D")
AMBER = HexColor("#F5A524")
PANEL = HexColor("#F2F4F7")
PAGE_BG = HexColor("#E9EDF2")
BORDER = HexColor("#D9DFE7")
INK = HexColor("#1B2433")
MUTED = HexColor("#5B6675")
SOFT = HexColor("#9FB0C8")
CARD = HexColor("#FFFFFF")

W, H = A4
M = 28                      # outer margin
CW = W - 2 * M              # content width
FOOT = 34                   # footer band height
GAP = 12                    # vertical gap between panels
PAD = 14                    # inner panel padding

LABEL_COLOURS = {"likely_fake": RED, "suspicious": AMBER, "ignored": MINT}
LABEL_TEXT = {"likely_fake": "LIKELY FAKE", "suspicious": "SUSPICIOUS", "ignored": "NO THREAT FOUND"}
FOUND_BY = {"manual": "Owner report", "urlscan_hash": "urlscan - image hash",
            "urlscan_hotlink": "urlscan - hotlinking", "urlscan_title": "urlscan - page title",
            "name_net": "Name net (look-alike name)"}
STATUS_TEXT = {"new": "New", "investigated": "Awaiting owner", "confirmed": "Confirmed fake",
               "not_fake": "Marked not fake", "reported": "Reported"}


def _t(s) -> str:
    """Escape for Paragraph markup. Helvetica has no rupee glyph -> 'Rs.'"""
    s = str(s if s is not None else "").replace("₹", "Rs. ")
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def style(size=9.5, leading=None, color=INK, font="Helvetica", **kw):
    return ParagraphStyle("x", fontName=font, fontSize=size, leading=leading or size * 1.38,
                          textColor=color, alignment=TA_LEFT, **kw)


def case_id(s: Suspect) -> str:
    day = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d")
    return f"FORGE-{day}-{hashlib.sha256(s.url.encode()).hexdigest()[:4].upper()}"


# ------------------------------------------------------------------ evidence gathering helpers
def brand_assets(dna: BrandDNA) -> dict:
    """Screenshot of the REAL store + thumbnails of its images (for original -> copy pairs). Cached."""
    key = dna.store_url
    cached = cache_get("brand_assets", key)
    if cached and Path(cached.get("screenshot", "")).exists():
        return cached
    from prarthana.investigator import fetch_page, _save_evidence_image
    from schemas import file_sha256, photo_phash
    shots = CACHE / "screenshots"
    shots.mkdir(parents=True, exist_ok=True)
    shot = shots / ("store_" + re.sub(r"[^A-Za-z0-9]+", "_", dna.store_url)[:70] + ".png")
    try:
        page = fetch_page(dna.store_url, str(shot))
    except Exception:
        return {}
    by_sha, by_phash = {}, []
    for b in page["images"].values():
        sha = file_sha256(b)
        p = _save_evidence_image(b, sha)
        if not p:
            continue
        by_sha[sha] = p
        try:
            by_phash.append([photo_phash(b), p])
        except Exception:
            pass
    out = {"screenshot": str(shot), "by_sha": by_sha, "by_phash": by_phash}
    cache_set("brand_assets", key, out)
    return out


def _original_for(match: dict, assets: dict):
    if match.get("sha256") and match["sha256"] in assets.get("by_sha", {}):
        return assets["by_sha"][match["sha256"]]
    ph = match.get("phash")
    if ph and assets.get("by_phash"):
        try:
            h = imagehash.hex_to_hash(ph)
            return min(assets["by_phash"], key=lambda x: h - imagehash.hex_to_hash(x[0]))[1]
        except Exception:
            return None
    return None


def abuse_contacts(s: Suspect) -> dict:
    """Registrar + hosting abuse contacts from RDAP (public business contacts, not personal data)."""
    from prarthana.investigator import rdap
    import requests
    out = {"registrar": "", "registrar_abuse": "", "host": "", "host_abuse": "", "ip": s.ip or ""}

    def vcard(e, field):
        for item in (e.get("vcardArray") or [None, []])[1]:
            if item and item[0] == field:
                return item[3]
        return ""

    def walk(ents, want_role):
        for e in ents or []:
            if want_role in (e.get("roles") or []):
                yield e
            yield from walk(e.get("entities"), want_role)

    data = rdap(s.domain or domain_of(s.url))
    if data:
        for e in walk(data.get("entities"), "registrar"):
            out["registrar"] = vcard(e, "fn") or out["registrar"]
            break
        for e in walk(data.get("entities"), "abuse"):
            out["registrar_abuse"] = vcard(e, "email") or out["registrar_abuse"]
            if out["registrar_abuse"]:
                break
    ip = s.ip
    if ip and not ip.startswith(("127.", "10.", "192.168.")):
        ipdata = cache_get("rdap_ip", ip)
        if ipdata is None:
            try:
                r = requests.get(f"https://rdap.org/ip/{ip}", timeout=15)
                ipdata = r.json() if r.ok else {}
            except Exception:
                ipdata = {}
            cache_set("rdap_ip", ip, ipdata)
        out["host"] = ipdata.get("name", "") or ""
        for e in walk(ipdata.get("entities"), "abuse"):
            out["host_abuse"] = vcard(e, "email")
            if out["host_abuse"]:
                break
    return out


def breakdown(s: Suspect) -> list:
    """[(signal, points, reason, colour)] parsed from the scorer's reasons."""
    rules = [("photo", "Product photos", CORAL), ("logo", "Logo / favicon", HexColor("#7C5CFF")),
             ("price", "Price undercut", AMBER), ("contact", "Unofficial contacts", RED),
             ("domain registered", "Young domain", HexColor("#2F80ED")),
             ("scam", "Scam phrases", MUTED)]
    rows = []
    for r in s.reasons or []:
        m = re.match(r"^\+(\d+):\s*(.*)$", r)
        pts, text = (int(m.group(1)), m.group(2)) if m else (0, re.sub(r"^Note \(no points\):\s*", "", r))
        name, col = "Note", MUTED
        for key, label, c in rules:
            if key in text.lower():
                name, col = label, c
                break
        rows.append((name, pts, text, col))
    return rows


# ------------------------------------------------------------------ drawing primitives
class Doc:
    def __init__(self, c, s: Suspect, dna: BrandDNA, total_pages: int):
        self.c, self.s, self.dna, self.total = c, s, dna, total_pages
        self.cid = case_id(s)
        self.now = dt.datetime.now(dt.timezone.utc).strftime("%d %b %Y, %H:%M UTC")
        self.y = H

    # ---- text
    def text(self, x, y, txt, size=9, font="Helvetica", color=INK, right=False, center=False, space=0):
        c = self.c
        c.setFillColor(color)
        c.setFont(font, size)
        if space:
            wdt = c.stringWidth(txt, font, size) + space * len(txt)
            x0 = x - wdt if right else (x - wdt / 2 if center else x)
            t = c.beginText(x0, y)
            t.setFont(font, size)
            t.setCharSpace(space)
            t.textLine(txt)
            t.setCharSpace(0)  # reset: char spacing is part of the PDF text state and would leak
            c.drawText(t)
        elif right:
            c.drawRightString(x, y, txt)
        elif center:
            c.drawCentredString(x, y, txt)
        else:
            c.drawString(x, y, txt)

    def para(self, txt, x, ytop, w, st) -> float:
        p = Paragraph(txt, st)
        _, h = p.wrap(w, 2000)
        p.drawOn(self.c, x, ytop - h)
        return h

    @staticmethod
    def para_h(txt, w, st) -> float:
        return Paragraph(txt, st).wrap(w, 2000)[1]

    def fit(self, txt, w, size, font="Helvetica-Bold") -> str:
        while txt and self.c.stringWidth(txt, font, size) > w:
            txt = txt[:-2] + "…" if not txt.endswith("…") else txt[:-2] + "…"
        return txt

    # ---- shapes
    def panel(self, x, y, w, h, fill=PANEL, r=8, stroke=BORDER):
        c = self.c
        c.setFillColor(fill)
        if stroke is not None:
            c.setStrokeColor(stroke)
            c.setLineWidth(0.6)
        c.roundRect(x, y, w, h, r, stroke=1 if stroke is not None else 0, fill=1)

    def chip(self, x, y, txt, fill, color=white, size=7.5, h=15, pad=7, right=False):
        c = self.c
        w = c.stringWidth(txt, "Helvetica-Bold", size) + 2 * pad
        if right:
            x -= w
        c.setFillColor(fill)
        c.roundRect(x, y, w, h, h / 2, stroke=0, fill=1)
        self.text(x + w / 2, y + (h - size) / 2 + 1.3, txt, size, "Helvetica-Bold", color, center=True)
        return w

    def section_title(self, x, ytop, num, title, sub=""):
        self.text(x, ytop - 11, num, 9, "Helvetica-Bold", CORAL)
        self.text(x + 20, ytop - 11, title, 11, "Helvetica-Bold", NAVY, space=0.8)
        if sub:
            self.text(x + CW - 2 * PAD, ytop - 11, sub, 8, "Helvetica", MUTED, right=True)
        c = self.c
        c.setStrokeColor(BORDER)
        c.setLineWidth(0.6)
        c.line(x, ytop - 19, x + CW - 2 * PAD, ytop - 19)
        return ytop - 28

    def image_fit(self, path, x, y, w, h, crop_top=False, bg=CARD):
        c = self.c
        c.setFillColor(bg)
        c.setStrokeColor(BORDER)
        c.rect(x, y, w, h, stroke=1, fill=1)
        try:
            im = Image.open(path).convert("RGB")
        except Exception:
            self.text(x + w / 2, y + h / 2 - 3, "image unavailable", 8, color=MUTED, center=True)
            return
        if crop_top:  # full-page screenshot: keep the top part with the box's aspect ratio
            ch = int(im.width * h / w)
            im = im.crop((0, 0, im.width, min(im.height, ch)))
        iw, ih = im.size
        scale = min(w / iw, h / ih)
        dw, dh = iw * scale, ih * scale
        c.drawImage(ImageReader(im), x + (w - dw) / 2, y + (h - dh) / 2, dw, dh)

    # ---- icons (simple vector glyphs, white on a navy disc)
    def icon(self, kind, cx, cy, r=11):
        c = self.c
        c.setFillColor(NAVY)
        c.circle(cx, cy, r, stroke=0, fill=1)
        c.setStrokeColor(white)
        c.setFillColor(white)
        c.setLineWidth(1.1)
        k = r * 0.5
        if kind == "globe":
            c.circle(cx, cy, k, stroke=1, fill=0)
            c.ellipse(cx - k * 0.45, cy - k, cx + k * 0.45, cy + k, stroke=1, fill=0)
            c.line(cx - k, cy, cx + k, cy)
        elif kind == "clock":
            c.circle(cx, cy, k, stroke=1, fill=0)
            c.line(cx, cy, cx, cy + k * 0.7)
            c.line(cx, cy, cx + k * 0.55, cy)
        elif kind == "radar":
            for f in (1, 0.6, 0.25):
                c.circle(cx, cy, k * f, stroke=1, fill=0)
            c.line(cx, cy, cx + k * 0.8, cy + k * 0.6)
        elif kind == "link":
            c.roundRect(cx - k, cy - k * 0.35, k * 1.1, k * 0.7, k * 0.35, stroke=1, fill=0)
            c.roundRect(cx - k * 0.1, cy - k * 0.35, k * 1.1, k * 0.7, k * 0.35, stroke=1, fill=0)
        elif kind == "image":
            c.rect(cx - k, cy - k * 0.75, 2 * k, 1.5 * k, stroke=1, fill=0)
            p = c.beginPath()
            p.moveTo(cx - k * 0.8, cy - k * 0.55)
            p.lineTo(cx - k * 0.2, cy + k * 0.25)
            p.lineTo(cx + k * 0.2, cy - k * 0.15)
            p.lineTo(cx + k * 0.8, cy - k * 0.55)
            c.drawPath(p, stroke=1, fill=0)
        elif kind == "flag":
            c.line(cx - k * 0.7, cy - k, cx - k * 0.7, cy + k)
            p = c.beginPath()
            p.moveTo(cx - k * 0.7, cy + k)
            p.lineTo(cx + k * 0.9, cy + k * 0.5)
            p.lineTo(cx - k * 0.7, cy)
            p.close()
            c.drawPath(p, stroke=0, fill=1)
        elif kind == "shield":
            p = c.beginPath()
            p.moveTo(cx, cy + k)
            p.lineTo(cx + k * 0.85, cy + k * 0.55)
            p.lineTo(cx + k * 0.6, cy - k * 0.5)
            p.lineTo(cx, cy - k)
            p.lineTo(cx - k * 0.6, cy - k * 0.5)
            p.lineTo(cx - k * 0.85, cy + k * 0.55)
            p.close()
            c.drawPath(p, stroke=1, fill=0)

    def warn_triangle(self, x, y, size, fill=white, mark=RED):
        c = self.c
        p = c.beginPath()
        p.moveTo(x, y)
        p.lineTo(x + size, y)
        p.lineTo(x + size / 2, y + size * 0.88)
        p.close()
        c.setFillColor(fill)
        c.drawPath(p, stroke=0, fill=1)
        self.text(x + size / 2, y + size * 0.14, "!", size * 0.62, "Helvetica-Bold", mark, center=True)

    def logo_mark(self, x, y, s=16):
        """FORGE mark: coral diamond with a navy notch."""
        c = self.c
        p = c.beginPath()
        p.moveTo(x + s / 2, y + s)
        p.lineTo(x + s, y + s / 2)
        p.lineTo(x + s / 2, y)
        p.lineTo(x, y + s / 2)
        p.close()
        c.setFillColor(CORAL)
        c.drawPath(p, stroke=0, fill=1)
        c.setFillColor(NAVY)
        c.rect(x + s * 0.4, y + s * 0.3, s * 0.2, s * 0.4, stroke=0, fill=1)

    # ---- page furniture
    def background(self):
        self.c.setFillColor(PAGE_BG)
        self.c.rect(0, 0, W, H, stroke=0, fill=1)

    def big_header(self):
        c = self.c
        hh = 104
        c.setFillColor(NAVY)
        c.rect(0, H - hh, W, hh, stroke=0, fill=1)
        # subtle diagonal accent
        c.setFillColor(NAVY_2)
        p = c.beginPath()
        p.moveTo(W * 0.58, H)
        p.lineTo(W, H)
        p.lineTo(W, H - hh)
        p.lineTo(W * 0.66, H - hh)
        p.close()
        c.drawPath(p, stroke=0, fill=1)
        self.logo_mark(M, H - 36, 16)
        self.text(M + 24, H - 31, "FORGE  ·  ART OF ORIGINAL", 9, "Helvetica-Bold", CORAL, space=1.2)
        self.text(M, H - 66, "EVIDENCE REPORT", 27, "Helvetica-Bold", white, space=1)
        self.text(M, H - 86, "Counterfeit storefront investigation  ·  confidential", 9, "Helvetica", SOFT)
        rx = W - M
        for i, (lab, val) in enumerate([("CASE ID", self.cid), ("GENERATED", self.now),
                                        ("BRAND", self.dna.brand_name or domain_of(self.dna.store_url))]):
            yy = H - 30 - i * 25
            self.text(rx, yy, lab, 6.5, "Helvetica-Bold", SOFT, right=True, space=0.8)
            self.text(rx, yy - 11, self.fit(val, 200, 10), 10, "Helvetica-Bold", white, right=True)
        c.setFillColor(CORAL)
        c.rect(0, H - hh - 4, W, 4, stroke=0, fill=1)
        self.y = H - hh - 4 - GAP

    def small_header(self, num, title, sub):
        c = self.c
        hh = 62
        c.setFillColor(NAVY)
        c.rect(0, H - hh, W, hh, stroke=0, fill=1)
        self.logo_mark(M, H - 27, 12)
        self.text(M + 18, H - 24, "FORGE  ·  EVIDENCE REPORT", 7.5, "Helvetica-Bold", CORAL, space=1)
        self.text(M, H - 50, f"{num}  {title}", 17, "Helvetica-Bold", white, space=1)
        tw = c.stringWidth(f"{num}  {title}", "Helvetica-Bold", 17) + len(f"{num}  {title}")
        self.text(M + tw + 10, H - 49, sub, 9, "Helvetica", SOFT)
        self.text(W - M, H - 24, "CASE ID", 6.5, "Helvetica-Bold", SOFT, right=True, space=0.8)
        self.text(W - M, H - 37, self.cid, 10, "Helvetica-Bold", white, right=True)
        self.text(W - M, H - 50, self.fit(self.s.domain, 200, 8.5, "Helvetica"), 8.5, "Helvetica", SOFT, right=True)
        c.setFillColor(CORAL)
        c.rect(0, H - hh - 3, W, 3, stroke=0, fill=1)
        self.y = H - hh - 3 - GAP

    def footer(self):
        c = self.c
        c.setFillColor(NAVY)
        c.rect(0, 0, W, FOOT - 8, stroke=0, fill=1)
        pn = c.getPageNumber()
        self.text(M, 10, "Generated by FORGE  ·  contacts hashed and masked (no personal data stored)", 7, "Helvetica",
                  SOFT)
        self.text(W - M, 10, f"{self.cid}   ·   Page {pn} of {self.total}", 7, "Helvetica-Bold", white, right=True)

    def finish_page(self):
        self.footer()
        self.c.showPage()

    def bottom(self):
        return FOOT + 2

    # ------------------------------------------------------------------ PAGE 1 — CASE FILE
    def page_case_file(self):
        s = self.s
        self.background()
        self.big_header()
        col = LABEL_COLOURS.get(s.label, MUTED)

        # hero: score ring + verdict badge + threshold scale
        hh = 178
        x0, ytop = M, self.y
        self.panel(x0, ytop - hh, CW, hh)
        c = self.c
        cx, cy, r = x0 + 92, ytop - hh / 2, 64
        c.setLineCap(1)
        c.setStrokeColor(HexColor("#DCE2EA"))
        c.setLineWidth(15)
        c.circle(cx, cy, r, stroke=1, fill=0)
        c.setStrokeColor(col)
        if s.score > 0:
            c.arc(cx - r, cy - r, cx + r, cy + r, startAng=90, extent=-3.6 * max(1, min(100, s.score)))
        c.setFillColor(CARD)
        c.circle(cx, cy, r - 12, stroke=0, fill=1)
        self.text(cx, cy - 4, str(s.score), 38, "Helvetica-Bold", NAVY, center=True)
        self.text(cx, cy - 22, "/ 100", 10, "Helvetica-Bold", MUTED, center=True)
        self.text(cx, cy + 30, "RISK SCORE", 6.5, "Helvetica-Bold", MUTED, center=True, space=0.8)

        rx = x0 + 186
        rw = CW - (rx - x0) - PAD
        yy = ytop - 26
        self.text(rx, yy, "VERDICT", 7, "Helvetica-Bold", MUTED, space=1)
        # badge
        bt = LABEL_TEXT.get(s.label, s.label.upper())
        bw = c.stringWidth(bt, "Helvetica-Bold", 16) + 16 * 0.08 * len(bt) + 56
        c.setFillColor(col)
        c.roundRect(rx, yy - 42, bw, 32, 7, stroke=0, fill=1)
        if s.label != "ignored":
            self.warn_triangle(rx + 11, yy - 34, 17, white, col)
        self.text(rx + 38, yy - 32, bt, 16, "Helvetica-Bold", white, space=1.3)
        self.text(rx + bw + 12, yy - 24, "Status", 7, "Helvetica-Bold", MUTED)
        self.text(rx + bw + 12, yy - 36, STATUS_TEXT.get(s.status, s.status), 10, "Helvetica-Bold", NAVY)
        # suspect url
        yy -= 62
        self.text(rx, yy, "SUSPECT STOREFRONT", 7, "Helvetica-Bold", MUTED, space=1)
        self.text(rx, yy - 15, self.fit(s.url, rw, 12), 12, "Helvetica-Bold", NAVY)
        self.text(rx, yy - 29, self.fit(f"impersonating {self.dna.store_url}", rw, 8.5, "Helvetica"), 8.5,
                  "Helvetica", MUTED)
        # threshold scale
        sy = ytop - hh + 30
        segs = [(0, 50, MINT, "ignored"), (50, 75, AMBER, "suspicious"), (75, 100, RED, "likely fake")]
        for a, b, cc, lab in segs:
            c.setFillColor(cc)
            c.rect(rx + rw * a / 100, sy, rw * (b - a) / 100 - 1.5, 7, stroke=0, fill=1)
            self.text(rx + rw * (a + b) / 200, sy - 11, lab, 6.5, "Helvetica-Bold", MUTED, center=True)
        for t in (0, 50, 75, 100):
            self.text(rx + rw * t / 100, sy + 11, str(t), 6.5, "Helvetica", MUTED, center=True)
        mx = rx + rw * min(100, s.score) / 100
        p = c.beginPath()
        p.moveTo(mx, sy + 7)
        p.lineTo(mx - 6, sy + 19)
        p.lineTo(mx + 6, sy + 19)
        p.close()
        c.setFillColor(NAVY)
        c.drawPath(p, stroke=0, fill=1)
        self.y = ytop - hh - GAP

        # AI verdict quote box
        st = style(11.5, 16.5, INK, "Helvetica")
        tw = CW - 70
        th = self.para_h(_t(s.verdict or "No verdict available."), tw, st)
        hh = max(96, th + 46)
        ytop = self.y
        self.panel(M, ytop - hh, CW, hh, fill=HexColor("#FFF4F1"), stroke=HexColor("#FFD3CB"))
        c.setFillColor(CORAL)
        c.rect(M, ytop - hh, 5, hh, stroke=0, fill=1)
        self.text(M + 18, ytop - 46, "“", 48, "Helvetica-Bold", CORAL)
        self.text(M + 52, ytop - 20, "AI VERDICT", 7.5, "Helvetica-Bold", CORAL, space=1.2)
        self.para(_t(s.verdict or "No verdict available."), M + 52, ytop - 30, tw, st)
        self.y = ytop - hh - GAP

        # key facts grid (3 x 2)
        exact, sim = len(s.image_exact_matches or []), len(s.image_similar_matches or [])
        age = f"{s.domain_age_days} days" if s.domain_age_days is not None else "Unknown"
        age_note = ("Registered under 30 days ago" if s.domain_age_days is not None and s.domain_age_days < 30
                    else "From RDAP registration date" if s.domain_age_days is not None else "RDAP had no record")
        facts = [("globe", "DOMAIN", s.domain or domain_of(s.url), s.ip and f"IP {s.ip}" or "IP not resolved"),
                 ("clock", "DOMAIN AGE", age, age_note),
                 ("radar", "FOUND BY", FOUND_BY.get(s.found_by, s.found_by), "Detection net"),
                 ("link", "CAMPAIGN", s.campaign_id or "Standalone", "Linked sites share contacts / images / IP"
                  if s.campaign_id else "No linked sites found yet"),
                 ("image", "MATCHED IMAGES", f"{exact + sim} found", f"{exact} exact copies · {sim} look-alikes"),
                 ("flag", "STATUS", STATUS_TEXT.get(s.status, s.status), "Owner confirmation required")]
        ytop = self.y
        hh = 28 + 2 * 62 + 10 + PAD
        self.panel(M, ytop - hh, CW, hh)
        yy = self.section_title(M + PAD, ytop - 8, "01", "KEY FACTS")
        cw = (CW - 2 * PAD - 2 * 10) / 3
        for i, (ic, lab, val, note) in enumerate(facts):
            cx0 = M + PAD + (i % 3) * (cw + 10)
            cy0 = yy - (i // 3) * 72 - 62
            self.panel(cx0, cy0, cw, 62, fill=CARD, r=6)
            self.icon(ic, cx0 + 21, cy0 + 31)
            self.text(cx0 + 40, cy0 + 44, lab, 6.5, "Helvetica-Bold", MUTED, space=0.8)
            self.text(cx0 + 40, cy0 + 29, self.fit(str(val), cw - 48, 10.5), 10.5, "Helvetica-Bold", NAVY)
            self.text(cx0 + 40, cy0 + 15, self.fit(note, cw - 48, 7, "Helvetica"), 7, "Helvetica", MUTED)
        self.y = ytop - hh - GAP

        # signals checklist — stretches to the footer
        ytop, ybot = self.y, self.bottom()
        self.panel(M, ybot, CW, ytop - ybot)
        yy = self.section_title(M + PAD, ytop - 8, "02", "SIGNALS CHECKED", "points add up to the risk score")
        got = {name: (pts, col) for name, pts, _, col in breakdown(s)}
        sig = [("Product photos", 30, "Brand product photos re-used (exact or look-alike)"),
               ("Logo / favicon", 20, "Brand logo or favicon shown on the site"),
               ("Price undercut", 15, "Prices at least 40% below the real store"),
               ("Unofficial contacts", 15, "Phone / UPI / e-mail not on the brand's official list"),
               ("Young domain", 10, "Domain registered less than 30 days ago"),
               ("Scam phrases", 0, "Pressure / payment phrases (noted, no points)")]
        rh = min(34, (yy - ybot - PAD) / len(sig))
        for i, (name, mx_, desc) in enumerate(sig):
            ry = yy - (i + 1) * rh
            hit = name in got
            if i:
                c.setStrokeColor(BORDER)
                c.setLineWidth(0.5)
                c.line(M + PAD, ry + rh, M + CW - PAD, ry + rh)
            cy_ = ry + rh / 2
            c.setFillColor(RED if hit and name != "Scam phrases" else (AMBER if hit else HexColor("#C9D1DC")))
            c.circle(M + PAD + 8, cy_, 7, stroke=0, fill=1)
            self.text(M + PAD + 8, cy_ - 3, "!" if hit else "–", 8.5, "Helvetica-Bold", white, center=True)
            self.text(M + PAD + 24, cy_ + 1.5, name, 9.5, "Helvetica-Bold", NAVY)
            self.text(M + PAD + 24, cy_ - 9, desc, 7.5, "Helvetica", MUTED)
            label = (f"+{got[name][0]}" if hit and got[name][0] else ("FOUND" if hit else "not found"))
            fill = (got[name][1] if hit and got[name][0] else (AMBER if hit else HexColor("#DCE2EA")))
            self.chip(M + CW - PAD, cy_ - 7.5, label + (f" / {mx_}" if mx_ else ""), fill,
                      white if hit else MUTED, right=True)
        self.finish_page()

    # ------------------------------------------------------------------ PAGE 2 — EVIDENCE
    def ensure(self, need, cont_title="EVIDENCE"):
        if self.y - need < self.bottom():
            self.finish_page()
            self.background()
            self.small_header("02", cont_title, "(continued)")

    def page_evidence(self, assets: dict):
        s, c = self.s, self.c
        self.background()
        self.small_header("02", "EVIDENCE", "what FORGE captured on the suspect site")

        # screenshots side by side
        hh = 200
        ytop = self.y
        self.panel(M, ytop - hh, CW, hh)
        yy = self.section_title(M + PAD, ytop - 8, "A", "SCREENSHOTS", "top of each page, captured headless")
        bw = (CW - 2 * PAD - 16) / 2
        bh = yy - (ytop - hh) - PAD - 26
        shots = [("REAL STORE", MINT, assets.get("screenshot"), domain_of(self.dna.store_url)),
                 ("SUSPECT", RED, s.screenshot_path, s.domain)]
        for i, (lab, colr, path, dom) in enumerate(shots):
            bx = M + PAD + i * (bw + 16)
            by = yy - bh - 4
            if path and Path(path).exists():
                self.image_fit(path, bx, by, bw, bh, crop_top=True)
            else:
                self.panel(bx, by, bw, bh, fill=CARD, r=0)
                self.text(bx + bw / 2, by + bh / 2, "Screenshot unavailable", 9, "Helvetica-Bold", MUTED, center=True)
            c.setStrokeColor(colr)
            c.setLineWidth(2)
            c.rect(bx, by, bw, bh, stroke=1, fill=0)
            self.chip(bx + 8, by + bh - 22, lab, colr)
            self.text(bx, by - 13, self.fit(dom, bw - 80, 8.5), 8.5, "Helvetica-Bold", NAVY)
            self.text(bx + bw, by - 13, "captured by FORGE", 7, "Helvetica", MUTED, right=True)
        self.y = ytop - hh - GAP

        # matched images: original -> copy pairs
        pairs = [(m, "EXACT COPY") for m in s.image_exact_matches or []] + \
                [(m, "LOOK-ALIKE") for m in s.image_similar_matches or []]
        shown = pairs[:6]
        rows = max(1, (len(shown) + 2) // 3)
        hh = 28 + rows * 104 + PAD - 4
        self.ensure(hh)
        ytop = self.y
        self.panel(M, ytop - hh, CW, hh)
        more = f"  (+{len(pairs) - 6} more)" if len(pairs) > 6 else ""
        yy = self.section_title(M + PAD, ytop - 8, "B", "MATCHED IMAGES",
                                f"brand original  →  suspect copy{more}")
        cw = (CW - 2 * PAD - 2 * 10) / 3
        if not shown:
            self.text(M + CW / 2, yy - 50, "No brand images were found on the suspect site.", 9, "Helvetica-Bold",
                      MUTED, center=True)
        for i, (m, kind) in enumerate(shown):
            cx0 = M + PAD + (i % 3) * (cw + 10)
            cy0 = yy - (i // 3) * 104 - 98
            self.panel(cx0, cy0, cw, 96, fill=CARD, r=6)
            t = 62
            orig = _original_for(m, assets)
            ox, kx = cx0 + 10, cx0 + cw - 10 - t
            for path, x in ((orig, ox), (m.get("path"), kx)):
                if path and Path(path).exists():
                    self.image_fit(path, x, cy0 + 26, t, t)
                else:
                    self.panel(x, cy0 + 26, t, t, fill=PANEL, r=0)
                    self.text(x + t / 2, cy0 + 26 + t / 2 - 3, "n/a", 8, "Helvetica-Bold", MUTED, center=True)
            # arrow
            ay = cy0 + 26 + t / 2
            c.setStrokeColor(CORAL)
            c.setFillColor(CORAL)
            c.setLineWidth(1.6)
            c.line(ox + t + 6, ay, kx - 9, ay)
            p = c.beginPath()
            p.moveTo(kx - 4, ay)
            p.lineTo(kx - 10, ay + 4.5)
            p.lineTo(kx - 10, ay - 4.5)
            p.close()
            c.drawPath(p, stroke=0, fill=1)
            self.text(ox, cy0 + 14, "original", 6.5, "Helvetica", MUTED)
            self.text(kx + t, cy0 + 14, "copy", 6.5, "Helvetica", MUTED, right=True)
            lw = c.stringWidth(kind, "Helvetica-Bold", 6.5) + 10
            self.chip(cx0 + (cw - lw) / 2, cy0 + 8, kind, RED if kind == "EXACT COPY" else AMBER, size=6.5,
                      h=13, pad=5)
        self.y = ytop - hh - GAP

        # score breakdown table + stacked bar
        rows_ = breakdown(s)
        st = style(8.3, 11, INK)
        rw = CW - 2 * PAD - 150
        heights = [max(22, self.para_h(_t(r[2]), rw, st) + 10) for r in rows_] or [22]
        hh = 28 + 16 + sum(heights) + 50 + PAD
        self.ensure(min(hh, 400))
        ytop = self.y
        self.panel(M, ytop - hh, CW, hh)
        yy = self.section_title(M + PAD, ytop - 8, "C", "SCORE BREAKDOWN", f"total {s.score} / 100")
        self.text(M + PAD, yy - 8, "SIGNAL", 6.5, "Helvetica-Bold", MUTED, space=0.8)
        self.text(M + PAD + 102, yy - 8, "POINTS", 6.5, "Helvetica-Bold", MUTED, space=0.8)
        self.text(M + PAD + 150, yy - 8, "REASON", 6.5, "Helvetica-Bold", MUTED, space=0.8)
        yy -= 16
        if not rows_:
            self.text(M + PAD, yy - 14, "No signals.", 9, color=MUTED)
        for (name, pts, reason, colr), rh in zip(rows_, heights):
            self.panel(M + PAD, yy - rh + 2, CW - 2 * PAD, rh - 4, fill=CARD, r=4, stroke=None)
            c.setFillColor(colr)
            c.rect(M + PAD, yy - rh + 2, 3, rh - 4, stroke=0, fill=1)
            self.text(M + PAD + 10, yy - rh / 2 - 3, name, 8.8, "Helvetica-Bold", NAVY)
            self.chip(M + PAD + 102, yy - rh / 2 - 7, f"+{pts}" if pts else "0", colr if pts else HexColor("#C9D1DC"),
                      size=7.5, h=14)
            self.para(_t(reason), M + PAD + 150, yy - (rh - self.para_h(_t(reason), rw, st)) / 2, rw, st)
            yy -= rh
        # stacked bar
        yy -= 14
        bx, bwid = M + PAD, CW - 2 * PAD
        c.setFillColor(HexColor("#DCE2EA"))
        c.roundRect(bx, yy - 14, bwid, 14, 4, stroke=0, fill=1)
        xx = bx
        for name, pts, _, colr in rows_:
            if pts <= 0:
                continue
            w_ = bwid * pts / 100
            c.setFillColor(colr)
            c.rect(xx, yy - 14, w_, 14, stroke=0, fill=1)
            if w_ > 26:
                self.text(xx + w_ / 2, yy - 10, f"+{pts}", 7, "Helvetica-Bold", white, center=True)
            xx += w_
        for t, lab in ((50, "50 suspicious"), (75, "75 likely fake")):
            tx = bx + bwid * t / 100
            c.setStrokeColor(NAVY)
            c.setLineWidth(1)
            c.setDash(2, 2)
            c.line(tx, yy - 18, tx, yy + 2)
            c.setDash()
            self.text(tx, yy - 27, lab, 6.5, "Helvetica-Bold", NAVY, center=True)
        self.text(bx, yy - 27, "0", 6.5, "Helvetica", MUTED)
        self.text(bx + bwid, yy - 27, "100", 6.5, "Helvetica", MUTED, right=True)
        self.text(xx + 4 if xx < bx + bwid - 60 else xx - 4, yy + 4, f"= {s.score}", 8, "Helvetica-Bold", NAVY,
                  right=xx >= bx + bwid - 60)
        self.y = ytop - hh - GAP

        # masked contacts — stretches to the footer
        n = len(s.contacts_masked or [])
        need = 28 + 16 + min(max(1, n), 3) * 22 + PAD + 14
        self.ensure(need)
        ytop, ybot = self.y, self.bottom()
        self.panel(M, ybot, CW, ytop - ybot)
        yy = self.section_title(M + PAD, ytop - 8, "D", "CONTACTS FOUND ON SUSPECT SITE", "masked · stored only as SHA-256")
        cols = [(0, "MASKED VALUE"), (130, "TYPE"), (230, "SHA-256 (first 16)"), (400, "OFFICIAL BRAND CONTACT?")]
        for dx, lab in cols:
            self.text(M + PAD + dx, yy - 8, lab, 6.5, "Helvetica-Bold", MUTED, space=0.8)
        yy -= 16
        official = set(self.dna.official_contact_hashes or [])
        if not n:
            self.text(M + PAD, yy - 14, "No phone numbers, UPI IDs or e-mails were found.", 9, color=MUTED)
        for i, mval in enumerate(s.contacts_masked or []):
            h_ = s.contact_hashes[i] if i < len(s.contact_hashes) else ""
            ry = yy - (i + 1) * 22
            if ry < ybot + 20:
                self.text(M + PAD, ry + 6, f"+{n - i} more (see cache/state.json, hashed)", 8, color=MUTED)
                break
            self.panel(M + PAD, ry, CW - 2 * PAD, 19, fill=CARD, r=3, stroke=None)
            kind = "Phone" if re.fullmatch(r"[0-9X]{10}", mval) else "UPI ID / e-mail"
            self.text(M + PAD + 6, ry + 6, mval, 9, "Courier-Bold", NAVY)
            self.text(M + PAD + 130, ry + 6, kind, 8.5, color=INK)
            self.text(M + PAD + 230, ry + 6, h_[:16] + "…", 8, "Courier", MUTED)
            ok = h_ in official
            self.chip(M + PAD + 400, ry + 2.5, "YES - official" if ok else "NO - unofficial", MINT if ok else RED,
                      size=6.8, h=14)
        self.text(M + PAD, ybot + 10, "Raw values are never stored: FORGE keeps only the SHA-256 hash and the masked "
                                      "form shown above.", 7.5, "Helvetica-Oblique", MUTED)
        self.finish_page()

    # ------------------------------------------------------------------ PAGE 3 — ACTION
    def page_action(self, contacts: dict):
        s, c = self.s, self.c
        self.background()
        self.small_header("03", "ACTION", "what FORGE did and what happens next")
        st_order = ["new", "investigated", "confirmed", "reported"]
        lvl = st_order.index(s.status) if s.status in st_order else (2 if s.status == "not_fake" else 1)
        flagged = s.label in ("likely_fake", "suspicious")
        steps = [
            ("Hunted", f"Found via {FOUND_BY.get(s.found_by, s.found_by)}.", "done"),
            ("Investigated", f"Visited in headless Chromium: screenshot, "
                             f"{len(s.image_exact_matches or []) + len(s.image_similar_matches or [])} brand images, "
                             f"{len(s.prices_found or {})} prices, {len(s.contact_hashes or [])} contacts (hashed).",
             "done" if s.screenshot_path or lvl >= 1 else "todo"),
            ("Scored", f"{s.score}/100 - {LABEL_TEXT.get(s.label, s.label)}. "
                       f"{len([r for r in s.reasons if r.startswith('+')])} signals triggered.", "done"),
            ("Verdict", "Plain-English verdict written for the brand owner (see page 1).",
             "done" if s.verdict else "todo"),
            ("Owner alerted", "E-mail with this evidence PDF sent to the brand owner." if flagged
             else "Not flagged, no alert needed.", "done" if flagged else "skip"),
            ("Brand confirmation", {"confirmed": "Owner confirmed: 'Yes, it's fake'.",
                                    "reported": "Owner confirmed: 'Yes, it's fake'.",
                                    "not_fake": "Owner marked this site as NOT fake."}.get(
                s.status, "Awaiting the brand owner's decision in the FORGE dashboard."),
             "done" if s.status in ("confirmed", "reported", "not_fake") else "now"),
            ("Abuse report", "Abuse report with evidence sent to the registrar." if s.status == "reported"
             else "Will be sent only after the owner confirms.", "done" if s.status == "reported" else "todo"),
        ]
        hh = 28 + len(steps) * 40 + PAD - 6
        ytop = self.y
        self.panel(M, ytop - hh, CW, hh)
        yy = self.section_title(M + PAD, ytop - 8, "A", "TIMELINE")
        x = M + PAD + 14
        for i, (title, desc, state) in enumerate(steps):
            cy_ = yy - 14 - i * 40
            if i < len(steps) - 1:
                c.setStrokeColor(MINT if state == "done" and steps[i + 1][2] == "done" else BORDER)
                c.setLineWidth(2)
                c.line(x, cy_ - 9, x, cy_ - 31)
            if state == "done":
                c.setFillColor(MINT)
                c.circle(x, cy_, 9, stroke=0, fill=1)
                c.setStrokeColor(white)
                c.setLineWidth(1.8)
                c.line(x - 4, cy_, x - 1, cy_ - 3.5)
                c.line(x - 1, cy_ - 3.5, x + 4.5, cy_ + 3.5)
            elif state == "now":
                c.setFillColor(CORAL)
                c.circle(x, cy_, 9, stroke=0, fill=1)
                c.setFillColor(white)
                c.circle(x, cy_, 3.5, stroke=0, fill=1)
            else:
                c.setFillColor(CARD)
                c.setStrokeColor(HexColor("#B7C0CC"))
                c.setLineWidth(1.5)
                c.circle(x, cy_, 8, stroke=1, fill=1)
            tag = {"done": ("DONE", MINT), "now": ("WAITING", CORAL), "todo": ("PENDING", HexColor("#B7C0CC")),
                   "skip": ("SKIPPED", HexColor("#B7C0CC"))}[state]
            self.text(x + 22, cy_ + 1, title, 10, "Helvetica-Bold", NAVY)
            self.text(x + 22, cy_ - 11, self.fit(desc, CW - 2 * PAD - 120, 8, "Helvetica"), 8, "Helvetica", MUTED)
            self.chip(M + CW - PAD, cy_ - 6, tag[0], tag[1], size=6.8, h=14, right=True)
        self.y = ytop - hh - GAP

        # registrar + host abuse contacts
        hh = 112
        ytop = self.y
        bw = (CW - 12) / 2
        cards = [("shield", "REGISTRAR (RDAP)", contacts.get("registrar") or "Not found in RDAP",
                  contacts.get("registrar_abuse") or "no abuse e-mail published"),
                 ("globe", "HOSTING NETWORK (RDAP)", contacts.get("host") or "Not available",
                  contacts.get("host_abuse") or ("IP " + contacts["ip"] if contacts.get("ip") else "IP not resolved"))]
        for i, (ic, lab, name, mail) in enumerate(cards):
            bx = M + i * (bw + 12)
            self.panel(bx, ytop - hh, bw, hh)
            self.icon(ic, bx + PAD + 12, ytop - 26, 12)
            self.text(bx + PAD + 32, ytop - 22, lab, 7, "Helvetica-Bold", MUTED, space=0.8)
            self.text(bx + PAD + 32, ytop - 34, "abuse contact", 7, "Helvetica", MUTED)
            self.text(bx + PAD, ytop - 60, self.fit(name, bw - 2 * PAD, 11), 11, "Helvetica-Bold", NAVY)
            c.setStrokeColor(BORDER)
            c.setLineWidth(0.6)
            c.line(bx + PAD, ytop - 70, bx + bw - PAD, ytop - 70)
            self.text(bx + PAD, ytop - 88, self.fit(mail, bw - 2 * PAD, 10, "Courier-Bold"), 10, "Courier-Bold", CORAL)
        self.y = ytop - hh - GAP

        # report status
        hh = 84
        ytop = self.y
        self.panel(M, ytop - hh, CW, hh, fill=NAVY, stroke=None)
        stcol = {"reported": MINT, "confirmed": AMBER, "not_fake": HexColor("#B7C0CC")}.get(s.status, CORAL)
        sttxt = {"reported": "REPORT SENT", "confirmed": "CONFIRMED · READY TO REPORT",
                 "not_fake": "CLOSED · NOT FAKE"}.get(s.status, "AWAITING BRAND CONFIRMATION")
        self.text(M + PAD + 4, ytop - 24, "REPORT STATUS", 7, "Helvetica-Bold", SOFT, space=1)
        self.chip(M + PAD + 4, ytop - 50, sttxt, stcol, NAVY if s.status != "new" else white, size=9, h=20, pad=10)
        msg = (f"Abuse report sent{(' to ' + s.abuse_email) if s.abuse_email else ''}."
               if s.status == "reported" else
               "Nothing is reported until the brand owner presses \"Yes, it's fake\" in the FORGE dashboard.")
        self.para(_t(msg), M + PAD + 4, ytop - 56, CW - 2 * PAD - 8, style(8.5, 11.5, white))
        self.y = ytop - hh - GAP

        # next steps — stretches to the footer
        ytop, ybot = self.y, self.bottom()
        self.panel(M, ybot, CW, ytop - ybot)
        yy = self.section_title(M + PAD, ytop - 8, "B", "NEXT STEPS FOR THE BRAND OWNER")
        tips = [("Review", "Compare the screenshots and image pairs on page 2 with your own store."),
                ("Confirm", "Press \"Yes, it's fake\" or \"Not fake\" in your FORGE dashboard."),
                ("Report", "FORGE then e-mails this PDF to the registrar's abuse contact shown above."),
                ("Warn customers", "Post your official domain and payment details on your social channels."),
                ("Watch", "Run FORGE with --watch to re-hunt every 6 hours for new copies.")]
        rh = min(36, (yy - ybot - PAD) / len(tips))
        for i, (t, d) in enumerate(tips):
            ry = yy - (i + 1) * rh
            c.setFillColor(NAVY)
            c.circle(M + PAD + 10, ry + rh / 2, 9, stroke=0, fill=1)
            self.text(M + PAD + 10, ry + rh / 2 - 3.2, str(i + 1), 9, "Helvetica-Bold", white, center=True)
            self.text(M + PAD + 28, ry + rh / 2 + 1, t, 9.5, "Helvetica-Bold", NAVY)
            self.text(M + PAD + 120, ry + rh / 2 + 1, d, 8.5, "Helvetica", INK)
        self.finish_page()


def _render(fileobj, s, dna, total, assets, contacts) -> int:
    c = rl_canvas.Canvas(fileobj, pagesize=A4)
    c.setTitle(f"FORGE evidence report - {s.domain}")
    c.setAuthor("FORGE")
    d = Doc(c, s, dna, total)
    d.page_case_file()
    d.page_evidence(assets)
    d.page_action(contacts)
    pages = c.getPageNumber() - 1
    c.save()
    return pages


def build_evidence_pdf(s: Suspect, dna: BrandDNA) -> str:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    s.domain = s.domain or domain_of(s.url)
    path = OUTPUT / f"evidence_{re.sub(r'[^A-Za-z0-9]+', '_', s.domain or s.url).strip('_')[:60]}.pdf"
    try:
        assets = brand_assets(dna) if dna.store_url else {}
    except Exception:
        assets = {}
    try:
        contacts = abuse_contacts(s)
    except Exception:
        contacts = {}
    total = _render(io.BytesIO(), s, dna, 3, assets, contacts)  # pass 1: count pages
    _render(str(path), s, dna, total, assets, contacts)
    s.pdf_path = str(path)
    return str(path)
