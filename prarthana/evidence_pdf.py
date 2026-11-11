"""Evidence PDF for one suspect — "bento" theme (A4, 5 pages: case file, evidence, cloaking check, campaign, action).

Rounded cards on a grainy stone page, numbered across the report ("01 / VERDICT" ...). Fonts are embedded
from prarthana/assets/fonts (Instrument Serif, JetBrains Mono, Inter Tight) with Times / Courier / Helvetica
fallbacks. Suspect contacts are only ever shown masked.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import io
import re
from pathlib import Path

import imagehash
from PIL import Image
from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.platypus import Paragraph

from schemas import BrandDNA, Suspect
from prarthana.common import CACHE, OUTPUT, cache_get, cache_set, domain_of, plural, registrable

ASSETS = Path(__file__).resolve().parent / "assets"
FONT_DIR = ASSETS / "fonts"
GRAIN = ASSETS / "grain.png"


# ------------------------------------------------------------------ fonts (embedded TTF, with fallbacks)
def _register(name: str, file: str, fallback: str) -> str:
    try:
        if name not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(name, str(FONT_DIR / file)))
        return name
    except Exception:
        return fallback


SERIF = _register("InstrumentSerif", "InstrumentSerif-Regular.ttf", "Times-Roman")
SERIF_I = _register("InstrumentSerif-Italic", "InstrumentSerif-Italic.ttf", "Times-Italic")
MONO = _register("JetBrainsMono", "JetBrainsMono-Regular.ttf", "Courier")
MONO_M = _register("JetBrainsMono-Medium", "JetBrainsMono-Medium.ttf", "Courier-Bold")
SANS = _register("InterTight", "InterTight-Regular.ttf", "Helvetica")
SANS_M = _register("InterTight-Medium", "InterTight-Medium.ttf", "Helvetica-Bold")
RUPEE_FONTS = {SANS, SANS_M} - {"Helvetica", "Helvetica-Bold"}  # only Inter Tight has the ₹ glyph

# ------------------------------------------------------------------ palette (only these) + geometry
STONE = HexColor("#E8E2D6")
BONE = HexColor("#F5F1EA")
INK = HexColor("#141414")
MUTED = HexColor("#5C564D")
BORDER = HexColor("#D3CBBC")
ORANGE = HexColor("#FF4F1A")
ORANGE_1 = HexColor("#FF7A4D")
ORANGE_2 = HexColor("#FFA27F")
ORANGE_3 = HexColor("#FFC6B0")
SAGE = HexColor("#A7B39A")
SHADES = [ORANGE, ORANGE_1, ORANGE_2, ORANGE_3]

W, H = A4
M = 22          # page margin
G = 6           # gap between cards
R = 12          # card corner radius
P = 13          # card inner padding
CW = W - 2 * M
TOP = H - 64    # content starts under the header
BOTTOM = 30     # content ends above the footer
SIG_H = 70      # height of the "SIGNATURE" card at the bottom of the last page
# where pyHanko draws the visible signature stamp (x1, y1, x2, y2), inside the SIGNATURE card
SIG_BOX = (int(M + P), int(BOTTOM + 10), int(W - M - P), int(BOTTOM + SIG_H - 28))

VERDICT_TEXT = {"likely_fake": "Likely fake.", "suspicious": "Suspicious.", "ignored": "No threat found."}
LABEL_TEXT = {"likely_fake": "likely fake", "suspicious": "suspicious", "ignored": "no threat found"}
FOUND_BY = {"manual": "Owner report", "urlscan_hash": "urlscan · image hash",
            "urlscan_hotlink": "urlscan · hotlinking", "urlscan_title": "urlscan · page title",
            "name_net": "Name net (look-alike name)"}
STATUS_TEXT = {"new": "new", "investigated": "awaiting owner", "confirmed": "confirmed fake",
               "not_fake": "marked not fake", "reported": "reported"}
SIGNALS = [("Product photos", 30, "Brand product photos re-used (exact or look-alike)"),
           ("Logo / favicon", 20, "Brand logo or favicon shown on the site"),
           ("Price undercut", 15, "Prices at least 40% below the real store"),
           ("Unofficial contacts", 15, "Phone / UPI / e-mail not on the brand's official list"),
           ("Young domain", 10, "Domain registered less than 30 days ago"),
           ("Cloaking", 10, "Different page for Instagram-ad shoppers than for bots"),
           ("Relaunch", 10, "Same contact / image / IP as a fake you already confirmed"),
           ("Scam phrases", 0, "Pressure / payment phrases (noted, no points)")]
SHORT = {"Relaunch": "relaunch", "Cloaking": "cloaking", "Product photos": "photos", "Logo / favicon": "logo", "Price undercut": "price",
         "Unofficial contacts": "contacts", "Young domain": "domain", "Scam phrases": "phrases", "Note": "note"}


def _t(s, rupee: bool = False) -> str:
    """Escape for Paragraph markup. Only Inter Tight has ₹; other fonts get 'Rs.'"""
    s = str(s if s is not None else "")
    if not rupee:
        s = s.replace("₹", "Rs. ")
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def style(size=9, leading=None, color=INK, font=None, **kw):
    font = font or SANS
    return ParagraphStyle("x", fontName=font, fontSize=size, leading=leading or size * 1.36,
                          textColor=color, alignment=TA_LEFT, **kw)


def grain_png() -> str:
    """Warm-brown film grain tile (transparent PNG), generated once and reused on every page."""
    if not GRAIN.exists():
        import numpy as np
        rng = np.random.default_rng(21)
        n = 256
        a = np.clip(rng.normal(0, 1, (n, n)), -3, 3)
        alpha = (np.clip(np.abs(a) / 3, 0, 1) ** 1.6 * 46).astype("uint8")
        rgba = np.zeros((n, n, 4), "uint8")
        rgba[..., 0], rgba[..., 1], rgba[..., 2] = 92, 70, 46
        rgba[..., 3] = alpha
        GRAIN.parent.mkdir(parents=True, exist_ok=True)
        Image.fromarray(rgba, "RGBA").save(GRAIN, optimize=True)
    return str(GRAIN)


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
    """[(signal, points, reason, colour)] parsed from the scorer's reasons (orange shades by position)."""
    rules = [("relaunch of", "Relaunch"), ("photo", "Product photos"), ("logo", "Logo / favicon"), ("price", "Price undercut"),
             ("contact", "Unofficial contacts"), ("domain registered", "Young domain"), ("cloaking", "Cloaking"),
             ("scam", "Scam phrases")]
    rows, k = [], 0
    for r in s.reasons or []:
        m = re.match(r"^\+(\d+):\s*(.*)$", r)
        pts, text = (int(m.group(1)), m.group(2)) if m else (0, re.sub(r"^Note \(no points\):\s*", "", r))
        name = next((label for key, label in rules if key in text.lower()), "Note")
        col = SHADES[k % len(SHADES)] if pts else BORDER
        k += 1 if pts else 0
        rows.append((name, pts, text, col))
    return rows


def pair_tag(match: dict, assets: dict) -> str:
    """'sha 3f2a9c1e' for exact copies, 'pHash Δ4' (distance to the closest brand photo) for look-alikes."""
    if match.get("sha256"):
        return "sha " + match["sha256"][:8]
    ph = match.get("phash")
    if ph and assets.get("by_phash"):
        try:
            h = imagehash.hex_to_hash(ph)
            return f"pHash Δ{min(h - imagehash.hex_to_hash(x[0]) for x in assets['by_phash'])}"
        except Exception:
            pass
    return "pHash " + (ph or "?")[:8]


def file_sha(path) -> str:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except Exception:
        return ""


def file_time(path) -> str:
    try:
        ts = dt.datetime.fromtimestamp(Path(path).stat().st_mtime, dt.timezone.utc)
        return ts.strftime("%Y-%m-%d %H:%M UTC")
    except Exception:
        return "unknown"


# ------------------------------------------------------------------ drawing
class Doc:
    def __init__(self, c, s: Suspect, dna: BrandDNA, total_pages: int, contacts: dict):
        self.c, self.s, self.dna, self.total, self.contacts = c, s, dna, total_pages, contacts or {}
        self.cid = case_id(s)
        self.when = dt.datetime.now(dt.timezone.utc)
        self.now = self.when.strftime("%d %b %Y · %H:%M UTC")
        self.n = 0          # card counter across the whole report
        self.y = TOP

    # ---- text helpers
    def text(self, x, y, txt, size=9, font=None, color=INK, right=False, center=False):
        font = font or SANS
        if font not in RUPEE_FONTS:
            txt = str(txt).replace("₹", "Rs. ")
        c = self.c
        c.setFillColor(color)
        c.setFont(font, size)
        (c.drawRightString if right else c.drawCentredString if center else c.drawString)(x, y, txt)

    def width(self, txt, size, font):
        return self.c.stringWidth(str(txt), font, size)

    def fit(self, txt, w, size, font):
        txt = str(txt)
        if self.width(txt, size, font) <= w:
            return txt
        while txt and self.width(txt + "…", size, font) > w:
            txt = txt[:-1]
        return txt + "…"

    def para(self, markup, x, ytop, w, st) -> float:
        p = Paragraph(markup, st)
        _, h = p.wrap(w, 3000)
        p.drawOn(self.c, x, ytop - h)
        return h

    @staticmethod
    def para_h(markup, w, st) -> float:
        return Paragraph(markup, st).wrap(w, 3000)[1]

    # ---- shapes
    def card(self, x, ytop, w, h, label, kind="bone"):
        """Rounded bento card with a numbered mono label. Returns the y where content can start."""
        c = self.c
        fill = {"bone": BONE, "ink": INK, "orange": ORANGE, "stone": STONE}[kind]
        c.setFillColor(fill)
        if kind in ("bone", "stone"):
            c.setStrokeColor(BORDER)
            c.setLineWidth(0.7)
        c.roundRect(x, ytop - h, w, h, R, stroke=1 if kind in ("bone", "stone") else 0, fill=1)
        if label:
            self.n += 1
            col = {"bone": MUTED, "ink": BORDER, "orange": INK, "stone": MUTED}[kind]
            self.text(x + P, ytop - P - 6, f"{self.n:02d} / {label.upper()}", 6.8, MONO_M, col)
        return ytop - P - 22

    def pill(self, x, y, txt, fill=ORANGE, color=INK, size=7, h=14, right=False, font=None):
        font = font or MONO_M
        w = self.width(txt, size, font) + 16
        if right:
            x -= w
        self.c.setFillColor(fill)
        self.c.roundRect(x, y, w, h, h / 2, stroke=0, fill=1)
        self.text(x + w / 2, y + (h - size) / 2 + 1.2, txt, size, font, color, center=True)
        return w

    def rule(self, x1, x2, y, color=BORDER, w=0.6):
        self.c.setStrokeColor(color)
        self.c.setLineWidth(w)
        self.c.line(x1, y, x2, y)

    def seg_bar(self, x, y, w, h, rows, track, gap=2, labels=False, label_color=INK):
        """One segment per scored signal, width = points/100, orange shades; `track` colour for the rest."""
        c = self.c
        c.setFillColor(track)
        c.roundRect(x, y, w, h, min(3, h / 2), stroke=0, fill=1)
        xx = x
        for name, pts, _, col in rows:
            if pts <= 0:
                continue
            sw = w * pts / 100
            c.setFillColor(col)
            c.rect(xx, y, max(0.5, sw - gap), h, stroke=0, fill=1)
            if labels and sw > 24:
                self.text(xx + (sw - gap) / 2, y + h / 2 - 2.6, f"+{pts}", 6.8, MONO_M, INK, center=True)
            xx += sw
        return xx

    def ticks(self, x, y, w, color, size=6.3):
        c = self.c
        for t, lab in ((0, "0"), (50, "50 suspicious"), (75, "75 likely fake"), (100, "100")):
            tx = x + w * t / 100
            c.setStrokeColor(color)
            c.setLineWidth(0.6)
            c.line(tx, y + 3, tx, y + 7)
            if t == 0:
                self.text(tx, y - 5, lab, size, MONO, color)
            elif t == 100:
                self.text(tx, y - 5, lab, size, MONO, color, right=True)
            else:
                self.text(tx, y - 5, lab, size, MONO, color, center=True)

    def arch(self, x, y, w, h):
        rh = min(w / 2, h * 0.42)
        p = self.c.beginPath()
        p.moveTo(x, y)
        p.lineTo(x + w, y)
        p.lineTo(x + w, y + h - rh)
        p.arcTo(x, y + h - 2 * rh, x + w, y + h, 0, 180)
        p.lineTo(x, y)
        p.close()
        return p

    def arch_image(self, path, x, y, w, h, top=False, outline=BORDER):
        """Image cover-fitted and clipped into an arch (rounded top, square bottom)."""
        c = self.c
        c.saveState()
        c.clipPath(self.arch(x, y, w, h), stroke=0, fill=0)
        c.setFillColor(STONE)
        c.rect(x, y, w, h, stroke=0, fill=1)
        ok = False
        if path and Path(path).exists():
            try:
                im = Image.open(path).convert("RGB")
                ar = w / h
                if top:                                 # screenshot: keep the top of the page
                    im = im.crop((0, 0, im.width, min(im.height, int(im.width / ar) if im.width / im.height <= ar
                                                      else im.height)))
                if im.width / im.height > ar:          # too wide: crop sides, centred on the page content
                    nw = int(im.height * ar)
                    cx = im.width // 2
                    if top:
                        from PIL import ImageChops
                        bg = Image.new("RGB", im.size, im.getpixel((im.width - 1, im.height - 1)))
                        box = ImageChops.difference(im, bg).convert("L").point(lambda v: 255 if v > 18 else 0).getbbox()
                        if box:
                            cx = (box[0] + box[2]) // 2
                    l = min(max(0, cx - nw // 2), im.width - nw)
                    im = im.crop((l, 0, l + nw, im.height))
                else:                                   # too tall: crop bottom (or centre)
                    nh = int(im.width / ar)
                    t = 0 if top else (im.height - nh) // 2
                    im = im.crop((0, t, im.width, t + nh))
                c.drawImage(ImageReader(im), x, y, w, h)
                ok = True
            except Exception:
                pass
        c.restoreState()
        if not ok:
            self.text(x + w / 2, y + h / 2 - 3, "no image", 7, MONO, MUTED, center=True)
        c.setStrokeColor(outline)
        c.setLineWidth(0.8)
        c.drawPath(self.arch(x, y, w, h), stroke=1, fill=0)

    # ---- page furniture
    def new_page(self):
        c = self.c
        c.setFillColor(STONE)
        c.rect(0, 0, W, H, stroke=0, fill=1)
        g = grain_png()
        tile = 128
        for i in range(int(W // tile) + 1):
            for j in range(int(H // tile) + 1):
                c.drawImage(g, i * tile, j * tile, tile, tile, mask="auto")
        # header: wordmark + case id
        self.text(M, H - 46, "Forge", 30, SERIF, INK)
        wx = M + self.width("Forge", 30, SERIF) + 6
        c.setFillColor(ORANGE)
        c.circle(wx, H - 43.5, 3.6, stroke=0, fill=1)
        self.text(wx + 12, H - 44, "art of original · evidence report", 7.2, MONO, MUTED)
        self.text(W - M, H - 34, self.cid, 9, MONO_M, INK, right=True)
        self.text(W - M, H - 47, self.now, 7.2, MONO, MUTED, right=True)
        self.rule(M, W - M, H - 56, BORDER, 0.7)
        self.y = TOP

    def finish_page(self):
        pn = self.c.getPageNumber()
        self.rule(M, W - M, 22, BORDER, 0.6)
        self.text(M, 11, "contacts hashed + masked · no personal data", 7, MONO, MUTED)
        self.text(W - M, 11, f"art of original — p.{pn}/{self.total}", 7, MONO, MUTED, right=True)
        self.c.showPage()

    def ensure(self, need):
        if self.y - need < BOTTOM:
            self.finish_page()
            self.new_page()

    # ------------------------------------------------------------------ PAGE 1 — case file
    def page_case_file(self):
        s, c, ct = self.s, self.c, self.contacts
        self.new_page()
        rows = breakdown(s)
        wl = round(CW * 0.64)
        wr = CW - wl - G
        ha, hb = 318, 214
        # give spare page height to the top two rows so the summary card has no big empty band
        summary_need = 36 + self.para_h(_t(s.verdict or "No verdict available."), CW - 2 * P,
                                        style(16, 21, INK, SERIF)) + 52
        extra = (TOP - BOTTOM) - (ha + hb + 2 * G + summary_need)
        if extra > 0:
            ha += round(extra * 0.45)
            hb += round(extra * 0.55)
        y0 = self.y

        # 01 VERDICT — big ink card
        x = M
        yy = self.card(x, y0, wl, ha, "verdict", "ink")
        self.pill(x + wl - P, y0 - P - 10, STATUS_TEXT.get(s.status, s.status), right=True)
        self.text(x + P, yy - 44, VERDICT_TEXT.get(s.label, s.label), 50, SERIF_I, BONE)
        brand = self.dna.brand_name or (domain_of(self.dna.store_url) if self.dna.store_url else "") or "the brand"
        self.text(x + P, yy - 62, f"for {brand} · threshold 75 = likely fake", 7.5, MONO, BORDER)
        score = str(s.score)
        self.text(x + P - 4, yy - 168, score, 118, SERIF, ORANGE)
        sx = x + P - 4 + self.width(score, 118, SERIF) + 8
        self.text(sx, yy - 168, "/100 risk", 11, MONO_M, BONE)
        by = y0 - ha + 58
        bw = wl - 2 * P
        self.seg_bar(x + P, by, bw, 12, rows, MUTED)
        self.ticks(x + P, by - 11, bw, BORDER)
        items = [(f"{SHORT.get(name, name)} +{pts}", col) for name, pts, _, col in rows if pts > 0]
        lines, cur, cw_ = [], [], 0
        for lab, col in items:
            iw = 10 + self.width(lab, 6.6, MONO) + 10
            if cur and cw_ + iw > wl - 2 * P:
                lines.append(cur)
                cur, cw_ = [], 0
            cur.append((lab, col))
            cw_ += iw
        if cur:
            lines.append(cur)
        for li, line in enumerate(lines[:2]):
            ly = y0 - ha + 17 + (len(lines[:2]) - 1 - li) * 11
            lx = x + P
            for lab, col in line:
                c.setFillColor(col)
                c.rect(lx, ly, 7, 7, stroke=0, fill=1)
                self.text(lx + 10, ly + 1, lab, 6.6, MONO, BORDER)
                lx += 10 + self.width(lab, 6.6, MONO) + 10

        # 02 SUSPECT — orange card
        xr = M + wl + G
        st = style(9.5, 12.5, INK, MONO_M)
        st_small = style(8, 10.5, INK, MONO)
        store_txt = _t(self.dna.store_url or "the brand's store (URL not given)")
        rel = getattr(s, "relaunch_of", "") or ""
        need = 36 + self.para_h(_t(s.url), wr - 2 * P, st) + 21 + self.para_h(store_txt, wr - 2 * P, st_small) + 10
        if rel:
            need += 34
        hs = max(122, min(190, round(need)))
        yy = self.card(xr, y0, wr, hs, "suspect", "orange")
        h_ = self.para(_t(s.url), xr + P, yy - 2, wr - 2 * P, st)
        self.text(xr + P, yy - h_ - 16, "copying", 7, MONO, INK)
        h2 = self.para(store_txt, xr + P, yy - h_ - 21, wr - 2 * P, st_small)
        if rel:  # #16: this site relaunches a fake the owner already confirmed
            ry = yy - h_ - 21 - h2 - 10
            self.pill(xr + P, ry - 14, f"Relaunch of {rel}"[:40], fill=INK, color=BONE, size=6.8, h=15)

        # 03 TRACE — bone card
        ht = ha - hs - G
        yy = self.card(xr, y0 - hs - G, wr, ht, "trace")
        age = plural(s.domain_age_days, "day") if s.domain_age_days is not None else "unknown"
        trace = [("domain age", age + (" · young" if s.domain_age_days is not None and s.domain_age_days < 30 else "")),
                 ("found by", FOUND_BY.get(s.found_by, s.found_by)),
                 ("registrar", ct.get("registrar") or "not in RDAP"),
                 ("ip", s.ip or "not resolved"),
                 ("campaign", s.campaign_id or "standalone")]
        step = (ht - 34) / len(trace)
        for i, (k, v) in enumerate(trace):
            ty = yy - i * step
            self.text(xr + P, ty - 6, k, 6.6, MONO, MUTED)
            self.text(xr + P, ty - 19, self.fit(v, wr - 2 * P, 9.5, SANS_M), 9.5, SANS_M, INK)
        self.y = y0 - ha - G

        # 04 FACTS (left, narrow) + 05 SIGNALS (right, wide)
        y1 = self.y
        yy = self.card(M, y1, wr, hb, "facts")
        exact, sim = len(s.image_exact_matches or []), len(s.image_similar_matches or [])
        facts = [("domain", s.domain or domain_of(s.url), "IP " + s.ip if s.ip else "IP not resolved"),
                 ("matched images", f"{plural(exact + sim, 'image')} found",
                  f"{plural(exact, 'exact copy')} · {plural(sim, 'look-alike')}"),
                 ("status", STATUS_TEXT.get(s.status, s.status), "owner confirmation required")]
        step = (hb - 34) / len(facts)
        for i, (k, v, note) in enumerate(facts):
            ty = yy - i * step
            if i:
                self.rule(M + P, M + wr - P, ty + 6)
            self.text(M + P, ty - 6, k, 6.6, MONO, MUTED)
            self.text(M + P, ty - 22, self.fit(v, wr - 2 * P, 13, SERIF), 13, SERIF, INK)
            self.text(M + P, ty - 34, self.fit(note, wr - 2 * P, 7.4, SANS), 7.4, SANS, MUTED)

        xs = M + wr + G
        yy = self.card(xs, y1, wl, hb, "signals")
        self.text(xs + wl - P, y1 - P - 6, f"{len([r for r in rows if r[1]])} of {len([x for x in SIGNALS if x[1]])} scored", 6.8, MONO, MUTED,
                  right=True)
        got = {}
        for name, pts, _, col in rows:
            got.setdefault(name, (pts, col))
        step = (hb - 34) / len(SIGNALS)
        for i, (name, mx, desc) in enumerate(SIGNALS):
            ty = yy - i * step
            hit = name in got
            colr = INK if hit else MUTED
            self.text(xs + P, ty - 7, name, 9, SANS_M, colr)
            self.text(xs + P + 102, ty - 7, self.fit(desc, wl - 2 * P - 160, 7, SANS), 7, SANS, MUTED)
            pts = got[name][0] if hit else 0
            val = (f"+{pts} / {mx}" if mx else "noted") if hit else (f"— / {mx}" if mx else "—")
            self.text(xs + wl - P, ty - 7, val, 7.6, MONO_M, ORANGE if hit else MUTED, right=True)
            bx, bw = xs + P, wl - 2 * P
            c.setFillColor(BORDER)
            c.rect(bx, ty - 15, bw, 1.6, stroke=0, fill=1)
            frac = (pts / mx if mx else 1.0) if hit else 0
            if frac:
                c.setFillColor(ORANGE)
                c.rect(bx, ty - 15, bw * min(1, frac), 1.6, stroke=0, fill=1)
        self.y = y1 - hb - G

        # 06 ANALYST SUMMARY — full width, stretches to the footer
        y2 = self.y
        yy = self.card(M, y2, CW, y2 - BOTTOM, "analyst summary")
        verdict = s.verdict or "No verdict available."
        markup = _t(verdict)
        pat = r"\d+(?:\.\d+)?%(?:\s+(?:off|below))?"
        if re.search(pat, markup):
            markup = re.sub(pat, lambda m: f'<font color="#FF4F1A">{m.group(0)}</font>', markup)
        else:
            markup = re.sub(rf"\b{s.score}(?:/100)?\b", lambda m: f'<font color="#FF4F1A">{m.group(0)}</font>',
                            markup, count=1)
        self.para(markup, M + P, yy - 4, CW - 2 * P, style(16, 21, INK, SERIF))
        self.rule(M + P, M + CW - P, BOTTOM + 26)
        self.text(M + P, BOTTOM + 12, f"score {s.score}/100 · {LABEL_TEXT.get(s.label, s.label)} · "
                                      f"{plural(len([r for r in rows if r[1]]), 'signal')} triggered", 7, MONO, MUTED)
        self.text(M + CW - P, BOTTOM + 12, self.cid, 7, MONO_M, INK, right=True)
        self.finish_page()

    # ------------------------------------------------------------------ PAGE 2 — evidence
    def page_evidence(self, assets: dict):
        s, c = self.s, self.c
        self.new_page()

        # 07 / 08 screenshots in arches
        hh = 228
        bw = (CW - G) / 2
        y0 = self.y
        shots = [("original", SAGE, assets.get("screenshot"), self.dna.store_url),
                 ("suspect", ORANGE, s.screenshot_path, s.url)]
        for i, (tag, colr, path, url) in enumerate(shots):
            x = M + i * (bw + G)
            yy = self.card(x, y0, bw, hh, "screenshot")
            self.pill(x + bw - P, y0 - P - 10, tag, fill=colr, right=True)
            ih = 134
            self.arch_image(path, x + P, yy - ih - 2, bw - 2 * P, ih, top=True,
                            outline=SAGE if tag == "original" else ORANGE)
            ly = yy - ih - 16
            self.text(x + P, ly, self.fit(url, bw - 2 * P, 7.6, MONO_M), 7.6, MONO_M, INK)
            has = bool(path and Path(path).exists())
            self.text(x + P, ly - 12, "captured " + (file_time(path) if has else "unavailable"), 6.8, MONO, MUTED)
            sha = file_sha(path) if has else ""
            self.text(x + P, ly - 23, self.fit("sha256 " + (sha or "—"), bw - 2 * P, 6.8, MONO), 6.8, MONO, MUTED)
        self.y = y0 - hh - G

        # 09 matched images — pairs of arches with an orange "="
        pairs = [(m, "exact copy") for m in s.image_exact_matches or []] + \
                [(m, "look-alike") for m in s.image_similar_matches or []]
        shown = pairs[:6]
        nrows = max(1, (len(shown) + 2) // 3)
        ph_ = 108
        hh = 36 + nrows * (ph_ + G)
        self.ensure(hh)
        y0 = self.y
        yy = self.card(M, y0, CW, hh, "matched images")
        more = f" · +{len(pairs) - 6} more" if len(pairs) > 6 else ""
        self.text(M + CW - P, y0 - P - 6, f"brand original = suspect copy{more}", 6.8, MONO, MUTED, right=True)
        pw = (CW - 2 * P - 2 * G) / 3
        if not shown:
            self.text(M + CW / 2, yy - 50, "No brand images were found on the suspect site.", 10, SERIF_I, MUTED,
                      center=True)
        for i, (m, kind) in enumerate(shown):
            px = M + P + (i % 3) * (pw + G)
            pt = yy + 4 - (i // 3) * (ph_ + G)
            self.card(px, pt, pw, ph_, None, "stone")
            tw, th = 58, 66
            ox = px + 12
            kx = px + pw - 12 - tw
            self.arch_image(_original_for(m, assets), ox, pt - 10 - th, tw, th, outline=SAGE)
            self.arch_image(m.get("path"), kx, pt - 10 - th, tw, th, outline=ORANGE)
            self.text((ox + tw + kx) / 2, pt - 10 - th / 2 - 8, "=", 24, SERIF, ORANGE, center=True)
            self.text(ox, pt - 10 - th - 10, "original", 6.3, MONO, MUTED)
            self.text(kx + tw, pt - 10 - th - 10, "copy", 6.3, MONO, MUTED, right=True)
            tag = f"{kind} · {pair_tag(m, assets)}"
            tw_ = self.width(tag, 6.5, MONO_M) + 14
            self.pill(px + (pw - tw_) / 2, pt - ph_ + 6, tag, fill=ORANGE if kind == "exact copy" else ORANGE_3,
                      size=6.5, h=13)
        self.y = y0 - hh - G

        # 10 score breakdown
        rows = breakdown(s)
        rw = CW - 2 * P - 160
        st = style(8, 10.6, INK, SANS)
        heights = [max(20, self.para_h(_t(r[2], True), rw, st) + 9) for r in rows] or [20]
        hh = 36 + 14 + sum(heights) + 48
        self.ensure(min(hh, 420))
        y0 = self.y
        yy = self.card(M, y0, CW, hh, "score breakdown")
        self.text(M + CW - P, y0 - P - 6, f"total {s.score} / 100", 7.4, MONO_M, ORANGE, right=True)
        for dx, lab in ((0, "signal"), (108, "points"), (160, "reason")):
            self.text(M + P + dx, yy - 4, lab, 6.6, MONO, MUTED)
        yy -= 12
        if not rows:
            self.text(M + P, yy - 14, "No signals.", 9, SANS, MUTED)
        for (name, pts, reason, colr), rh in zip(rows, heights):
            self.rule(M + P, M + CW - P, yy)
            c.setFillColor(colr)
            c.circle(M + P + 3, yy - rh / 2 + 0.5, 3, stroke=0, fill=1)
            self.text(M + P + 11, yy - rh / 2 - 3, name, 8.8, SANS_M, INK)
            self.text(M + P + 108, yy - rh / 2 - 3, f"+{pts}" if pts else "0", 9, MONO_M,
                      ORANGE if pts else MUTED)
            self.para(_t(reason, True), M + P + 160, yy - (rh - self.para_h(_t(reason, True), rw, st)) / 2, rw, st)
            yy -= rh
        self.rule(M + P, M + CW - P, yy)
        yy -= 16
        end = self.seg_bar(M + P, yy - 12, CW - 2 * P, 12, rows, BORDER, labels=True)
        self.ticks(M + P, yy - 23, CW - 2 * P, MUTED)
        lab = f"= {s.score}"
        if end + 6 + self.width(lab, 8, MONO_M) < M + CW - P:
            self.text(end + 6, yy - 9, lab, 8, MONO_M, INK)
        self.y = y0 - hh - G

        # 11 contacts — stretches to the footer
        n = len(s.contacts_masked or [])
        need = 36 + 14 + min(max(1, n), 3) * 20 + 22
        self.ensure(need)
        y0 = self.y
        yy = self.card(M, y0, CW, y0 - BOTTOM, "contacts on suspect site")
        self.text(M + CW - P, y0 - P - 6, "masked · stored only as sha-256", 6.8, MONO, MUTED, right=True)
        cols = [(0, "masked value"), (140, "type"), (240, "sha-256 (first 16)"), (410, "official brand contact?")]
        for dx, lab in cols:
            self.text(M + P + dx, yy - 4, lab, 6.6, MONO, MUTED)
        yy -= 12
        official = set(self.dna.official_contact_hashes or [])
        if not n:
            self.text(M + P, yy - 16, "No phone numbers, UPI IDs or e-mails were found.", 9, SANS, MUTED)
        for i, mval in enumerate(s.contacts_masked or []):
            h_ = s.contact_hashes[i] if i < len(s.contact_hashes) else ""
            ry = yy - (i + 1) * 20
            if ry < BOTTOM + 30:
                self.text(M + P, ry + 6, f"+{n - i} more (hashed, see cache/state.json)", 7.5, MONO, MUTED)
                break
            self.rule(M + P, M + CW - P, ry + 20)
            kind = "phone" if re.fullmatch(r"[0-9X]{10}", mval) else "UPI ID / e-mail"
            self.text(M + P, ry + 6, mval, 8.6, MONO_M, INK)
            self.text(M + P + 140, ry + 6, kind, 8.4, SANS, INK)
            self.text(M + P + 240, ry + 6, h_[:16] + "…", 7.6, MONO, MUTED)
            ok = h_ in official
            self.pill(M + P + 410, ry + 2.5, "yes · official" if ok else "no · unofficial",
                      fill=SAGE if ok else ORANGE, size=6.6, h=13)
        self.text(M + P, BOTTOM + 11, "Raw values are never stored: FORGE keeps only the SHA-256 hash and the "
                                      "masked form shown above.", 7.4, SANS, MUTED)
        self.finish_page()

    # ------------------------------------------------------------------ PAGE 3 — action
    # ------------------------------------------------------------------ abuse contacts (RDAP)
    def abuse_cards(self, hh=112):
        ct = self.contacts
        bw = (CW - G) / 2
        y0 = self.y
        cards = [("registrar abuse", "rdap · domain", ct.get("registrar") or "Not found in RDAP",
                  ct.get("registrar_abuse") or "no abuse e-mail published"),
                 ("hosting abuse", "rdap · ip", ct.get("host") or "Not available",
                  ct.get("host_abuse") or ("IP " + ct["ip"] if ct.get("ip") else "IP not resolved"))]
        for i, (lab, src, name, mail) in enumerate(cards):
            x = M + i * (bw + G)
            yy = self.card(x, y0, bw, hh, lab)
            self.text(x + bw - P, y0 - P - 6, src, 6.8, MONO, MUTED, right=True)
            self.text(x + P, yy - 22, self.fit(name, bw - 2 * P, 19, SERIF), 19, SERIF, INK)
            self.rule(x + P, x + bw - P, yy - 36)
            self.text(x + P, yy - 54, self.fit(mail, bw - 2 * P, 8.6, MONO_M), 8.6, MONO_M, ORANGE)
        self.y = y0 - hh - G

    # ------------------------------------------------------------------ PAGE 4 — campaign map (#14)
    def page_campaign(self, gmap: dict):
        s, c = self.s, self.c
        self.new_page()
        gmap = gmap or {}
        members = gmap.get("members") or [s.domain]
        links = gmap.get("links") or []

        # map card
        from prarthana.campaign_graph import MAP_SIZE
        iw = CW - 2 * P
        ih = iw * MAP_SIZE[1] / MAP_SIZE[0]
        hh = 30 + ih + 30
        y0 = self.y
        yy = self.card(M, y0, CW, hh, "campaign map")
        self.text(M + CW - P, y0 - P - 6, s.campaign_id and f"campaign {s.campaign_id}" or "standalone suspect",
                  6.8, MONO_M, ORANGE if s.campaign_id else MUTED, right=True)
        png = gmap.get("png")
        if png and Path(png).exists():
            c.drawImage(png, M + P, yy - ih + 4, iw, ih, mask="auto")
        else:
            self.text(M + CW / 2, yy - ih / 2, "map unavailable", 9, MONO, MUTED, center=True)
        ly = y0 - hh + 12
        lx = M + P
        for col, lab, ring in ((SAGE, "brand", False), (ORANGE, "likely fake", False), (ORANGE_2, "suspicious", False),
                               (ORANGE, "this report", True)):
            c.setFillColor(col)
            if ring:
                c.setStrokeColor(INK)
                c.setLineWidth(1.6)
            c.circle(lx + 4, ly + 3, 4, stroke=1 if ring else 0, fill=1)
            self.text(lx + 12, ly, lab, 6.8, MONO, MUTED)
            lx += 22 + self.width(lab, 6.8, MONO) + 8
        self.text(M + CW - P, ly, "edges: same IP · shared contact · shared image   - - - copies brand", 6.6, MONO,
                  MUTED, right=True)
        self.y = y0 - hh - G

        # links + members — stretches down to the abuse cards
        y0 = self.y
        bottom = BOTTOM + 112 + G
        yy = self.card(M, y0, CW, y0 - bottom, "campaign links")
        self.text(M + CW - P, y0 - P - 6, f"{plural(len(members), 'site')} in this group", 6.8, MONO, MUTED,
                  right=True)
        ny = yy - 2
        if not links:
            ny -= self.para(_t("No other flagged site shares an IP address, a contact or an image with this one "
                               "(yet). FORGE re-checks every run and links new sites automatically."),
                            M + P, ny, CW - 2 * P, style(12.5, 16, INK, SERIF)) + 8
        for a, b, reasons in links[:6]:
            self.rule(M + P, M + CW - P, ny)
            self.text(M + P, ny - 13, self.fit(f"{a}  —  {b}", 230, 8.4, MONO_M), 8.4, MONO_M, INK)
            self.text(M + P + 240, ny - 13, self.fit("; ".join(reasons), CW - 2 * P - 240, 8.2, SANS), 8.2, SANS, INK)
            ny -= 20
        if len(links) > 6:
            self.text(M + P, ny - 12, f"+{len(links) - 6} more links (see output/campaign_graph.html)", 7.4, MONO,
                      MUTED)
            ny -= 18
        nodes = gmap.get("nodes") or {}
        if nodes and ny - 40 > bottom:
            ny -= 10
            for dx, lab in ((0, "member"), (250, "score"), (310, "label"), (400, "status")):
                self.text(M + P + dx, ny - 4, lab, 6.6, MONO, MUTED)
            ny -= 10
            for dom in members:
                if ny - 20 < bottom + 8:
                    break
                d = nodes.get(dom, {})
                self.rule(M + P, M + CW - P, ny)
                self.text(M + P, ny - 13, self.fit(dom + ("   ← this report" if dom == s.domain else ""), 240, 8.2,
                                                   MONO_M), 8.2, MONO_M, INK)
                self.text(M + P + 250, ny - 13, str(d.get("score", "")), 8.2, MONO, ORANGE)
                self.text(M + P + 310, ny - 13, LABEL_TEXT.get(d.get("label"), d.get("label", "")), 8.2, SANS, INK)
                self.text(M + P + 400, ny - 13, STATUS_TEXT.get(d.get("status"), d.get("status", "")), 8.2, SANS,
                          INK)
                ny -= 19
        self.text(M + P, bottom + 11, "Interactive version for the dashboard: output/campaign_graph.html", 7, MONO,
                  MUTED)
        self.y = bottom
        self.abuse_cards()
        self.finish_page()

    # ------------------------------------------------------------------ PAGE 3 — cloaking check (#13)
    @property
    def cloak(self) -> dict:
        return getattr(self.s, "cloaking_details", None) or {}

    def cloak_summary(self) -> str:
        cl = self.cloak
        if not cl.get("visits"):
            return "Not run for this suspect" + (f" ({cl['visit3_status'].replace('skipped: ', '')})."
                                                 if cl.get("visit3_status") else ".")
        names = ", ".join(v["label"] for v in cl["visits"])
        res = "cloaking detected" if cl.get("cloaking") else "no cloaking"
        return f"{plural(len(cl['visits']), 'visit')} ({names}): {res}."

    def page_cloaking(self):
        s, c, cl = self.s, self.c, self.cloak
        self.new_page()
        detected = bool(cl.get("cloaking"))
        visits = list(cl.get("visits") or [])
        v3 = cl.get("visit3_status") or "not run"
        if v3 == "ok":
            from prarthana.cloaking import country_name
            v3 = f"ok, from {cl.get('visit3_country_name') or country_name(cl.get('visit3_country'))}"

        # verdict band (ink)
        hh = 132
        y0 = self.y
        yy = self.card(M, y0, CW, hh, "cloaking check", "ink")
        self.pill(M + CW - P, y0 - P - 10, "cloaking detected" if detected else ("no cloaking" if visits
                  else "not checked"), fill=ORANGE if detected else SAGE, right=True)
        title = "Cloaking detected." if detected else ("No cloaking." if visits else "Not checked.")
        self.text(M + P, yy - 34, title, 38, SERIF_I, BONE)
        self.text(M + P, yy - 52, self.fit(f"{plural(len(visits), 'visit')} compared · visit 3: {v3}",
                                           CW - 2 * P, 7.4, MONO), 7.4, MONO, ORANGE if detected else BORDER)
        self.para(_t("Scam sites often show a clean page to bots and the real scam page only to shoppers who arrive "
                     "from an Instagram or Facebook ad. FORGE visited this site three different ways and compared "
                     "what each visit saw. If the visits see different pages, that difference is itself evidence."),
                  M + P, yy - 60, CW - 2 * P, style(8.2, 11, BORDER, SANS))
        self.y = y0 - hh - G

        # three visit cards with arch screenshots
        slots = [("bot", INK, BONE), ("Instagram-ad shopper", ORANGE, INK), (None, SAGE, INK)]
        by_label = {v["label"]: v for v in visits}
        net = next((v for v in visits if v["label"] not in ("bot", "Instagram-ad shopper")), None)
        from prarthana.cloaking import VISIT_PROFILES, country, country_name, network_label
        cc = (net or {}).get("country") or cl.get("visit3_country") or country()
        cname = f"{(net or {}).get('country_name') or cl.get('visit3_country_name') or country_name(cc)} ({cc})"
        bw = (CW - 2 * G) / 3
        comps_n = max(1, len(cl.get("comparisons") or []))
        comp_h = 36 + 14 + comps_n * 21 + 4
        diff_need = 36 + 40 + sum(self.para_h(_t(d, True), CW - 2 * P - 12, style(9.4, 12.6, INK, SANS)) + 5
                                  for d in (cl.get("differences") or [])) + \
            sum(self.para_h(_t("Note: " + n, True), CW - 2 * P - 12, style(7.8, 10.4, MUTED, SANS)) + 4
                for n in (cl.get("notes") or [])) + (30 if not cl.get("differences") else 0)
        hh = max(300, min(420, (self.y - BOTTOM) - comp_h - diff_need - 2 * G))
        ih = hh - 122
        y0 = self.y
        for i, (label, fill, fg) in enumerate(slots):
            v = by_label.get(label) if label else net
            name = label or (net["label"] if net else network_label())
            x = M + i * (bw + G)
            yy = self.card(x, y0, bw, hh, f"visit {i + 1}")
            self.pill(x + bw - P + 2, y0 - P - 10, name, fill=fill, color=fg, size=5.9, h=13, right=True)
            path = (v or {}).get("screenshot_path")
            self.arch_image(path if path and Path(path).exists() else None, x + P, yy - ih - 2, bw - 2 * P, ih,
                            top=True, outline=ORANGE if (detected and label == "Instagram-ad shopper") else BORDER)
            ly = yy - ih - 15
            if v and not v.get("error"):
                self.text(x + P, ly, self.fit(v.get("final_url") or "", bw - 2 * P, 6.9, MONO_M), 6.9, MONO_M, INK)
                info = f"{v.get('text_length', 0)} chars of visible text"
                if label is None:  # visit 3: the country comes first so it is never cut off
                    info = f"{cname} · {v.get('text_length', 0)} chars"
                self.text(x + P, ly - 11, self.fit(info, bw - 2 * P, 6.6, MONO), 6.6,
                          MONO_M if label is None else MONO, INK if label is None else MUTED)
                prof = v.get("profile") or VISIT_PROFILES.get(name, "")
            elif v:
                self.text(x + P, ly, "visit failed", 6.9, MONO_M, ORANGE)
                self.text(x + P, ly - 11, self.fit(v.get("error", ""), bw - 2 * P, 6.6, MONO), 6.6, MONO, MUTED)
                prof = v.get("profile") or ""
            else:
                self.text(x + P, ly, f"skipped · {cname}" if label is None else "not run", 6.9, MONO_M, MUTED)
                prof = v3.replace("skipped: ", "") if label is None else "Cloaking check was not run."
            self.para(_t(prof, True), x + P, ly - 17, bw - 2 * P, style(6.9, 9, MUTED, SANS))
        self.y = y0 - hh - G

        # pairwise comparisons
        comps = cl.get("comparisons") or []
        hh = 36 + 14 + max(1, len(comps)) * 21 + 4
        y0 = self.y
        yy = self.card(M, y0, CW, hh, "comparisons")
        self.text(M + CW - P, y0 - P - 6, "different = text < 60% same, other domain, or unexplained visual change",
                  6.6, MONO, MUTED, right=True)
        cols = [(0, "pair"), (210, "screenshot Δ"), (290, "text same"), (360, "final domains"), (470, "result")]
        for dx, lab in cols:
            self.text(M + P + dx, yy - 4, lab, 6.6, MONO, MUTED)
        yy -= 12
        if not comps:
            self.text(M + P, yy - 15, "No comparison possible (fewer than two visits loaded).", 8.5, SANS, MUTED)
        for i, cmp_ in enumerate(comps):
            ry = yy - (i + 1) * 21
            self.rule(M + P, M + CW - P, ry + 21)
            self.text(M + P, ry + 7, self.fit(cmp_["pair"], 200, 8.4, SANS_M), 8.4, SANS_M, INK)
            d = cmp_.get("phash_distance")
            self.text(M + P + 210, ry + 7, "—" if d is None else f"{d} {'>' if d > 18 else '≤'} 18", 7.8, MONO,
                      ORANGE if d is not None and d > 18 else INK)
            ts = cmp_.get("text_similarity")
            self.text(M + P + 290, ry + 7, "—" if ts is None else f"{round(ts * 100)}%", 7.8, MONO,
                      ORANGE if ts is not None and ts < 0.6 else INK)
            doms = [x for x in (cmp_.get("domains") or []) if x]
            dtxt = doms[0] if len(set(doms)) <= 1 and doms else " → ".join(doms)
            self.text(M + P + 360, ry + 7, self.fit(dtxt or "—", 104, 7.4, MONO), 7.4, MONO, INK)
            if cmp_.get("different"):
                self.pill(M + P + 470, ry + 3.5, "different", size=6.6, h=13)
            elif ts is None:
                self.pill(M + P + 470, ry + 3.5, "not compared", fill=BORDER, size=6.6, h=13)
            else:
                self.pill(M + P + 470, ry + 3.5, "same page", fill=SAGE, size=6.6, h=13)
        self.y = y0 - hh - G

        # differences — stretches to the footer
        y0 = self.y
        yy = self.card(M, y0, CW, y0 - BOTTOM, "differences found")
        diffs = list(cl.get("differences") or [])
        notes = list(cl.get("notes") or [])
        st_ = style(9.4, 12.6, INK, SANS)
        ny = yy - 2
        if not diffs:
            ny -= self.para(_t("No differences that point to cloaking: every visit saw the same page." if visits
                               else "The cloaking check was not run for this suspect."), M + P, ny, CW - 2 * P,
                            style(13, 17, INK, SERIF)) + 6
        for d in diffs:
            c.setFillColor(ORANGE)
            c.circle(M + P + 3, ny - 6.5, 2.6, stroke=0, fill=1)
            ny -= self.para(_t(d, True), M + P + 12, ny, CW - 2 * P - 12, st_) + 5
        for n in notes:
            if ny < BOTTOM + 40:
                break
            c.setFillColor(BORDER)
            c.circle(M + P + 3, ny - 5.5, 2.2, stroke=0, fill=1)
            ny -= self.para(_t("Note: " + n, True), M + P + 12, ny, CW - 2 * P - 12, style(7.8, 10.4, MUTED, SANS)) + 4
        self.rule(M + P, M + CW - P, BOTTOM + 26)
        self.text(M + P, BOTTOM + 12, self.fit(f"visit 3: {v3}", CW - 2 * P - 90, 7, MONO), 7, MONO, MUTED)
        self.text(M + CW - P, BOTTOM + 12, cl.get("checked_at") or "", 7, MONO_M, INK, right=True)
        self.finish_page()

    def page_action(self):
        s, c, ct = self.s, self.c, self.contacts
        self.new_page()
        st_order = ["new", "investigated", "confirmed", "reported"]
        lvl = st_order.index(s.status) if s.status in st_order else (2 if s.status == "not_fake" else 1)
        flagged = s.label in ("likely_fake", "suspicious")
        n_img = len(s.image_exact_matches or []) + len(s.image_similar_matches or [])
        shot_t = file_time(s.screenshot_path) if s.screenshot_path and Path(s.screenshot_path).exists() else ""
        gen_t = self.when.strftime("%Y-%m-%d %H:%M UTC")
        steps = [
            ("Hunted", f"Found via {FOUND_BY.get(s.found_by, s.found_by)}.", "done", "—"),
            ("Investigated", f"Visited in headless Chromium: screenshot, {plural(n_img, 'brand image')}, "
                             f"{plural(len(s.prices_found or {}), 'price')}, "
                             f"{plural(len(s.contact_hashes or []), 'contact')} (hashed).",
             "done" if s.screenshot_path or lvl >= 1 else "todo", shot_t or "—"),
            ("Cloaking check", self.cloak_summary(), "done" if self.cloak.get("visits") else "todo",
             self.cloak.get("checked_at") or "not run"),
            ("Scored", f"{s.score}/100 · {LABEL_TEXT.get(s.label, s.label)} · "
                       f"{plural(len([r for r in s.reasons if r.startswith('+')]), 'signal')} triggered.",
             "done", gen_t),
            ("Verdict", "Plain-English verdict written for the brand owner (see page 1).",
             "done" if s.verdict else "todo", gen_t if s.verdict else "pending"),
            ("Owner alerted", "E-mail with this evidence PDF sent to the brand owner." if flagged
             else "Not flagged, no alert needed.", "done" if flagged else "skip", gen_t if flagged else "skipped"),
            ("Brand confirmation", {"confirmed": "Owner confirmed: 'Yes, it's fake'.",
                                    "reported": "Owner confirmed: 'Yes, it's fake'.",
                                    "not_fake": "Owner marked this site as NOT fake."}.get(
                s.status, "Awaiting the brand owner's decision in the FORGE dashboard."),
             "done" if s.status in ("confirmed", "reported", "not_fake") else "now",
             "done" if s.status in ("confirmed", "reported", "not_fake") else "waiting"),
            ("Abuse report", "Abuse report with evidence sent to the registrar." if s.status == "reported"
             else "Will be sent only after the owner confirms.", "done" if s.status == "reported" else "todo",
             "sent" if s.status == "reported" else "pending"),
        ]
        sh = 33
        hh = 34 + len(steps) * sh
        y0 = self.y
        yy = self.card(M, y0, CW, hh, "timeline")
        lx = M + P + 8
        c.setStrokeColor(BORDER)
        c.setLineWidth(1.2)
        c.line(lx, yy - 6, lx, yy - 6 - (len(steps) - 1) * sh)
        for i, (title, desc, state, when) in enumerate(steps):
            cy = yy - 6 - i * sh
            if state == "done":
                c.setFillColor(ORANGE)
                c.circle(lx, cy, 5, stroke=0, fill=1)
            else:
                c.setFillColor(BONE)
                c.setStrokeColor(ORANGE if state == "now" else MUTED)
                c.setLineWidth(1.3)
                c.circle(lx, cy, 4.6, stroke=1, fill=1)
            self.text(lx + 16, cy - 1, title, 10, SANS_M, INK if state != "todo" else MUTED)
            self.text(lx + 16, cy - 13, self.fit(desc, CW - 2 * P - 160, 7.8, SANS), 7.8, SANS, MUTED)
            self.text(M + CW - P, cy - 1, when, 7.2, MONO_M if state == "now" else MONO,
                      ORANGE if state == "now" else (INK if state == "done" else MUTED), right=True)
        self.y = y0 - hh - G

        # 15 report status — black card
        hh = 86
        y0 = self.y
        yy = self.card(M, y0, CW, hh, "report status", "ink")
        sttxt = {"reported": "report sent", "confirmed": "confirmed · ready to report",
                 "not_fake": "closed · not fake"}.get(s.status, "awaiting brand confirmation")
        title = {"reported": "Report sent.", "confirmed": "Confirmed fake.", "not_fake": "Closed."}.get(
            s.status, "Awaiting brand confirmation.")
        self.pill(M + CW - P, y0 - P - 10, sttxt, right=True)
        self.text(M + P, yy - 22, title, 24, SERIF_I, BONE)
        msg = (f"Abuse report sent{(' to ' + s.abuse_email) if s.abuse_email else ''}." if s.status == "reported"
               else "Nothing is reported until the brand owner presses \"Yes, it's fake\" in the FORGE dashboard.")
        self.text(M + P, yy - 42, self.fit(msg, CW - 2 * P, 8.4, SANS), 8.4, SANS, BORDER)
        self.y = y0 - hh - G

        # 16 next steps — numbered list, stretches down to the signature card
        y0 = self.y
        nb = BOTTOM + SIG_H + G          # bottom of the next-steps card
        yy = self.card(M, y0, CW, y0 - nb, "next steps for the brand owner")
        tips = [("Review", "Compare the screenshots and image pairs on page 2 with your own store."),
                ("Confirm", "Press \"Yes, it's fake\" or \"Not fake\" in your FORGE dashboard."),
                ("Report", "FORGE then e-mails this PDF to the registrar's abuse contact shown above."),
                ("Warn customers", "Post your official domain and payment details on your social channels."),
                ("Watch", "Run FORGE with --watch to re-hunt every 6 hours for new copies.")]
        step = min(40, (yy - nb - 8) / len(tips))
        for i, (t, d) in enumerate(tips):
            ty = yy - i * step
            if i:
                self.rule(M + P, M + CW - P, ty + 4)
            self.text(M + P, ty - step / 2 - 4, f"{i + 1:02d}", 13, SERIF, ORANGE)
            self.text(M + P + 30, ty - step / 2 - 3, t, 9.6, SANS_M, INK)
            self.text(M + P + 130, ty - step / 2 - 3, d, 8.6, SANS, MUTED)
        # 17 signature — pyHanko draws the visible signature stamp into SIG_BOX after the PDF is generated
        y0 = BOTTOM + SIG_H
        self.card(M, y0, CW, SIG_H, "signature")
        self.text(M + CW - P, y0 - P - 6, "pyHanko · PAdES · certified, no changes allowed", 6.8, MONO, MUTED,
                  right=True)
        self.finish_page()


def _render(fileobj, s, dna, total, assets, contacts, gmap=None) -> int:
    c = rl_canvas.Canvas(fileobj, pagesize=A4)
    c.setTitle(f"FORGE evidence report - {s.domain}")
    c.setAuthor("FORGE")
    d = Doc(c, s, dna, total, contacts)
    d.page_case_file()
    d.page_evidence(assets)
    d.page_cloaking()
    d.page_campaign(gmap)
    d.page_action()
    pages = c.getPageNumber() - 1
    c.save()
    return pages


def build_evidence_pdf(s: Suspect, dna: BrandDNA, peers: list | None = None) -> str:
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
    try:
        from prarthana.campaign_graph import suspect_map
        gmap = suspect_map(s, peers, dna)
    except Exception:
        gmap = {}
    total = _render(io.BytesIO(), s, dna, 5, assets, contacts, gmap)  # pass 1: count pages
    _render(str(path), s, dna, total, assets, contacts, gmap)
    s.pdf_path = str(path)
    return str(path)
