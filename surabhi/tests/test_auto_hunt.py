"""Auto-hunt on the Brand protection page: NameNet finds lookalikes, each one is investigated with no user input.

DNS and websites are faked (no internet needed): the lookalike domains serve the demo clone sites' pages and
images from surabhi/demo_sites, the brand-owned lookalike redirects to the official site."""
from pathlib import Path

from surabhi import brand_dna, protection, store

DEMO = Path(__file__).resolve().parent.parent / 'demo_sites'
ASSETS = DEMO / 'assets'
OFFICIAL = 'northstar.com'
IPS = {OFFICIAL: '203.0.113.1', 'northstar-sale.com': '198.51.100.7', 'northstarr.com': '198.51.100.8',
       'northstar.shop': '198.51.100.9'}


def resolver(domain):
    ip = IPS.get(domain)
    return {'status': 'resolves', 'addresses': [ip], 'error': ''} if ip else \
        {'status': 'no_such_domain', 'addresses': [], 'error': ''}


def img(name):
    return {'name': name, 'source': f'https://x/{name}', 'role': 'logo' if 'logo' in name else 'image',
            'data': (ASSETS / name).read_bytes()}


SITES = {  # what each lookalike domain serves
    'northstar-sale.com': {'ok': True, 'final_url': 'https://northstar-sale.com/', 'title': 'Northstar sale',
                           'images': [img('logo.png'), img('jacket.png'), img('flask.png')]},
    'northstarr.com': {'ok': True, 'final_url': 'https://northstarr.com/', 'title': 'Parked domain', 'images': []},
    'northstar.shop': {'ok': True, 'final_url': 'https://www.northstar.com/', 'title': 'Northstar',
                       'images': [img('logo.png'), img('jacket.png')]},
}


def fetcher(url, max_images=40):
    domain = url.split('://')[-1].split('/')[0]
    site = dict(SITES.get(domain, {'ok': False, 'final_url': url, 'images': [], 'error': 'unreachable'}))
    return {'url': url, 'status_code': 200, 'error': '', 'image_errors': [], 'title': '', **site}


def test_auto_hunt_finds_investigates_and_skips_brand_owned(tmp_path):
    conn = store.connect(tmp_path / 'brand.db')
    out = brand_dna.register_brand('Northstar', f'https://{OFFICIAL}', logo=('logo.png', (ASSETS / 'logo.png').read_bytes()),
                                   reference_images=[(n, (ASSETS / n).read_bytes()) for n in ('jacket.png', 'flask.png')],
                                   keep_copies=False, conn=conn)
    steps = []
    hunt = protection.auto_hunt(out['brand']['id'], on_step=steps.append, conn=conn, resolver=resolver,
                                age_lookup=lambda d: 5, fetcher=fetcher)

    by_domain = {r['domain']: r for r in hunt['reports']}
    assert hunt['scan']['dns'] == 'ok'
    # the clone that copies the logo and product photos is high risk, with the reasons
    sale = by_domain['northstar-sale.com']['risk']
    assert sale['level'] == 'high' and sale['label'] == 'likely_fake'
    assert any('logo' in r for r in sale['reasons'])
    # a parked lookalike with no copied images is never high risk on its name alone
    assert by_domain['northstarr.com']['risk']['level'] != 'high'
    # the brand's own defensive domain (redirects to the official site) is left out, not reported
    assert 'northstar.shop' not in by_domain
    assert any(s['domain'] == 'northstar.shop' and 'official' in s['why'] for s in hunt['skipped'])
    assert any('Investigating' in s for s in steps)
    # reports convert into dashboard cases
    assert protection.to_suspect(by_domain['northstar-sale.com']).label == 'likely_fake'


def test_redirects_to_official():
    assert protection.redirects_to_official({'ok': True, 'final_url': 'https://www.northstar.com/x'}, 'northstar.com')
    assert protection.redirects_to_official({'ok': True, 'final_url': 'https://shop.northstar.com/'}, 'www.northstar.com')
    assert not protection.redirects_to_official({'ok': True, 'final_url': 'https://northstar-sale.com/'}, 'northstar.com')
    assert not protection.redirects_to_official({'ok': False, 'final_url': 'https://northstar.com/'}, 'northstar.com')
    assert not protection.redirects_to_official(None, 'northstar.com')


def test_images_from_website():
    logo, refs, _ = protection.images_from_website('https://northstar-sale.com', fetcher=fetcher)
    assert logo[0] == 'logo.png' and [n for n, _ in refs] == ['jacket.png', 'flask.png']
