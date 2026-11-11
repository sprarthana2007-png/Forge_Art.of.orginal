"""Collect visual evidence and write it into the team's shared ForgeState file (see schemas.py).

For the official store and every suspect, this:
  1. opens the page in Chromium with Playwright and saves a screenshot;
  2. fingerprints every image with the shared helpers in schemas.py:
       file_sha256  -> exact copies          photo_phash + is_similar -> near-duplicates
     (a mirrored copy is also checked, since flipping an image defeats a plain perceptual hash);
  3. records the domain age, page title and text, scam phrases and contacts
     (contacts are stored only as hash_contact / mask_contact values);
  4. writes BrandDNA and Suspect records with state_to_dict().

Two ways to run it:
    python analyze.py                          # demo: build forge_state.json from demo_sites/sites.json
    python analyze.py --state forge_state.json # enrich a state file the team pipeline already wrote

When enriching, score, label, reasons, verdict, status and found_by are left exactly as they are.

Setup:  python -m pip install -r requirements-analysis.txt && python -m playwright install chromium
"""
import argparse
import io
import json
import re
import urllib.request
from datetime import date
from pathlib import Path
from urllib.parse import quote, urlparse

from PIL import Image, ImageOps
from playwright.sync_api import sync_playwright

from phrases import find_phrases
from schemas import (BrandDNA, ForgeState, Suspect, file_sha256, hash_contact, is_similar, mask_contact, photo_phash,
                     state_from_dict, state_to_dict)

ROOT = Path(__file__).resolve().parent
THUMB = 400  # px; images are rasterised to this size before perceptual hashing
BROWSER_BAR = 76
CONTACT_RE = re.compile(r'([\w.+-]+@[\w-]+(?:\.[\w-]+)*|\+?\d[\d\s-]{8,}\d)')


def resolve(url, base):
    """Turn a sites-file URL into something the browser can open."""
    return url if re.match(r'^(https?|file)://', url) else (base / url).resolve().as_uri()


def domain_of(url):
    return (urlparse(url).hostname or '').removeprefix('www.')


def slug(url):
    return re.sub(r'[^a-z0-9]+', '_', Path(urlparse(url).path).stem.lower()).strip('_') or 'image'


def screenshot(page, url, shown_url, out):
    """Screenshot `url` inside a browser frame that shows `shown_url` in the address bar. Returns (title, text)."""
    frame = (ROOT / 'demo_sites' / 'frame.html').as_uri()
    page.goto(f'{frame}?src={quote(url, safe="")}&url={quote(shown_url, safe="")}')
    page.wait_for_load_state('networkidle')
    inner = page.frames[1]
    title = inner.title()
    page.evaluate('t => { document.getElementById("t").textContent = t; }', title)
    page.screenshot(path=str(out))
    text = inner.evaluate("""() => { const body = document.body.cloneNode(true);
        body.querySelectorAll('.demo-ribbon, script, style').forEach(e => e.remove());
        return body.innerText; }""")
    return title, ' '.join(text.split())[:2000]


def fingerprint_images(context, url):
    """{image_url: {sha256, phash, phash_mirrored, png, is_logo}} for every <img> on the page."""
    page = context.new_page()
    page.goto(url)
    page.wait_for_load_state('networkidle')
    images = page.eval_on_selector_all('img', '''els => els.map(e => ({src: e.currentSrc,
        logo: /logo/i.test(e.currentSrc + ' ' + e.alt + ' ' + e.className)})).filter(i => i.src)''')
    found = {}
    for image in images:
        src = image['src']
        if src in found:
            continue
        body = context.request.get(src).body() if src.startswith('http') else Path(urlparse(src).path).read_bytes()
        # Rasterise in the browser so SVG, WebP, etc. can all be perceptually hashed.
        page.set_content(f'<body style="margin:0"><img src="{src}" width="{THUMB}" height="{THUMB}" '
                         'style="display:block;object-fit:contain"></body>')
        page.wait_for_load_state('networkidle')
        png = page.locator('img').screenshot()
        mirrored = io.BytesIO()
        ImageOps.mirror(Image.open(io.BytesIO(png)).convert('RGB')).save(mirrored, 'PNG')
        found[src] = {'sha256': file_sha256(body), 'phash': photo_phash(png),
                      'phash_mirrored': photo_phash(mirrored.getvalue()), 'png': png, 'is_logo': image['logo']}
    page.close()
    return found


def match(official, suspect):
    """[(official_url, suspect_url, kind)] for every suspect image that copies an official one."""
    matches = []
    for s_url, s in suspect.items():
        exact = next((o_url for o_url, o in official.items() if o['sha256'] == s['sha256']), None)
        if exact:
            matches.append((exact, s_url, 'exact copy · SHA-256 match'))
            continue
        for o_url, o in official.items():
            if is_similar(s['phash'], o['phash']):
                matches.append((o_url, s_url, 'near-duplicate · perceptual hash'))
                break
            if is_similar(s['phash_mirrored'], o['phash']):
                matches.append((o_url, s_url, 'near-duplicate · perceptual hash, mirrored'))
                break
    return matches


def contacts(text):
    found = []
    for raw in CONTACT_RE.findall(text or ''):
        if raw not in found:
            found.append(raw)
    return found


def domain_age_days(domain, created=None):
    """Days since registration: from a YYYY-MM-DD `created` date, else an RDAP lookup, else None."""
    try:
        if not created:
            with urllib.request.urlopen(f'https://rdap.org/domain/{domain}', timeout=15) as response:
                events = json.load(response).get('events', [])
            created = next(e['eventDate'][:10] for e in events if e.get('eventAction') == 'registration')
        return (date.today() - date.fromisoformat(created)).days
    except Exception:  # no network, unknown TLD, no registration event
        return None


def demo_state(sites_file):
    """A fresh ForgeState from a sites file (stands in for the team's discovery and scoring steps)."""
    sites = json.loads(sites_file.read_text(encoding='utf-8'))
    official = sites['official']
    state = ForgeState(brand_dna=BrandDNA(store_url=f"https://{official['domain']}",
                                          brand_name=official.get('brand_name', ''), verified_owner=True))
    for site in sites['suspects']:
        state.suspects.append(Suspect(
            url=site.get('display_url', f"https://{site['domain']}"), domain=site['domain'],
            found_by=site.get('found_by', 'manual'),
            score=site['score'], label=site['label'], reasons=site['reasons'], status='investigated'))
        state.seen_domains.add(site['domain'])
    return state, sites


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--sites', type=Path, default=ROOT / 'demo_sites' / 'sites.json',
                        help='demo list of the official site and suspects (used when --state is not given)')
    parser.add_argument('--state', type=Path, help='existing ForgeState JSON to enrich in place')
    parser.add_argument('--out', type=Path, default=ROOT / 'forge_state.json', help='where to write a new demo state')
    args = parser.parse_args()

    if args.state:
        out = args.state
        state = state_from_dict(json.loads(out.read_text(encoding='utf-8')))
        if not state.brand_dna or not state.brand_dna.store_url:
            raise SystemExit('The state file has no brand_dna.store_url to compare against.')
        extra = {}
    else:
        out = args.out
        state, sites = demo_state(args.sites)
        # The demo sites are local files; record the public-looking URL but load the local copy.
        local = lambda site: resolve(site['url'], args.sites.parent)
        extra = {'official': {**sites['official'], 'load': local(sites['official'])},
                 **{s['domain']: {**s, 'load': local(s)} for s in sites['suspects']}}

    evidence = out.parent / 'evidence'
    (evidence / 'matches').mkdir(parents=True, exist_ok=True)
    rel = lambda path: path.relative_to(out.parent).as_posix()
    brand = state.brand_dna

    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(viewport={'width': 1280, 'height': 1000})
        page = context.new_page()

        official_info = extra.get('official', {})
        official_domain = official_info.get('domain') or domain_of(brand.store_url)
        shot = evidence / 'official.png'
        official_load = official_info.get('load', brand.store_url)
        title, text = screenshot(page, official_load, brand.store_url, shot)
        official_images = fingerprint_images(context, official_load)
        logos = [i for i in official_images.values() if i['is_logo']]
        brand.screenshot_path = rel(shot)
        brand.brand_name = brand.brand_name or title.split('—')[0].strip()
        brand.logo_sha256 = brand.logo_sha256 or (logos[0]['sha256'] if logos else '')
        brand.logo_phash = brand.logo_phash or (logos[0]['phash'] if logos else '')
        brand.photo_sha256s = sorted({*brand.photo_sha256s, *(i['sha256'] for i in official_images.values() if not i['is_logo'])})
        brand.photo_phashes = sorted({*brand.photo_phashes, *(i['phash'] for i in official_images.values() if not i['is_logo'])})
        brand.official_contact_hashes = sorted({*brand.official_contact_hashes, *map(hash_contact, contacts(text))})
        brand.domain_age_days = domain_age_days(official_domain, official_info.get('created'))
        print(f'official  {official_domain}: {len(official_images)} images, {brand.domain_age_days} days old')

        for number, suspect in enumerate(state.suspects, 1):
            if suspect.label == 'ignored':
                continue
            suspect.domain = suspect.domain or domain_of(suspect.url)
            info = extra.get(suspect.domain, {})
            shot = evidence / f'suspect_{re.sub(r"[^a-z0-9]+", "_", suspect.domain.lower())}.png'
            load = info.get('load', suspect.url)
            suspect.page_title, suspect.page_text = screenshot(page, load, suspect.url, shot)
            suspect.screenshot_path = rel(shot)
            images = fingerprint_images(context, load)
            suspect.image_sha256s = sorted({i['sha256'] for i in images.values()})
            pairs, exact, similar = [], set(), set()
            for o_url, s_url, kind in match(official_images, images):
                o_png = evidence / 'matches' / f'official_{slug(o_url)}.png'
                s_png = evidence / 'matches' / f'{slug(suspect.domain)}_{slug(s_url)}.png'
                o_png.write_bytes(official_images[o_url]['png'])
                s_png.write_bytes(images[s_url]['png'])
                pairs.append([rel(o_png), rel(s_png), kind])
                (exact if kind.startswith('exact') else similar).add(
                    official_images[o_url]['sha256'] if kind.startswith('exact') else official_images[o_url]['phash'])
                suspect.logo_match = suspect.logo_match or official_images[o_url]['is_logo']
            suspect.matched_image_pairs = pairs
            suspect.image_exact_matches = sorted(exact)
            suspect.image_similar_matches = sorted(similar)
            found = contacts(suspect.page_text)
            suspect.contact_hashes = sorted({*suspect.contact_hashes, *map(hash_contact, found)})
            suspect.contacts_masked = sorted({*suspect.contacts_masked, *map(mask_contact, found)})
            suspect.scam_phrases = list(dict.fromkeys([*suspect.scam_phrases,
                                                        *(m for _, _, m, _ in find_phrases(suspect.page_text))]))
            if suspect.domain_age_days is None:
                suspect.domain_age_days = domain_age_days(suspect.domain, info.get('created'))
            print(f'suspect   {suspect.domain}: {len(images)} images, {len(exact)} exact, {len(similar)} similar, '
                  f'logo {"copied" if suspect.logo_match else "not copied"}, {suspect.domain_age_days} days old')
        browser.close()

    state.log.append(f'{date.today().isoformat()} analyze.py: screenshots, image hashes, phrases and contacts collected')
    tmp = out.with_suffix('.tmp')
    tmp.write_text(json.dumps(state_to_dict(state), indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    tmp.replace(out)  # atomic, so the dashboard never reads a half-written file
    print(f'wrote     {out}')


if __name__ == '__main__':
    main()
