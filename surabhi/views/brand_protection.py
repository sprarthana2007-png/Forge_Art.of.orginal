"""Brand protection: Brand DNA fingerprints + NameNet lookalike domains + combined investigations.

The backend lives in the repository's `surabhi/` package (also used by the FORGE pipeline); this page only
calls it. Data is stored in the same SQLite file as the rest of the dashboard.
"""
import sqlite3
import sys
from html import escape
from pathlib import Path

import streamlit as st

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.append(str(REPO))

import actions  # noqa: E402
import storage  # noqa: E402
import ui  # noqa: E402
from surabhi import brand_dna, engine_view, name_net, protection, store  # noqa: E402

IMAGE_TYPES = ['png', 'jpg', 'jpeg', 'webp', 'gif', 'bmp', 'svg']
CLASS_TEXT = {'official': 'Official', 'unrelated': 'Unrelated', 'lookalike': 'Lookalike name',
              'suspicious': 'Suspicious (live + recent)', 'not_registered': 'Not registered',
              'likely_brand_owned': 'Likely brand-owned', 'invalid': 'Invalid'}
LEVEL_TEXT = {'high': 'High risk', 'medium': 'Medium risk', 'low': 'Low risk', 'none': 'No match',
              'official': 'Official'}

ui.shell('Brand protection', lede='Brand DNA fingerprints of your logo and product images, lookalike domains '
                                  'found by NameNet, and combined investigations of suspicious websites.')
ui.notice()


def table(headers, rows):
    head = ''.join(f'<th>{escape(h)}</th>' for h in headers)
    body = ''.join('<tr>' + ''.join(f'<td>{c}</td>' for c in r) + '</tr>' for r in rows)
    st.markdown(f'<div class="fx-table-wrap"><table class="fx-table"><thead><tr>{head}</tr></thead>'
                f'<tbody>{body}</tbody></table></div>', unsafe_allow_html=True)


def meter(score):
    return (f'<div class="meter"><div class="fx-bar"><i style="width:{int(score)}%"></i></div>'
            f'<b>{int(score)}</b></div>')


def tag(text, solid=False):
    return f'<span class="fx-tag{" solid" if solid else ""}">{escape(text)}</span>'


def uploads(files):
    return [(f.name, f.getvalue()) for f in files or []]


try:
    conn = store.connect()
    stats = store.stats(conn)
    brands = brand_dna.list_brands(conn)
    findings = store.list_findings(conn)
    investigations = store.list_investigations(conn)
except (sqlite3.Error, OSError) as exc:
    st.error(f'Unable to open the Brand DNA database ({store.db_path()}): {exc}')
    st.stop()

# --- What the FORGE engine found (cache/state.json) -------------------------
engine_state = None
if storage.state_source() == 'engine':
    try:
        engine_state = storage.read_state()
    except (OSError, ValueError) as exc:
        ui.note(f'Engine results could not be read: {exc}')
engine = engine_view.engine_summary(engine_state)

# --- Overview -------------------------------------------------------------
total = engine_view.combined_stats(stats, sum(i['risk_level'] == 'high' for i in investigations),
                                   [b['official_domain'] for b in brands], engine)
cols = st.columns(4)
cols[0].metric('Registered brands', total['brands'])
cols[1].metric('Images analysed', total['images_analysed'])
cols[2].metric('Exact duplicates', total['exact_matches'])
cols[3].metric('Visually similar', total['similar_matches'])
cols = st.columns(4)
cols[0].metric('Brand fingerprints', total['fingerprints'])
cols[1].metric('Suspicious domain variations', total['suspicious_domains'])
cols[2].metric('Investigations', total['investigations'])
cols[3].metric('High risk', total['high_risk'])

if engine['has_engine']:
    ui.section('Brand DNA from the FORGE engine')
    dna = engine['brand']
    if dna is not None:
        photos = len(getattr(dna, 'photo_sha256s', None) or [])
        table(('Brand', 'Official website', 'Product photos', 'Logo', 'Favicon', 'Official contacts', 'Fingerprints'),
              [[f'<b>{escape(getattr(dna, "brand_name", "") or "—")}</b>',
                ui.breakable(getattr(dna, 'store_url', '') or '—'), str(photos),
                tag('Yes', solid=True) if getattr(dna, 'logo_sha256', '') else tag('No'),
                tag('Yes', solid=True) if getattr(dna, 'favicon_sha256', '') else tag('No'),
                f"{len(getattr(dna, 'official_contact_hashes', None) or [])} (hashed)",
                f"{engine['stats']['fingerprints']} (SHA-256 + pHash)"]])
    if engine['suspects']:
        table(('Suspect', 'Found by', 'Risk', 'Label', 'Exact copies', 'Look-alikes', 'Cloaking', 'Status'),
              [[ui.breakable(r['domain']), escape(r['found_by'].replace('_', ' ')), meter(r['score']),
                tag(r['label'].replace('_', ' ') or '—', solid=r['label'] == 'likely_fake'),
                str(r['exact']), str(r['similar']), 'Yes' if r['cloaking'] else 'No',
                escape(r['status'] or '—')] for r in engine['suspects']])
    ui.note('These come from the FORGE engine run (cache/state.json). Open the Evidence page to review a case, '
            'mark it Fake, create the signed PDF and send the abuse report.')

# --- Brands ---------------------------------------------------------------
ui.section('Registered brands', 1)
if brands:
    rows = []
    for b in brands:
        logo = next((i for i in b['images'] if i['kind'] == 'logo'), None)
        uri = ui.image_uri(logo['stored_path'], width=96) if logo and logo['stored_path'] else None
        thumb = f'<img src="{uri}" alt="" style="height:2.2rem;border-radius:4px">' if uri else '—'
        fp = b['fingerprint']
        rows.append([thumb, f'<b>{escape(b["name"])}</b>', ui.breakable(b['official_domain']),
                     tag(fp['status'].title(), solid=fp['status'] == 'complete'),
                     str(fp['logos']), str(fp['references']), escape(b['created_at'][:10])])
    table(('Logo', 'Brand', 'Official domain', 'Brand DNA', 'Logos', 'Reference images', 'Registered'), rows)
elif engine['brand'] is not None:
    ui.note('No extra brand registered here. The brand above comes from the FORGE engine; register another '
            'brand below to check it by hand.')
else:
    ui.note('No brand registered yet. Register your brand below to build its Brand DNA.')

with st.expander('Register a brand or add images', expanded=not brands):
    with st.form('register_brand', clear_on_submit=True):
        a, b = st.columns(2)
        name = a.text_input('Brand name *')
        url = b.text_input('Official website *', placeholder='https://yourbrand.com')
        logo = st.file_uploader('Brand logo', type=IMAGE_TYPES)
        refs = st.file_uploader('Reference images (product photos, banners)', type=IMAGE_TYPES,
                                accept_multiple_files=True)
        ui.note('No images? Leave both empty: FORGE downloads the logo and images from the official website. '
                'Registering the same website again adds the new images to that brand. '
                'SVG logos are rendered to an image for the visual match; exact copies match on the SVG file.')
        if st.form_submit_button('Save Brand DNA', type='primary'):
            try:
                logo_in, refs_in = ((logo.name, logo.getvalue()) if logo else None), uploads(refs)
                if logo_in is None and not refs_in and name.strip() and url.strip():
                    with st.spinner('Downloading the logo and images from the official website…'):
                        logo_in, refs_in, site = protection.images_from_website(url.strip())
                    if logo_in is None and not refs_in:
                        st.warning('No images could be downloaded from the website'
                                   + (f" ({site['error']})" if site.get('error') else '')
                                   + '. The brand is saved by name; upload a logo for the image check.')
                out = brand_dna.register_brand(name, url, logo=logo_in, reference_images=refs_in, conn=conn)
            except ValueError as exc:
                st.error(str(exc))
            else:
                for r in out['rejected']:
                    st.warning(f"{r['filename']}: {r['error']}")
                msg = (f"{out['brand']['name']}: {len(out['added'])} image(s) fingerprinted, "
                       f"{len(out['duplicates'])} already registered. Brand DNA {out['fingerprint']['status']}.")
                if out['rejected']:
                    st.info(msg)
                else:
                    st.session_state['save_notice'] = msg
                    st.rerun()

if not brands:
    st.stop()
brand_ids = [b['id'] for b in brands]
brand_names = {b['id']: f"{b['name']} ({b['official_domain']})" for b in brands}

# --- Auto-hunt --------------------------------------------------------------
ui.section('Hunt automatically')
ui.note('One click: NameNet finds the live lookalike domains of the brand, then FORGE investigates every one of '
        'them by itself and puts the risky ones in the case files. Domains that belong to the brand (same servers '
        'or a redirect to the official site) are left out.')
with st.form('auto_hunt'):
    a, b = st.columns([3, 2])
    hunt_brand = a.selectbox('Brand', brand_ids, format_func=brand_names.get, key='hunt_brand')
    hunt_limit = b.number_input('Sites to investigate', min_value=1, max_value=20, value=8)
    if st.form_submit_button('Start the hunt', type='primary'):
        try:
            with st.status('Hunting…', expanded=True) as status:
                hunt = protection.auto_hunt(hunt_brand, limit=int(hunt_limit), on_step=status.write, conn=conn)
                added = []
                brand_row = store.get_brand(conn, hunt_brand)
                for r in hunt['reports']:
                    if r['risk']['label'] != 'ignored':
                        s = protection.to_suspect(r)
                        s.found_by = 'name_net'
                        added.append(actions.add_suspect(s, brand_dna.to_brand_dna(brand_row)))
                status.update(label='Hunt finished', state='complete', expanded=False)
        except (ValueError, OSError) as exc:
            st.error(f'The hunt stopped: {exc}')
        else:
            st.session_state['hunt_result'] = {
                'scan': {k: hunt['scan'].get(k) for k in ('checked', 'dns', 'error')},
                'found': len(hunt['scan']['findings']), 'added': added, 'skipped': hunt['skipped'],
                'rows': [(r['domain'], r['risk']['score'], r['risk']['level'], r['risk']['summary'])
                         for r in hunt['reports']]}
            st.rerun()

hunt_result = st.session_state.get('hunt_result')
if hunt_result:
    sc = hunt_result['scan']
    if sc.get('dns') != 'ok':
        st.warning(f"DNS is unavailable ({sc.get('error')}); lookalike domains cannot be verified right now.")
    else:
        st.success(f"Checked {sc['checked']} possible lookalike names: {hunt_result['found']} exist online, "
                   f"{len(hunt_result['rows'])} investigated, {len(hunt_result['added'])} added to the case files."
                   + (' Open Cases to review them.' if hunt_result['added'] else ''))
    if hunt_result['rows']:
        table(('Domain', 'Risk', 'Level', 'Summary'),
              [[ui.breakable(d), meter(score), tag(LEVEL_TEXT.get(level, level), solid=level == 'high'), escape(summary)]
               for d, score, level, summary in hunt_result['rows']])
    if hunt_result['skipped']:
        ui.note('Left out: ' + '; '.join(f"{s['domain']} ({s['why']})" for s in hunt_result['skipped'][:12]))

# --- Investigate ------------------------------------------------------------
ui.section('Investigate a suspicious domain or website', 2)
ui.note('NameNet checks the name; Brand DNA compares the site\'s logo, favicon and images (and any you upload) '
        'with your fingerprints. Both are combined into one report.')
with st.form('investigate'):
    a, b = st.columns([3, 2])
    target = a.text_input('Domain or URL *', placeholder='northstar-sale.shop or https://…')
    choice = b.selectbox('Brand', [None] + brand_ids, format_func=lambda i: 'Detect automatically' if i is None
                         else brand_names[i])
    extra = st.file_uploader('Images from the suspicious site (optional)', type=IMAGE_TYPES,
                             accept_multiple_files=True)
    c, d = st.columns(2)
    fetch = c.checkbox('Download the website and its images', value=True)
    network = d.checkbox('Check DNS and registration date', value=True)
    if st.form_submit_button('Run investigation', type='primary'):
        try:
            with st.spinner('Investigating…'):
                report = protection.investigate(target, choice, images=uploads(extra), check_network=network,
                                                fetch_website=fetch, conn=conn)
        except ValueError as exc:
            st.error(str(exc))
        else:
            st.session_state['bp_report'] = report.get('id')
            st.rerun()


def show_report(r):
    risk, nnr, dna = r['risk'], r['name_net'], r['brand_dna']
    st.markdown(f'''<div class="fx-verdict"><div class="fx-label muted">Investigation · {escape(r["domain"])}
 · against {escape(r["brand"]["name"])}</div>
<div class="v">{escape(LEVEL_TEXT.get(risk["level"], risk["level"]))} · {risk["score"]}/100</div>
<div class="fx-conf"><div class="fx-bar"><i style="width:{risk["score"]}%"></i></div><b>{escape(risk["basis"])}</b></div>
<p>{escape(risk["summary"])}</p></div>''', unsafe_allow_html=True)
    comp = risk['components']
    ui.note(f"Score = name {comp['name']} + infrastructure {comp['infrastructure']} + content {comp['content']}"
            f" · evidence of impersonation: {'yes' if risk['malicious_evidence'] else 'no'}")
    left, right = st.columns(2)
    with left:
        ui.section('NameNet')
        st.markdown(f"**{nnr['similarity']}/100** name similarity · "
                    f"{escape(CLASS_TEXT.get(nnr['classification'], nnr['classification']))}")
        for t in nnr['techniques']:
            st.markdown(f"- {escape(t['detail'])}")
        dns = nnr.get('dns') or {}
        if dns.get('status') not in (None, 'skipped'):
            ui.note(f"DNS: {dns['status'].replace('_', ' ')} {', '.join(dns.get('addresses', [])[:3])}"
                    + (f" · {dns['error']}" if dns.get('error') else ''))
        if nnr.get('domain_age_days') is not None:
            ui.note(f"Registered about {ui.age_text(nnr['domain_age_days'])} ago")
    with right:
        ui.section('Brand DNA')
        site = r.get('website')
        if site:
            ui.note(f"Website: {'loaded' if site['ok'] else 'not loaded — ' + (site['error'] or 'unknown')}"
                    f" · {site['images_found']} image(s) downloaded")
        st.markdown(f"**{dna['images_analysed']}** analysed · **{dna['exact_match_count']}** exact duplicate(s) · "
                    f"**{dna['similar_match_count']}** visually similar")
        for x in dna['invalid_images']:
            ui.note(f"Skipped {x['name']}: {x['error']}")
    hits = [(res, m, 'Exact') for res in dna['results'] for m in res.get('exact_matches', [])] + \
           [(res, m, 'Similar') for res in dna['results'] for m in res.get('similar_matches', [])]
    if hits:
        table(('Suspect image', 'Matches', 'Kind', 'Match', 'Similarity'),
              [[escape(res.get('source') or res['name']), escape(f"{m['brand_name']} · {m['filename']}"),
                escape(m['kind']), tag(k, solid=k == 'Exact'), meter(m['similarity'])] for res, m, k in hits])
    ui.section('Why')
    for reason in risk['reasons']:
        st.markdown(f'- {escape(reason)}')
    if risk['level'] not in ('official', 'none'):
        if st.button('Add to case files', key=f"bp_case_{r.get('id')}",
                     help='Review it on the Evidence page: mark it Fake, create the signed PDF, send the abuse report.'):
            try:
                brand = store.get_brand(conn, r['brand']['id'])
                domain = actions.add_suspect(protection.to_suspect(r), brand_dna.to_brand_dna(brand) if brand else None)
            except (ValueError, OSError) as exc:
                st.error(f'Could not add the case: {exc}')
            else:
                st.session_state['save_notice'] = f'{domain} added to the case files.'
                st.rerun()


shown = next((i for i in investigations if i['id'] == st.session_state.get('bp_report')), None)
if shown:
    show_report(shown['report'])

# --- NameNet ----------------------------------------------------------------
ui.section('NameNet · check domain variations', 3)
with st.form('namenet'):
    a, b = st.columns([2, 3])
    nb = a.selectbox('Brand', brand_ids, format_func=brand_names.get, key='nn_brand')
    cands = b.text_area('Candidate domains (one per line)', height=110, placeholder='northstar-sale.com\nn0rthstar.in')
    c, d = st.columns(2)
    nn_network = c.checkbox('Check DNS and registration date', value=True, key='nn_network')
    check = c.form_submit_button('Check candidates', type='primary')
    scan = d.form_submit_button('Scan generated typo / lookalike variants (DNS)')
    if check or scan:
        brand = next(x for x in brands if x['id'] == nb)
        with st.spinner('Checking domains…'):
            if check:
                res = name_net.check_domains(brand, cands.splitlines(), check_network=nn_network, conn=conn)
                st.session_state['save_notice'] = f'{len(res)} domain(s) checked.'
                st.rerun()
            out = name_net.scan_variants(brand['official_domain'], brand['name'])
            if out['dns'] != 'ok':
                st.warning(f"DNS is unavailable ({out['error']}); variants cannot be verified right now.")
            else:
                with conn:
                    for f in out['findings']:
                        store.save_finding(conn, brand['id'], f)
                st.session_state['save_notice'] = (f"{out['checked']} variants checked, "
                                                   f"{len(out['findings'])} live lookalike(s) found.")
                st.rerun()

ui.section('Suspicious domain variations', 4)
flagged = [f for f in findings if f['classification'] != 'unrelated']
if engine['name_net']:
    table(('Domain', 'Found by', 'Risk', 'Label'),
          [[ui.breakable(r['domain']), 'NameNet (FORGE engine)', meter(r['score']),
            tag(r['label'].replace('_', ' ') or '—', solid=r['label'] == 'likely_fake')] for r in engine['name_net']])
if flagged:
    table(('Domain', 'Brand', 'Name similarity', 'Classification', 'Why it was flagged'),
          [[ui.breakable(f['domain']), escape(f['brand_name']), meter(f['similarity']),
            tag(CLASS_TEXT.get(f['classification'], f['classification']), solid=f['classification'] == 'suspicious'),
            '<br>'.join(escape(t['detail']) for t in f['techniques']) or escape(f['reasons'][0] if f['reasons'] else '')]
           for f in flagged])
    ui.note(name_net.EVIDENCE_NOTE)
elif not engine['name_net']:
    ui.note('No lookalike domains recorded yet.')

# --- Combined risk ------------------------------------------------------------
ui.section('Combined risk assessments', 5)
if investigations:
    table(('#', 'Domain', 'Brand', 'Risk', 'Level', 'Images', 'Exact', 'Similar', 'Basis', 'When'),
          [[str(i['id']), ui.breakable(i['domain']), escape(i['report']['brand']['name']), meter(i['risk_score']),
            tag(LEVEL_TEXT.get(i['risk_level'], i['risk_level']), solid=i['risk_level'] == 'high'),
            str(i['images_analysed']), str(i['exact_matches']), str(i['similar_matches']),
            escape(i['report']['risk']['basis']), escape(i['created_at'][:10])]
           for i in investigations])
    pick = st.selectbox('Open a report', [i['id'] for i in investigations],
                        format_func=lambda x: next(f"#{i['id']} · {i['domain']}" for i in investigations if i['id'] == x),
                        index=None, placeholder='Choose an investigation…')
    if pick and pick != st.session_state.get('bp_report'):
        st.session_state['bp_report'] = pick
        st.rerun()
else:
    ui.note('No investigations yet.')
conn.close()
