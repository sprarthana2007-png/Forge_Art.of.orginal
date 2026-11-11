"""Cases come from the team's shared ForgeState file (schemas.py); owner decisions live in SQLite.

Each non-ignored Suspect becomes a "case". Owner decisions and report status are kept in
SQLite (so a new pipeline run can never overwrite them) and are also written back to the
suspect's `status` field so the rest of the pipeline sees them.
"""
import json
import os
import sqlite3
import sys
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
if str(REPO) not in sys.path:  # the shared schemas.py and the FORGE engine (prarthana/) live at the repo root
    sys.path.append(str(REPO))

from schemas import Suspect, state_from_dict  # noqa: E402

DEMO_STATE = ROOT / 'forge_state.json'
ENGINE_STATE = Path(os.getenv('FORGE_CACHE_DIR') or REPO / 'cache').resolve() / 'state.json'


def default_state_file():
    """FORGE_STATE_FILE if set; else the FORGE engine's results (cache/state.json) once it has run; else the demo."""
    if os.getenv('FORGE_STATE_FILE'):
        return Path(os.environ['FORGE_STATE_FILE']).resolve()
    return ENGINE_STATE if ENGINE_STATE.is_file() else DEMO_STATE.resolve()


def refresh_state_file():
    """Switch from the demo to the engine's results as soon as the engine has produced them (called per page)."""
    global STATE_FILE
    if STATE_FILE == DEMO_STATE.resolve() and not os.getenv('FORGE_STATE_FILE'):
        STATE_FILE = default_state_file()


def state_source():
    """'engine', 'demo' or 'custom' (FORGE_STATE_FILE pointing somewhere else)."""
    if STATE_FILE == ENGINE_STATE:
        return 'engine'
    return 'demo' if STATE_FILE == DEMO_STATE.resolve() else 'custom'


STATE_FILE = default_state_file()
DB_FILE = Path(os.getenv('FORGE_DB_PATH', str(ROOT / 'data' / 'forge.db'))).resolve()
DECISIONS = ('pending', 'fake', 'genuine', 'unsure')
REPORT_STATUSES = ('not started', 'drafted', 'sent', 'resolved')
LABELS = ('likely_fake', 'suspicious', 'ignored')
STATUSES = ('new', 'investigated', 'confirmed', 'not_fake', 'reported')


def connect():
    DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_FILE, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute('''CREATE TABLE IF NOT EXISTS reviews (
        case_id INTEGER PRIMARY KEY, domain TEXT NOT NULL UNIQUE,
        decision TEXT NOT NULL DEFAULT 'pending',
        report_status TEXT NOT NULL DEFAULT 'not started',
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP)''')
    conn.commit()
    return conn


# --- Reading the shared state ---------------------------------------------

def _check(condition, message):
    if not condition:
        raise ValueError(message)


def validate_state(data):
    """Check the parts of a ForgeState dict the dashboard relies on. Returns the dict."""
    _check(isinstance(data, dict), 'The state file must contain a JSON object (state_to_dict output).')
    _check(isinstance(data.get('suspects', []), list), 'suspects must be a list.')
    for n, s in enumerate(data.get('suspects', []), 1):
        where = f'Suspect {n}'
        _check(isinstance(s, dict), f'{where} must be an object.')
        _check(isinstance(s.get('url'), str) and s['url'].strip(), f'{where}: url must be a non-empty string.')
        where = f"Suspect {s.get('domain') or s['url']}"
        score = s.get('score', 0)
        _check(type(score) in (int, float) and 0 <= score <= 100, f'{where}: score must be between 0 and 100.')
        _check(s.get('label', 'ignored') in LABELS, f'{where}: label must be one of {", ".join(LABELS)}.')
        _check(s.get('status', 'new') in STATUSES, f'{where}: status must be one of {", ".join(STATUSES)}.')
        for key in ('reasons', 'scam_phrases', 'contacts_masked'):
            _check(isinstance(s.get(key, []), list) and all(isinstance(v, str) for v in s.get(key, [])),
                   f'{where}: {key} must be a list of strings.')
        for key in ('image_exact_matches', 'image_similar_matches'):  # engine format: {url, sha256|phash, path}
            _check(isinstance(s.get(key, []), list) and all(isinstance(v, (dict, str)) for v in s.get(key, [])),
                   f'{where}: {key} must be a list of image matches.')
        pairs = s.get('matched_image_pairs', [])
        _check(isinstance(pairs, list) and all(isinstance(p, list) and len(p) == 3 and all(isinstance(v, str) for v in p)
                                               for p in pairs),
               f'{where}: matched_image_pairs needs [official_path, suspect_path, match_kind] entries.')
        age = s.get('domain_age_days')
        _check(age is None or (type(age) is int and age >= 0), f'{where}: domain_age_days must be a whole number of days.')
    return data


def read_state():
    return state_from_dict(validate_state(json.loads(STATE_FILE.read_text(encoding='utf-8'))))


def load_brand():
    """The BrandDNA from the state file as a dict, or None."""
    state = read_state()
    return vars(state.brand_dna) if state.brand_dna else None


def suspect_domain(suspect):
    return suspect.domain or (urlparse(suspect.url).hostname or suspect.url).removeprefix('www.')


def _initial_review(status):
    """What a pipeline status means as an owner decision, for cases the owner has not reviewed yet."""
    return {'confirmed': ('fake', 'not started'), 'not_fake': ('genuine', 'not started'),
            'reported': ('fake', 'sent')}.get(status, ('pending', 'not started'))


def _case(case_id, suspect: Suspect, brand):
    return {
        'case_id': case_id, 'domain': suspect_domain(suspect), 'url': suspect.url,
        'risk_score': max(0, min(100, suspect.score)), 'label': suspect.label, 'reasons': list(suspect.reasons),
        'official_screenshot': brand.screenshot_path if brand else '', 'fake_screenshot': suspect.screenshot_path,
        'matched_images': list(suspect.matched_image_pairs),
        'image_exact_matches': list(suspect.image_exact_matches), 'image_similar_matches': list(suspect.image_similar_matches),
        'logo_match': suspect.logo_match, 'domain_age_days': suspect.domain_age_days,
        'official_domain_age_days': brand.domain_age_days if brand else None,
        'page_title': suspect.page_title, 'page_text': suspect.page_text, 'scam_phrases': list(suspect.scam_phrases),
        'prices_found': dict(suspect.prices_found), 'contacts_masked': list(suspect.contacts_masked),
        'found_by': suspect.found_by, 'campaign_id': suspect.campaign_id, 'ip': suspect.ip,
        'abuse_email': suspect.abuse_email, 'evidence_sha256': suspect.evidence_sha256, 'pdf_path': suspect.pdf_path,
        'pipeline_verdict': suspect.verdict, 'pipeline_status': suspect.status,
    }


def load_cases():
    """Every suspect not labelled 'ignored', as a case dict with the owner's saved review merged in."""
    state = read_state()
    suspects, seen = [], set()
    for suspect in state.suspects:
        domain = suspect_domain(suspect)
        if suspect.label != 'ignored' and domain not in seen:
            seen.add(domain)
            suspects.append(suspect)
    conn = connect()
    try:
        with conn:
            ids = {r['domain']: r['case_id'] for r in conn.execute('SELECT case_id, domain FROM reviews')}
            next_id = max(ids.values(), default=0) + 1
            for suspect in suspects:
                domain = suspect_domain(suspect)
                if domain not in ids:  # first time this domain is seen: give it the next case number
                    decision, report = _initial_review(suspect.status)
                    conn.execute('INSERT INTO reviews (case_id, domain, decision, report_status) VALUES (?, ?, ?, ?)',
                                 (next_id, domain, decision, report))
                    ids[domain], next_id = next_id, next_id + 1
            saved = {r['case_id']: dict(r) for r in conn.execute('SELECT * FROM reviews')}
    finally:
        conn.close()
    cases = [_case(ids[suspect_domain(s)], s, state.brand_dna) for s in suspects]
    for case in cases:
        case.update({k: saved[case['case_id']][k] for k in ('decision', 'report_status', 'updated_at')})
    return sorted(cases, key=lambda c: c['case_id'])


# --- Saving owner reviews --------------------------------------------------

def pipeline_status(decision, report_status):
    """The Suspect.status that matches an owner review, or None to leave the pipeline's value alone."""
    if report_status in ('sent', 'resolved'):
        return 'reported'
    return {'fake': 'confirmed', 'genuine': 'not_fake', 'unsure': 'investigated'}.get(decision)


def _write_status(domain, status):
    """Set one suspect's status in the state file, keeping every other field as it is."""
    data = json.loads(STATE_FILE.read_text(encoding='utf-8'))
    for suspect in data.get('suspects', []):
        if (suspect.get('domain') or (urlparse(suspect.get('url', '')).hostname or '').removeprefix('www.')) == domain:
            suspect['status'] = status
    tmp = STATE_FILE.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    tmp.replace(STATE_FILE)  # atomic: other modules never see a half-written file


def save_review(case_id, *, decision=None, report_status=None):
    """Save the owner's review in SQLite, then mirror it into the suspect's status in the state file.

    A Fake / Genuine decision is also passed to the FORGE engine (actions.confirm -> prarthana.alert.confirm):
    it learns from the answer, tracks confirmed fakes for relaunches and prepares the Safe Browsing report.
    Returns a short note about the engine step ('' when there was nothing to do)."""
    updates = {}
    if decision is not None:
        if decision not in DECISIONS:
            raise ValueError('Invalid decision')
        updates['decision'] = decision
    if report_status is not None:
        if report_status not in REPORT_STATUSES:
            raise ValueError('Invalid report status')
        updates['report_status'] = report_status
    if not updates:
        return ''
    conn = connect()
    try:
        with conn:
            sql = ', '.join(f'{key}=?' for key in updates)
            cur = conn.execute(f'UPDATE reviews SET {sql}, updated_at=CURRENT_TIMESTAMP WHERE case_id=?', (*updates.values(), case_id))
            if cur.rowcount != 1:
                raise ValueError('Unknown case_id')
            row = conn.execute('SELECT * FROM reviews WHERE case_id=?', (case_id,)).fetchone()
    finally:
        conn.close()
    note = ''
    if decision in ('fake', 'genuine'):
        import actions
        problem = actions.engine_error()
        if problem:
            note = f'Saved, but the FORGE engine is unavailable ({problem}).'
        else:
            try:
                actions.confirm(row['domain'], decision == 'fake')
                note = 'FORGE engine updated' + (' and Safe Browsing report prepared.' if decision == 'fake' else '.')
            except Exception as exc:  # the decision itself is saved; report the engine problem
                note = f'Saved, but the FORGE engine step failed ({type(exc).__name__}: {exc}).'
    status = pipeline_status(row['decision'], row['report_status'])
    if status:
        _write_status(row['domain'], status)
    return note


# --- Workspace profile (single brand owner, no accounts) -------------------

PROFILE_FIELDS = ('brand_name', 'business_name', 'website', 'industry', 'contact_name', 'contact_email',
                  'contact_phone', 'address', 'country', 'tax_id', 'appearance')
INDUSTRIES = ('', 'Apparel & footwear', 'Beauty & personal care', 'Consumer electronics', 'Food & beverage',
              'Home & furniture', 'Jewellery & accessories', 'Outdoor & sports', 'Other')
PROFILE_DEFAULTS = {'brand_name': 'Northstar', 'business_name': 'Northstar Outdoor Pvt Ltd',
                    'website': 'northstar.example', 'industry': 'Outdoor & sports', 'country': 'India',
                    'appearance': 'Light'}


def _profile_table(conn):
    conn.execute('CREATE TABLE IF NOT EXISTS profile (key TEXT PRIMARY KEY, value TEXT NOT NULL)')


def load_profile():
    conn = connect()
    try:
        _profile_table(conn)
        saved = dict(conn.execute('SELECT key, value FROM profile').fetchall())
    finally:
        conn.close()
    return {field: saved.get(field, PROFILE_DEFAULTS.get(field, '')) for field in PROFILE_FIELDS}


def save_profile(**fields):
    unknown = set(fields) - set(PROFILE_FIELDS)
    if unknown:
        raise ValueError(f'Unknown profile field: {sorted(unknown)[0]}')
    fields = {k: str(v or '').strip()[:500] for k, v in fields.items()}
    if 'brand_name' in fields and not fields['brand_name']:
        raise ValueError('Brand name cannot be empty.')
    conn = connect()
    try:
        with conn:
            _profile_table(conn)
            conn.executemany('INSERT OR REPLACE INTO profile (key, value) VALUES (?, ?)', fields.items())
    finally:
        conn.close()
    return load_profile()


# --- Gemini verdicts -------------------------------------------------------

def _verdict_table(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS verdicts (
        id INTEGER PRIMARY KEY AUTOINCREMENT, case_id INTEGER NOT NULL, model TEXT NOT NULL,
        investigation TEXT NOT NULL, result TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP)''')


def save_verdict(case_id, model, investigation, result):
    conn = connect()
    try:
        with conn:
            _verdict_table(conn)
            conn.execute('INSERT INTO verdicts (case_id, model, investigation, result) VALUES (?, ?, ?, ?)',
                         (case_id, model, json.dumps(investigation, ensure_ascii=False),
                          json.dumps(result, ensure_ascii=False)))
    finally:
        conn.close()


def latest_verdict(case_id):
    """The most recent Gemini verdict for a case, or None."""
    conn = connect()
    try:
        _verdict_table(conn)
        row = conn.execute('SELECT * FROM verdicts WHERE case_id=? ORDER BY id DESC LIMIT 1', (case_id,)).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    return {'model': row['model'], 'created_at': row['created_at'],
            'investigation': json.loads(row['investigation']), 'result': json.loads(row['result'])}
