"""Generate the tiny test stores: tests/fixtures/store (real) and tests/fixtures/clone (fake).

Run: python tests/fixtures/make_fixtures.py  (outputs are committed, so this is only needed to regenerate)
All data is fictional. Contacts are fake placeholder numbers/IDs.
"""
import random
import shutil
from pathlib import Path
from PIL import Image, ImageDraw

HERE = Path(__file__).parent
STORE, CLONE = HERE / "store", HERE / "clone"
PRODUCTS = [("Indigo Block Print Kurta", 2499), ("Terracotta Clay Earrings", 899), ("Handloom Cotton Saree", 4999)]


def product_photo(seed: int) -> Image.Image:
    rnd = random.Random(seed)
    im = Image.new("RGB", (400, 400), tuple(rnd.randint(0, 255) for _ in range(3)))
    d = ImageDraw.Draw(im)
    for _ in range(25):
        x, y = rnd.randint(0, 380), rnd.randint(0, 380)
        w, h = rnd.randint(20, 200), rnd.randint(20, 200)
        col = tuple(rnd.randint(0, 255) for _ in range(3))
        (d.ellipse if rnd.random() < .5 else d.rectangle)([x, y, x + w, y + h], fill=col)
    return im


def logo() -> Image.Image:
    im = Image.new("RGB", (200, 80), (250, 240, 220))
    d = ImageDraw.Draw(im)
    d.ellipse([10, 10, 70, 70], fill=(180, 60, 30))
    d.rectangle([85, 30, 190, 50], fill=(40, 40, 120))
    return im


def page(title, products, contacts, extra=""):
    cards = "\n".join(
        f'<div class="card"><img src="img/p{i}.{ext}" alt="{n}"><h3>{n}</h3><p class="price">₹{p:,}</p></div>'
        for i, (n, p, ext) in enumerate(products, 1))
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>{title}</title>
<link rel="icon" href="img/favicon.png"></head><body>
<header><img src="img/logo.png" alt="logo"><h1>{title}</h1></header>
{extra}
<main>{cards}</main>
<footer><p>Contact: {contacts}</p></footer></body></html>"""


def main():
    for d in (STORE, CLONE):
        shutil.rmtree(d, ignore_errors=True)
        (d / "img").mkdir(parents=True)
    photos = [product_photo(i) for i in range(1, 4)]
    for d in (STORE, CLONE):
        logo().save(d / "img" / "logo.png")
        logo().resize((32, 32)).save(d / "img" / "favicon.png")
    for i, ph in enumerate(photos, 1):
        ph.save(STORE / "img" / f"p{i}.png")
    # clone: two byte-identical copies + one re-encoded/resized look-alike (tests pHash path)
    shutil.copy(STORE / "img" / "p1.png", CLONE / "img" / "p1.png")
    shutil.copy(STORE / "img" / "p2.png", CLONE / "img" / "p2.png")
    photos[2].resize((360, 360)).save(CLONE / "img" / "p3.jpg", quality=80)

    (STORE / "index.html").write_text(page(
        "Mitti &amp; Dhaaga",
        [(n, p, "png") for n, p in PRODUCTS],
        "hello@mittidhaaga.example, +91 98765 43210"), encoding="utf-8")
    (CLONE / "index.html").write_text(page(
        "Mitti &amp; Dhaaga - Official Sale",
        [(n, round(p * 0.3), "png" if i < 3 else "jpg") for i, (n, p) in enumerate(PRODUCTS, 1)],
        "WhatsApp 91234 56780, pay to mittisale@okaxis",
        extra="<p><b>MEGA SALE 70% OFF - UPI only, no COD. Offer ends in 10 minutes!</b></p>"), encoding="utf-8")
    print("fixtures written")


if __name__ == "__main__":
    main()
