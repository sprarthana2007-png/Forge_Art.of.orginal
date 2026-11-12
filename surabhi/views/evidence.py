"""One case file: record-sleeve case picker, evidence tabs, reasons, Gemini verdict, owner decision."""
import json
import math
from html import escape
from pathlib import Path

import streamlit as st

import actions
import gemini
import storage
import ui
from phrases import find_phrases, headline

TABS = ('Overview', 'Images', 'Domain', 'Phrases', 'Verdict')
BROWSER_BAR = 76  # px of fake browser chrome at the top of each screenshot

profile = ui.base('Evidence')
cases = ui.load_cases_or_stop()
brand = storage.load_brand() or {}
case = ui.selected_case(cases)
ids = [c['case_id'] for c in cases]
position = ids.index(case['case_id'])
ui.header(stamp=case['report_status'])
ui._bottom_menu('Evidence')
ui.notice()

st.session_state.setdefault('evidence_tab', TABS[0])
saved_verdict = storage.latest_verdict(case['case_id'])
phrases = find_phrases(case.get('page_text', ''))
# Phrases the pipeline recorded that the built-in patterns did not already find.
extra_phrases = [p for p in case.get('scam_phrases', [])
                 if p.lower() not in {match.lower() for _, _, match, _ in phrases}]
official_url = brand.get('store_url') or (f"https://{profile['website']}" if profile['website'] else 'Official website')
official_domain = (official_url.split('://')[-1].split('/')[0]).removeprefix('www.')
suspect_url = case.get('url') or f"https://{case['domain']}"


def pick_case(case_id):
    st.session_state['selected_case_id'] = case_id


def pick_tab(name):
    st.session_state['evidence_tab'] = name


def step_tab(delta):
    st.session_state['evidence_tab'] = TABS[(TABS.index(st.session_state['evidence_tab']) + delta) % len(TABS)]


# --- Left: record sleeve with the vinyl case picker -----------------------

def sleeve():
    with st.container(key='sleeve'):
        st.markdown(f'''<div class="fx-sleeve-title">CASE {case["case_id"]:03d}</div>
<div class="fx-label" style="margin-top:1.1rem;font-size:.66rem">Domain</div>
<div class="fx-sleeve-domain">{ui.breakable(case["domain"])}</div>''', unsafe_allow_html=True)
        size, radius = 250, 97
        rules = []
        with st.container(key='vinyl'):
            st.markdown(f'<div class="fx-disc"><div class="ring"></div><div class="label"><div>'
                        f'<b>CASE<br>{case["case_id"]:03d}</b><i></i></div></div></div>', unsafe_allow_html=True)
            for i, cid in enumerate(ids):
                angle = math.radians(225 - i * 360 / len(ids))
                current = cid == case['case_id']
                button = 48 if current else 42
                left = size / 2 + radius * math.cos(angle) - button / 2
                top = size / 2 + radius * math.sin(angle) - button / 2
                key = f'vinylon_{cid}' if current else f'vinyl_{cid}'
                rules.append(f'.st-key-{key} {{ left: {left:.0f}px; top: {top:.0f}px; }}')
                with st.container(key=key):
                    st.button(f'{i + 1:02d}', key=f'pick_{cid}', on_click=pick_case, args=(cid,),
                              help=cases[i]['domain'])
        st.markdown(f'<style>{" ".join(rules)}</style>', unsafe_allow_html=True)
        with st.container(key='case_steps'):
            prev, nxt = st.columns(2)
            prev.button('‹  Previous Case', key='case_prev', use_container_width=True, disabled=position == 0,
                        on_click=pick_case, args=(ids[max(0, position - 1)],))
            nxt.button('Next Case  ›', key='case_next', use_container_width=True, disabled=position == len(ids) - 1,
                       on_click=pick_case, args=(ids[min(len(ids) - 1, position + 1)],))


# --- Right: the case file -------------------------------------------------

def tab_thumb(name):
    if name == 'Overview':
        uri = ui.image_uri(case['fake_screenshot'], width=360, top=BROWSER_BAR)
        return f'<img src="{uri}" alt="">' if uri else ''
    if name == 'Images':
        source = case['matched_images'][0][1] if case.get('matched_images') else case['fake_screenshot']
        uri = ui.image_uri(source, width=300)
        return f'<img src="{uri}" alt="" style="object-fit:contain;background:#fff">' if uri else ''
    if name == 'Domain':
        return f'<span class="fx-mini-url">🔒 {escape(case["domain"])}</span>'
    if name == 'Phrases':
        top = headline(phrases) or (extra_phrases[0] if extra_phrases else None)
        return f'<span class="fx-mini-chip">{escape(top or "None found")}</span>'
    age = ui.age_text(case.get('domain_age_days'))
    text = saved_verdict['result']['verdict'] if saved_verdict else (f'Registered {age} ago' if age else 'Not reviewed')
    return f'<span class="fx-mini-date">🗓 {escape(text)}</span>'


def tab_strip():
    current = st.session_state['evidence_tab']
    cols = st.columns([.45, 1, 1, 1, 1, 1, .45], gap='small')
    with cols[0], st.container(key='tabnav_prev'):
        st.button('‹', key='tab_prev', on_click=step_tab, args=(-1,))
    for i, name in enumerate(TABS):
        key = f'hit_tabon_{i}' if name == current else f'hit_tab_{i}'
        with cols[i + 1], st.container(key=key):
            st.markdown(f'<div class="fx-tabcard"><div class="n">{i + 1:02d}</div><div class="thumb">{tab_thumb(name)}</div>'
                        f'<div class="t">{name}</div></div>', unsafe_allow_html=True)
            st.button(name, key=f'tab_{name}', on_click=pick_tab, args=(name,))
    with cols[-1], st.container(key='tabnav_next'):
        st.button('›', key='tab_next', on_click=step_tab, args=(1,))


def site_panel(kind, url, screenshot):
    uri = ui.image_uri(screenshot, width=1100, top=BROWSER_BAR)
    pill = '<span class="fx-pill ok">Official website</span>' if kind == 'ok' else '<span class="fx-pill bad">Suspected website</span>'
    full = ui.image_uri(screenshot, width=1600) if uri else None  # whole picture, address bar included
    body = (f'<details class="fx-zoom"><summary><img src="{uri}" alt="{escape(url)}">'
            f'<div class="fx-zoom-hint">Click to enlarge</div>'
            f'<span class="fx-zoom-full"><img src="{full}" alt="{escape(url)}"></span></summary></details>'
            if uri else '<div class="fx-note">Screenshot missing.</div>')
    st.markdown(f'<div class="fx-site"><div class="fx-site-head">{pill}<span class="u">{escape(url)} ↗</span></div>{body}</div>',
                unsafe_allow_html=True)


def side_column():
    reasons = ''.join(f'<li>{escape(r)}</li>' for r in case['reasons'])
    if saved_verdict:
        result = saved_verdict['result']
        verdict = f'<b>{escape(result["verdict"])}</b>{escape(result["summary"])}'
    elif case.get('pipeline_verdict'):
        verdict = escape(case['pipeline_verdict'])
    else:
        verdict = '<span style="color:var(--muted)">Gemini has not reviewed this case yet. Open the Verdict tab to ask.</span>'
    st.markdown(f'<div class="fx-side"><span class="fx-label">Reasons</span><ul class="fx-reasons">{reasons}</ul>'
                f'<div class="verdict"><span class="fx-label">Gemini verdict</span>{verdict}</div></div>',
                unsafe_allow_html=True)


def overview():
    official, suspect, side = st.columns([1, 1, .78], gap='medium')
    with official:
        site_panel('ok', official_url, case['official_screenshot'])
    with suspect:
        site_panel('bad', suspect_url, case['fake_screenshot'])
    with side:
        side_column()


def images():
    pairs = case.get('matched_images', [])
    exact, similar = len(case.get('image_exact_matches', [])), len(case.get('image_similar_matches', []))
    ui.note(f"{exact} exact cop{'y' if exact == 1 else 'ies'} (SHA-256) · {similar} near-duplicate{'s' * (similar != 1)} "
            f"(perceptual hash) · logo {'copied' if case.get('logo_match') else 'not copied'}")
    if not pairs:
        st.info('No image pairs to show. Run analyze.py on this suspect to collect them.' if exact or similar
                else 'No copied or near-duplicate images were found on this site.')
        return
    for index, (official, suspect, kind) in enumerate(pairs, 1):
        label, left, right = st.columns([1, 1, 1], gap='large', vertical_alignment='center')
        with label:
            st.markdown(f'<div class="fx-label">Match {index:02d}</div><div class="fx-match-kind" style="margin-top:.5rem">'
                        f'{escape(kind)}</div>', unsafe_allow_html=True)
        with left:
            ui.evidence_image(official, 'Official image')
        with right:
            ui.evidence_image(suspect, 'Found on suspected site')


def fact_rows(rows):
    st.markdown(''.join(f'<div class="fx-phrase"><b>{escape(k)}</b><span>{escape(str(v))}</span></div>'
                        for k, v in rows if v not in (None, '', [], {})), unsafe_allow_html=True)


def domain():
    left, right = st.columns(2, gap='large')
    with left:
        ui.age_card(f'{case["domain"]} · age', case.get('domain_age_days'))
    with right:
        ui.age_card(f'{official_domain} · age', case.get('official_domain_age_days'))
    ui.section('Site facts')
    fact_rows([('Suspected URL', suspect_url), ('Official store', official_url), ('Page title', case.get('page_title')),
               ('Found by', (case.get('found_by') or '').replace('_', ' ')), ('IP address', case.get('ip')),
               ('Abuse contact', case.get('abuse_email')), ('Campaign', case.get('campaign_id')),
               ('Pipeline label', (case.get('label') or '').replace('_', ' ')),
               ('Pipeline status', (case.get('pipeline_status') or '').replace('_', ' '))])
    contacts = case.get('contacts_masked', [])
    prices = case.get('prices_found', {})
    if contacts or prices:
        ui.section('Contacts and prices')
        official_prices = brand.get('prices', {})
        fact_rows([('Contacts (masked)', ', '.join(contacts))] +
                  [(f'Price · {name}', f'₹{price:,.0f}' + (f'  (official ₹{official_prices[name]:,.0f})' if name in official_prices else ''))
                   for name, price in prices.items() if isinstance(price, (int, float))])
    if case.get('evidence_sha256') or case.get('pdf_path'):
        ui.section('Evidence file')
        fact_rows([('Evidence fingerprint', case.get('evidence_sha256'))])
        pdf = ui.evidence_path(case['pdf_path']) if case.get('pdf_path') else None
        if pdf and pdf.is_file():
            st.download_button('Download evidence PDF', pdf.read_bytes(), file_name=pdf.name, mime='application/pdf',
                               icon=':material/download:')


def phrases_tab():
    if not phrases and not extra_phrases:
        st.info('No pressure or payment phrases were found in the page text.')
        return
    total = len(phrases) + len(extra_phrases)
    ui.note(f'{total} warning phrase{"s" * (total != 1)} found on {case["domain"]}.')
    st.markdown(''.join(
        f'<div class="fx-phrase"><b>{escape(label)}</b><span>{escape(before)}<mark>{escape(match)}</mark>{escape(after)}</span></div>'
        for label, before, match, after in phrases), unsafe_allow_html=True)
    if extra_phrases:
        st.markdown(''.join(f'<div class="fx-phrase"><b>Flagged by pipeline</b><span><mark>{escape(p)}</mark></span></div>'
                            for p in extra_phrases), unsafe_allow_html=True)


def verdict_tab():
    investigation = gemini.build_investigation(case, profile, brand)
    with st.expander('Investigation JSON sent to Gemini (with both screenshots)'):
        st.code(json.dumps(investigation, indent=2, ensure_ascii=False), language='json')
    missing = gemini.missing_packages()
    ready = not missing and gemini.api_key_configured()
    if missing:
        st.warning(f"Gemini needs {' and '.join(missing)}, which {'is' if len(missing) == 1 else 'are'} not installed. "
                   'Stop the app, run `python -m pip install -r requirements.txt`, then start it again.')
    elif not ready:
        st.info('Gemini is not connected. Set GEMINI_API_KEY before starting the app, then come back here.')
    if st.button('Ask Gemini again' if saved_verdict else 'Ask Gemini', type='primary',
                 icon=':material/auto_awesome:', disabled=not ready, key='run_verdict'):
        with st.spinner('Gemini is reviewing the evidence…'):
            try:
                sent, result, model = gemini.get_verdict(case, profile, brand)
            except Exception as exc:  # network, quota, bad key, unexpected output
                st.error(f'Gemini could not give a verdict: {exc}')
            else:
                storage.save_verdict(case['case_id'], model, sent, result)
                st.rerun()
    if saved_verdict:
        ui.verdict_card(saved_verdict)
        if saved_verdict['investigation'] != investigation:
            ui.note('The evidence has changed since this verdict. Ask Gemini again for an up-to-date verdict.')


def decision_row():
    with st.container(key='decision'):
        label, *buttons = st.columns([1.15, 1, 1, 1], gap='medium', vertical_alignment='center')
        label.markdown('<span class="fx-label" style="font-size:.85rem">Owner decision</span>', unsafe_allow_html=True)
        for column, decision in zip(buttons, ('fake', 'genuine', 'unsure')):
            current = case['decision'] == decision
            if column.button(decision.title(), key=f"{case['case_id']}_{decision}", use_container_width=True,
                             type='primary' if current else 'secondary'):
                ui.persist(case['case_id'], decision=decision)
        ui.note(f"Last saved (UTC): {case['updated_at']}")


SAFE_BROWSING_FORM = 'https://safebrowsing.google.com/safebrowsing/report_phish/'


def run_action(label, fn, *args):
    """Run an engine action, keep its result for the next run, show errors."""
    try:
        with st.spinner(f'{label}…'):
            result = fn(*args)
    except (ValueError, OSError) as exc:
        st.error(str(exc))
        return
    except Exception as exc:  # a failure inside the engine: show it, keep the page working
        st.error(f'{label} failed ({type(exc).__name__}: {exc})')
        return
    st.session_state['action_result'] = (case['domain'], label, result)
    st.rerun()


def engine_actions():
    """After the owner says Fake: signed evidence PDF, abuse report and Safe Browsing pack from the FORGE engine."""
    if case['decision'] != 'fake':
        return
    ui.section('Act on this fake')
    problem = actions.engine_error()
    if problem:
        st.warning(f'The FORGE engine is not available here ({problem}). Install the repository requirements '
                   '(`pip install -r requirements.txt` at the repository root) to make evidence PDFs and reports.')
        return
    done = st.session_state.pop('action_result', None)
    if done and done[0] == case['domain']:
        label, result = done[1], done[2]
        if label == 'Sending abuse report':
            where = (f"saved as {Path(result['how'][4:]).name} in output/" if result['how'].startswith('eml:')
                     else f"sent by {result['how']}")
            st.success(f"Abuse report {where}" + (f" (demo mode: the real contact would be "
                                                    f"{result['abuse_email'] or 'the registrar'})" if result['demo'] else ''))
        else:
            st.success(f"Signed evidence PDF created · {Path(result['pdf_path']).name}")
    status = case['pipeline_status']
    pdf = ui.evidence_path(case['pdf_path']) if case.get('pdf_path') else None
    a, b, c = st.columns(3, gap='medium')
    with a:
        st.markdown('<span class="fx-label">Evidence PDF</span>', unsafe_allow_html=True)
        if pdf and pdf.is_file():
            ok = actions.verify(pdf)
            st.markdown(f'<span class="fx-tag{" solid" if ok else ""}">'
                        f'{"Signed · verified" if ok else "Changed or unsigned"}</span>', unsafe_allow_html=True)
            st.download_button('Download PDF', pdf.read_bytes(), file_name=pdf.name, mime='application/pdf',
                               key=f"pdf_{case['case_id']}", use_container_width=True)
        if st.button('Create signed PDF' if not (pdf and pdf.is_file()) else 'Re-create PDF',
                     key=f"mkpdf_{case['case_id']}", use_container_width=True):
            run_action('Creating evidence PDF', actions.evidence_pdf, case['domain'])
    with b:
        st.markdown('<span class="fx-label">Abuse report</span>', unsafe_allow_html=True)
        if status == 'reported' or case['report_status'] in ('sent', 'resolved'):
            st.markdown('<span class="fx-tag solid">Reported</span>', unsafe_allow_html=True)
        elif st.button('Send abuse report', key=f"abuse_{case['case_id']}", type='primary', use_container_width=True):
            run_action('Sending abuse report', send_report)
        ui.note('Demo mode: goes to GMAIL_ADDRESS or an .eml file, not the registrar (DEMO_MODE=off to send).'
                if actions.demo_mode() else 'Live mode: sent to the registrar\'s abuse contact.')
    with c:
        st.markdown('<span class="fx-label">Google Safe Browsing</span>', unsafe_allow_html=True)
        pack = actions.safe_browsing_path(case['domain'])
        if pack:
            st.link_button('Open the report form', SAFE_BROWSING_FORM, use_container_width=True)
            ui.note('Paste the URL and details below; Google needs a person to submit it.')
        else:
            ui.note('Prepared by the engine when the case is marked Fake.')
    if pack:
        with st.expander('Safe Browsing report text'):
            st.code(pack.read_text(encoding='utf-8'), language=None)


def send_report():
    result = actions.abuse_report(case['domain'])
    storage.save_review(case['case_id'], report_status='sent')
    return result


def case_file():
    st.markdown(f'<div class="fx-folder-tab">FILE&nbsp;&nbsp;&nbsp;{ui.file_number(case)}</div>', unsafe_allow_html=True)
    with st.container(key='folder'):
        risk = ui.risk_label(case['risk_score'])
        st.markdown(f'''<div class="fx-file-head"><h2>The evidence speaks.</h2>
<div class="score">{case["risk_score"]:g}<small>/100</small></div>
<span class="fx-stamp big">{risk} risk</span></div>''', unsafe_allow_html=True)
        tab_strip()
        {'Overview': overview, 'Images': images, 'Domain': domain, 'Phrases': phrases_tab,
         'Verdict': verdict_tab}[st.session_state['evidence_tab']]()
        decision_row()
        engine_actions()


left, right = st.columns([1.3, 3.4], gap='large')
with left:
    sleeve()
with right:
    case_file()
