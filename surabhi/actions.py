"""Owner actions that call the FORGE engine (prarthana/alert.py) from the dashboard.

    confirm(domain, yes)     "Fake" / "Genuine": records the answer for learning, remembers the fake for relaunch
                             tracking and prepares the Google Safe Browsing pack (prarthana.alert.confirm)
    evidence_pdf(domain)     signed, tamper-evident evidence PDF (prarthana.alert.make_evidence_pdf)
    abuse_report(domain)     abuse e-mail with the signed PDF (prarthana.alert.send_abuse_report). Demo mode by
                             default: it goes to GMAIL_ADDRESS or is saved as an .eml, never to the real registrar
    verify(path)             is the PDF signed and unchanged? (prarthana.alert.verify_evidence)
    add_suspect(suspect)     put a Brand protection investigation into the case files

Every change is written back to the state file the dashboard reads (cache/state.json for engine results).
"""
import os
import re
from pathlib import Path

import storage
from schemas import BrandDNA, ForgeState, read_json_file, state_from_dict, state_to_dict, write_json_file

OUTPUT = Path(os.getenv('FORGE_OUTPUT_DIR') or storage.REPO / 'output').resolve()


def engine_error():
    """None if the FORGE engine can be used, else a readable reason (e.g. a missing package)."""
    try:
        import prarthana.alert  # noqa: F401
    except Exception as exc:  # ImportError for missing packages, or anything raised while importing
        return f'{type(exc).__name__}: {exc}'
    return None


def demo_mode():
    return ''.join(os.getenv('DEMO_MODE', '').split()).lower() != 'off'


def _load():
    return state_from_dict(storage.validate_state(read_json_file(storage.STATE_FILE)))


def _save(state):
    write_json_file(storage.STATE_FILE, state_to_dict(state))  # temp file + swap: the engine never reads a half-written file


def _find(state, domain):
    for s in state.suspects:
        if storage.suspect_domain(s) == domain:
            return s
    raise ValueError(f'{domain} is not in the state file any more.')


def _dna(state):
    return state.brand_dna or BrandDNA(store_url='', brand_name='the brand')


def safe_browsing_path(domain):
    """output/safe_browsing_<domain>.txt as written by the engine, or None if it does not exist yet."""
    path = OUTPUT / f"safe_browsing_{re.sub(r'[^A-Za-z0-9]+', '_', domain).strip('_')[:60]}.txt"
    return path if path.is_file() else None


def confirm(domain, yes):
    from prarthana.alert import confirm as engine_confirm
    state = _load()
    s = _find(state, domain)
    engine_confirm(s, yes, _dna(state))
    for other in state.suspects:  # the same site listed twice gets the same answer
        if storage.suspect_domain(other) == domain:
            other.status = s.status
    _save(state)
    return {'status': s.status, 'safe_browsing': str(safe_browsing_path(domain) or '')}


def verify(pdf_path):
    """True / False, or None when there is no PDF or the engine is unavailable."""
    path = Path(pdf_path) if pdf_path else None
    if not path or not path.is_file() or engine_error():
        return None
    from prarthana.alert import verify_evidence
    try:
        return bool(verify_evidence(str(path)))
    except Exception:
        return False


def evidence_pdf(domain):
    from prarthana.alert import make_evidence_pdf
    state = _load()
    s = _find(state, domain)
    if s.status not in ('confirmed', 'reported'):
        raise ValueError('Mark the case as Fake first: evidence PDFs are made for confirmed fakes.')
    s.pdf_path = make_evidence_pdf(s, _dna(state), peers=state.suspects)
    _save(state)
    return {'pdf_path': s.pdf_path, 'sha256': s.evidence_sha256, 'verified': verify(s.pdf_path)}


def abuse_report(domain):
    from prarthana.alert import send_abuse_report
    state = _load()
    s = _find(state, domain)
    if s.status != 'confirmed':
        raise ValueError('Only a case marked Fake (and not reported yet) can be reported.')
    if not verify(s.pdf_path):  # attach a fresh signed PDF whose path we keep in the state file
        evidence_pdf(domain)
        state = _load()
        s = _find(state, domain)
    how = send_abuse_report(s, _dna(state))
    if how == 'not_confirmed':
        raise ValueError('The engine refused: the case is not confirmed.')
    _save(state)
    return {'how': how, 'abuse_email': s.abuse_email, 'demo': demo_mode(), 'pdf_path': s.pdf_path}


def add_suspect(suspect, brand_dna=None):
    """Add (or update) a suspect in the state file so it becomes a case. Creates the file if needed.

    While the dashboard still shows the synthetic Northstar demo, a real investigation is not mixed into it: the
    case goes into a fresh state file (the engine's cache/state.json) under the brand it was checked against, and
    the dashboard switches to it."""
    if storage.state_source() == 'demo':
        storage.STATE_FILE = storage.ENGINE_STATE
    if storage.STATE_FILE.is_file():
        state = _load()
    else:
        state = ForgeState(brand_dna=brand_dna)
        storage.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    if state.brand_dna is None and brand_dna is not None:
        state.brand_dna = brand_dna
    domain = storage.suspect_domain(suspect)
    kept = [s for s in state.suspects if storage.suspect_domain(s) == domain]
    if kept:  # keep the owner's progress (status, PDF) on a site that is already a case
        suspect.status, suspect.pdf_path, suspect.evidence_sha256 = kept[0].status, kept[0].pdf_path, kept[0].evidence_sha256
    state.suspects = [s for s in state.suspects if storage.suspect_domain(s) != domain] + [suspect]
    state.seen_domains.add(domain)
    _save(state)
    return domain
