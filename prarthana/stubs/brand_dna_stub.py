"""STUB for teammate topic #2 (Brand DNA). Same signature as Surabhi's: build_brand_dna(url) -> BrandDNA.

Minimal real version so the flow runs end to end: loads the store in headless Chromium, hashes every image
(logo / favicon / product photos), reads product names + prices and hashes the official contacts.
"""
from __future__ import annotations

from schemas import BrandDNA, file_sha256, photo_phash
from prarthana.common import domain_of, launch_chromium
from prarthana.investigator import PRICE_RE, TIMEOUT_MS, UA, hashed_contacts

JS_PRODUCTS = """() => {
  const out = [];
  for (const h of document.querySelectorAll('h2,h3,h4,.product-title,.product-name,[itemprop=name]')) {
    let card = h.parentElement;
    for (let i = 0; i < 3 && card && !/(₹|Rs\\.?|INR)\\s*[0-9]/i.test(card.innerText); i++) card = card.parentElement;
    if (card) out.push([h.innerText.trim(), card.innerText]);
  }
  return out;
}"""


def build_brand_dna(url: str) -> BrandDNA:
    from playwright.sync_api import sync_playwright

    images = {}
    with sync_playwright() as p:
        browser = launch_chromium(p)
        try:
            ctx = browser.new_context(user_agent=UA)
            page = ctx.new_page()

            def on_response(resp):
                try:
                    if resp.request.resource_type == "image" and resp.ok:
                        images[resp.url] = resp.body()
                except Exception:
                    pass
            page.on("response", on_response)
            page.goto(url, timeout=TIMEOUT_MS, wait_until="load")
            title = page.title()
            site_name = page.eval_on_selector_all("meta[property='og:site_name']", "e => e.map(x => x.content)")
            logo_srcs = page.eval_on_selector_all(
                "img[src*=logo i], img[alt*=logo i], img[class*=logo i], header img",
                "els => els.map(e => e.currentSrc || e.src)")
            fav = page.eval_on_selector_all("link[rel*='icon']", "els => els.map(e => e.href)")
            for src in page.eval_on_selector_all("img", "els => els.map(e => e.currentSrc || e.src)") + fav[:1]:
                if src and src not in images and not src.startswith("data:"):
                    try:
                        r = ctx.request.get(src)
                        if r.ok:
                            images[src] = r.body()
                    except Exception:
                        pass
            products = page.evaluate(JS_PRODUCTS)
            text = page.inner_text("body")
        finally:
            browser.close()

    dna = BrandDNA(store_url=url, allow_list=[domain_of(url)])
    dna.brand_name = (site_name[0] if site_name else title.split("|")[0].split(" - ")[0]).strip()
    logo_url = logo_srcs[0] if logo_srcs else None
    fav_url = fav[0] if fav else None
    for u, b in images.items():
        try:
            ph = photo_phash(b)
        except Exception:
            continue
        if u == logo_url:
            dna.logo_sha256, dna.logo_phash = file_sha256(b), ph
        elif u == fav_url:
            dna.favicon_sha256, dna.favicon_phash = file_sha256(b), ph
        else:
            dna.photo_sha256s.append(file_sha256(b))
            dna.photo_phashes.append(ph)
    for name, card in products:
        m = PRICE_RE.search(card)
        if name and m and name not in dna.prices:
            dna.product_names.append(name)
            dna.prices[name] = float(m.group(1).replace(",", ""))
    dna.official_contact_hashes, _ = hashed_contacts(text)
    return dna
