"""Generate the small labelled evaluation set (fictional data): tests/eval/sites/<name>/index.html,
tests/eval/shared/*.png and tests/eval/labels.json.

    python tests/eval/make_eval.py      # outputs are committed; only needed to regenerate

Every site is served by tests/eval/server.py on http://<name>.localhost:<port>/ so each one has its own domain.
"""
import json
import random
import shutil
from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
SITES, SHARED = HERE / "sites", HERE / "shared"
STORE_IMG = HERE.parent / "fixtures" / "store" / "img"
BRAND = "Mitti &amp; Dhaaga"
PRODUCTS = [("Indigo Block Print Kurta", 2499), ("Terracotta Clay Earrings", 899), ("Handloom Cotton Saree", 4999)]
OFFICIAL = "hello@mittidhaaga.example, +91 98765 43210"


def own_photo(seed, size=400):
    rnd = random.Random(seed)
    im = Image.new("RGB", (size, size), tuple(rnd.randint(0, 255) for _ in range(3)))
    d = ImageDraw.Draw(im)
    for _ in range(18):
        x, y = rnd.randint(0, size - 20), rnd.randint(0, size - 20)
        d.polygon([(x, y), (x + rnd.randint(20, 160), y + rnd.randint(-60, 60)),
                   (x + rnd.randint(-60, 60), y + rnd.randint(20, 160))], fill=tuple(rnd.randint(0, 255) for _ in range(3)))
    return im


def own_logo(seed, text_color=(30, 30, 30)):
    rnd = random.Random(seed)
    im = Image.new("RGB", (200, 80), tuple(rnd.randint(150, 255) for _ in range(3)))
    d = ImageDraw.Draw(im)
    d.rectangle([10, 15, 60, 65], fill=text_color)
    d.ellipse([80, 20, 190, 60], outline=text_color, width=6)
    return im


def shared_images():
    shutil.rmtree(SHARED, ignore_errors=True)
    SHARED.mkdir(parents=True)
    for f in ("p1.png", "p2.png", "p3.png", "logo.png", "favicon.png"):
        shutil.copy(STORE_IMG / f, SHARED / f)                     # the brand's own images
    for i in (1, 2, 3):                                             # re-encoded / resized look-alikes
        Image.open(STORE_IMG / f"p{i}.png").convert("RGB").resize((330, 330)).save(SHARED / f"look{i}.jpg", quality=72)
    for i in range(1, 7):                                           # other shops' own photos
        own_photo(100 + i).save(SHARED / f"own{i}.png")
    for i in range(1, 4):
        own_logo(200 + i).save(SHARED / f"ownlogo{i}.png")
    qr = own_photo(999, 220).convert("L").point(lambda v: 0 if v < 128 else 255).convert("RGB")
    qr.save(SHARED / "qr.png")                                      # the old fake's own payment QR banner


def page(title, logo, photos, prices, contacts, extra="", products=PRODUCTS, body=""):
    cards = "\n".join(
        f'<div class="card"><img src="/shared/{img}" alt="{n}"><h3>{n}</h3>'
        + (f'<p class="price">₹{p:,}</p>' if p else "") + "</div>"
        for (n, _), img, p in zip(products, photos, prices))
    logo_html = f'<header><img src="/shared/{logo}" alt="logo"><h1>{title}</h1></header>' if logo else f"<h1>{title}</h1>"
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>{title}</title></head><body>
{logo_html}
{extra}
{body}
<main>{cards}</main>
<footer><p>{('Contact: ' + contacts) if contacts else ''}</p></footer></body></html>"""


def cut(pct):
    return [round(p * (1 - pct)) for _, p in PRODUCTS]


FULL = [p for _, p in PRODUCTS]
BRAND_PHOTOS = ["p1.png", "p2.png", "p3.png"]
LOOK = ["look1.jpg", "look2.jpg", "look3.jpg"]
OWN = ["own1.png", "own2.png", "own3.png"]
OWN2 = ["own4.png", "own5.png", "own6.png"]
OLD_FAKE_CONTACTS = "UPI dealsmitti@ybl, WhatsApp 90000 11122"

SITES_DEF = {
    # history: a fake the owner confirmed earlier (not part of the scored set)
    "old-fake": dict(label="history", html=page(f"{BRAND} Outlet", "ownlogo1.png", BRAND_PHOTOS, cut(0.7),
                                                OLD_FAKE_CONTACTS, extra='<img src="/shared/qr.png" alt="pay">'),
                     desc="Earlier fake the owner already confirmed (history for the relaunch case)."),
    # ---------------- fakes
    "fake-full": dict(label="fake", html=page(f"{BRAND} - Official Sale", "logo.png", BRAND_PHOTOS, cut(0.7),
                                              "WhatsApp 91234 56780, pay to mittisale@okaxis"),
                      desc="Classic clone: brand photos + logo, 70% off, its own UPI / WhatsApp."),
    "fake-no-discount": dict(label="fake", html=page(f"{BRAND} India", "logo.png", BRAND_PHOTOS, FULL,
                                                     "orders.mitti@gmail.example, 93333 44455"),
                             desc="Clone at normal prices, other contacts."),
    "fake-lookalike": dict(label="fake", html=page(f"{BRAND} Store", "logo.png", LOOK, cut(0.6),
                                                   "pay mittistore@paytm"),
                           desc="Re-encoded / resized brand photos (look-alikes), logo, 60% off."),
    "fake-no-logo": dict(label="fake", html=page("Ethnic Deals", "ownlogo2.png", BRAND_PHOTOS, cut(0.5),
                                                 "ethnicdeals@ybl"),
                         desc="Brand photos under another name and logo, 50% off."),
    "fake-cloaking": dict(label="fake", html=page(f"{BRAND} - Official Sale", "logo.png", BRAND_PHOTOS, cut(0.7),
                                                  "WhatsApp 98888 77766, pay to mittiflash@okhdfc"),
                          desc="Full clone that shows bots a maintenance page (cloaking).", cloaking=True),
    "fake-relaunch": dict(label="fake", html=page(f"{BRAND} Outlet 2", "ownlogo1.png", BRAND_PHOTOS, cut(0.5),
                                                  OLD_FAKE_CONTACTS, extra='<img src="/shared/qr.png" alt="pay">'),
                          desc="Relaunch of old-fake: same UPI / WhatsApp and payment QR, brand photos, 50% off."),
    "fake-young": dict(label="fake", html=page(f"{BRAND} New Collection", "logo.png", BRAND_PHOTOS, [0, 0, 0], ""),
                       desc="Brand photos + logo, no prices / contacts shown yet; domain 5 days old (injected).",
                       domain_age_days=5),
    "fake-subtle": dict(label="fake", html=page("Handloom Hub", "ownlogo3.png", LOOK, FULL, "handloomhub@ybl"),
                        desc="Only look-alike photos + its own UPI, normal prices (hard case)."),
    "fake-text-only": dict(label="fake", html=page("Mitti Kurtas", "", OWN, cut(0.7), "mittikurtas@ybl"),
                           desc="Own photos but the brand's product names at 70% off + UPI (hard case)."),
    "fake-logo-cheap": dict(label="fake", html=page(f"{BRAND} Clearance", "logo.png", OWN2, cut(0.7),
                                                    "clearance.mitti@okaxis"),
                            desc="Brand logo, own photos, 70% off, own UPI."),
    # ---------------- legitimate
    "store": dict(label="legit", html=page(BRAND, "logo.png", BRAND_PHOTOS, FULL, OFFICIAL),
                  desc="The brand's real store."),
    "reseller": dict(label="legit", html=page(f"Crafts Collective - {BRAND} partner", "logo.png", BRAND_PHOTOS, FULL,
                                              OFFICIAL), desc="Approved reseller on the allow-list (brand photos + logo)."),
    "unrelated-shop": dict(label="legit", html=page("Indigo Threads", "ownlogo2.png", OWN, [2299, 799, 4599],
                                                    "hello@indigothreads.example",
                                                    products=[("Indigo Print Kurta", 0), ("Clay Jhumka Earrings", 0),
                                                              ("Cotton Handloom Saree", 0)]),
                           desc="Unrelated shop with similar product names, own photos and prices."),
    "brand-mention": dict(label="legit", html=page("Weekend Reads", "", [], [], "",
                                                   body=f"<p>Five small Indian labels we love, including {BRAND}, "
                                                        "who make block-printed kurtas in Jaipur.</p>"),
                          desc="Blog post that only mentions the brand name."),
    "review-blog": dict(label="legit", html=page("Saree Diaries", "", ["p3.png"], [0], "write@sareediaries.example",
                                                 products=[("Handloom Cotton Saree", 0)],
                                                 body=f"<p>My review of the {BRAND} handloom saree (photo: {BRAND}).</p>"),
                        desc="Review blog using one brand photo with credit."),
    "discount-shop": dict(label="legit", html=page("Bazaar Bargains", "ownlogo3.png", OWN2, [999, 349, 1899],
                                                   "help@bazaarbargains.example"),
                          desc="Discount shop selling its own products under the same generic names, cheaply."),
    "link-page": dict(label="legit", html=page(f"{BRAND} links", "logo.png", [], [], OFFICIAL,
                                               body='<p><a href="#">Shop</a> · <a href="#">Instagram</a></p>'),
                      desc="The brand's own link-in-bio page (logo + official contacts)."),
    "influencer": dict(label="legit", html=page("Style with Ananya", "logo.png", BRAND_PHOTOS, [0, 0, 0],
                                                "collabs@ananya.example",
                                                body=f"<p>My favourite pieces from {BRAND}, shop them on their site.</p>"),
                       desc="Influencer post with the brand's photos + logo and her own e-mail (hard case)."),
    "news-article": dict(label="legit", html=page("Jaipur Times", "logo.png", [], [], "",
                                                  body=f"<p>Local label {BRAND} opens its first store.</p>"),
                         desc="News article showing the brand's logo."),
    "crafts-market": dict(label="legit", html=page("Craft Mela Marketplace", "ownlogo1.png", OWN, [1999, 650, 3999],
                                                   "support@craftmela.example",
                                                   products=[("Block Print Cotton Top", 0), ("Terracotta Necklace", 0),
                                                             ("Ikat Saree", 0)]),
                          desc="Marketplace of other artisans, own photos and contacts."),
}


def main():
    shared_images()
    shutil.rmtree(SITES, ignore_errors=True)
    for name, d in SITES_DEF.items():
        (SITES / name).mkdir(parents=True)
        (SITES / name / "index.html").write_text(d["html"], encoding="utf-8")
    labels = {
        "brand_store": "store",
        "allow_list": ["reseller"],
        "history": [{"site": "old-fake", "status": "confirmed"}],
        "pages": [{"site": n, "label": d["label"], "description": d["desc"],
                   **({"domain_age_days": d["domain_age_days"]} if "domain_age_days" in d else {}),
                   **({"cloaking": True} if d.get("cloaking") else {})}
                  for n, d in SITES_DEF.items() if d["label"] != "history"],
    }
    (HERE / "labels.json").write_text(json.dumps(labels, indent=1, ensure_ascii=False))
    print(f"wrote {len(SITES_DEF)} sites, {len(labels['pages'])} labelled pages")


if __name__ == "__main__":
    main()
