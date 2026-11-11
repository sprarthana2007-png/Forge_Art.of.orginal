"""Dashboard -> FORGE engine: Fake / Genuine decisions, signed evidence PDF, abuse report, adding cases.

Runs the real engine (prarthana/alert.py) against a copy of the demo state, with the engine's cache and
output in throw-away folders (see conftest.py). The registrar lookup is replaced so no network is used.
"""
import json
import os
from pathlib import Path

import pytest

import actions
import storage
from schemas import Suspect

DEMO = json.loads((Path(storage.ROOT) / 'forge_state.json').read_text(encoding='utf-8'))
DOMAIN = 'northstar-sale.example'


@pytest.fixture(autouse=True)
def state(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, 'DB_FILE', tmp_path / 'forge.db')
    monkeypatch.setattr(storage, 'STATE_FILE', tmp_path / 'state.json')
    storage.STATE_FILE.write_text(json.dumps(DEMO), encoding='utf-8')
    for var in ('RESEND_API_KEY', 'GMAIL_APP_PASSWORD', 'GMAIL_ADDRESS', 'DEMO_MODE'):
        monkeypatch.delenv(var, raising=False)
    import prarthana.alert
    monkeypatch.setattr(prarthana.alert, 'find_abuse_email', lambda domain: 'abuse@registrar.example')
    return storage.STATE_FILE


def suspect(domain=DOMAIN):
    data = json.loads(storage.STATE_FILE.read_text(encoding='utf-8'))
    return next(s for s in data['suspects'] if s['domain'] == domain)


def case_id(domain=DOMAIN):
    return next(c['case_id'] for c in storage.load_cases() if c['domain'] == domain)


def test_engine_is_available():
    assert actions.engine_error() is None


def test_fake_decision_calls_the_engine():
    note = storage.save_review(case_id(), decision='fake')
    assert 'FORGE engine updated' in note
    assert suspect()['status'] == 'confirmed'
    feedback = json.loads((Path(os.environ['FORGE_CACHE_DIR']) / 'feedback.json').read_text())
    assert any(f['domain'] == DOMAIN and f['answer'] == 'fake' for f in feedback)  # learning (#19)
    pack = actions.safe_browsing_path(DOMAIN)
    assert pack and DOMAIN in pack.read_text(encoding='utf-8')                      # Safe Browsing pack (#15)


def test_genuine_decision_records_not_fake():
    storage.save_review(case_id(), decision='genuine')
    assert suspect()['status'] == 'not_fake'
    feedback = json.loads((Path(os.environ['FORGE_CACHE_DIR']) / 'feedback.json').read_text())
    assert any(f['domain'] == DOMAIN and f['answer'] == 'not_fake' for f in feedback)


def test_unsure_does_not_call_the_engine():
    assert storage.save_review(case_id(), decision='unsure') == ''
    assert suspect()['status'] == 'investigated'


def test_evidence_pdf_needs_a_confirmed_fake():
    with pytest.raises(ValueError, match='Fake first'):
        actions.evidence_pdf(DOMAIN)


def test_signed_pdf_and_abuse_report():
    storage.save_review(case_id(), decision='fake')
    out = actions.evidence_pdf(DOMAIN)
    assert Path(out['pdf_path']).is_file() and out['verified'] is True and out['sha256']
    assert suspect()['pdf_path'] == out['pdf_path'] and suspect()['evidence_sha256'] == out['sha256']

    sent = actions.abuse_report(DOMAIN)
    assert sent['demo'] is True and sent['how'].startswith('eml:')  # demo mode: saved, not sent to the registrar
    eml = Path(sent['how'][4:]).read_text(encoding='utf-8', errors='replace')
    assert 'DEMO MODE' in eml and 'abuse@registrar.example' in eml
    assert suspect()['status'] == 'reported' and suspect()['abuse_email'] == 'abuse@registrar.example'
    with pytest.raises(ValueError, match='not reported yet'):
        actions.abuse_report(DOMAIN)


def test_tampered_pdf_is_not_verified():
    storage.save_review(case_id(), decision='fake')
    pdf = Path(actions.evidence_pdf(DOMAIN)['pdf_path'])
    pdf.write_bytes(pdf.read_bytes() + b'%tampered\n')
    assert actions.verify(pdf) is False
    assert actions.verify(None) is None


def test_engine_failure_keeps_the_decision(monkeypatch):
    def boom(domain, yes):
        raise RuntimeError('engine down')
    monkeypatch.setattr(actions, 'confirm', boom)
    note = storage.save_review(case_id(), decision='fake')
    assert 'engine down' in note and suspect()['status'] == 'confirmed'  # still mirrored by the dashboard


def test_add_suspect_creates_or_updates_a_case(tmp_path, monkeypatch):
    new = Suspect(url='https://n0rthstar.shop/', domain='n0rthstar.shop', score=88, label='likely_fake',
                  reasons=['copied logo'])
    actions.add_suspect(new)
    assert 'n0rthstar.shop' in [c['domain'] for c in storage.load_cases()]
    storage.save_review(case_id('n0rthstar.shop'), decision='fake')
    actions.add_suspect(Suspect(url='https://n0rthstar.shop/', domain='n0rthstar.shop', score=90, label='likely_fake'))
    assert suspect('n0rthstar.shop')['status'] == 'confirmed' and suspect('n0rthstar.shop')['score'] == 90

    monkeypatch.setattr(storage, 'STATE_FILE', tmp_path / 'fresh' / 'state.json')  # no state file yet
    actions.add_suspect(new)
    assert [s['domain'] for s in json.loads(storage.STATE_FILE.read_text())['suspects']] == ['n0rthstar.shop']


def test_state_source(monkeypatch):
    monkeypatch.setattr(storage, 'STATE_FILE', storage.DEMO_STATE.resolve())
    assert storage.state_source() == 'demo'
    monkeypatch.setattr(storage, 'STATE_FILE', storage.ENGINE_STATE)
    assert storage.state_source() == 'engine'
