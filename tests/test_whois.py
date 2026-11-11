"""WHOIS fallback for the domain age (RDAP first)."""
import datetime as dt
import sys
import types

import pytest


@pytest.fixture()
def inv(tmp_path, monkeypatch):
    import prarthana.common as common
    import prarthana.investigator as investigator
    monkeypatch.setattr(common, "CACHE", tmp_path)
    monkeypatch.setattr(investigator, "cache_get", lambda sub, key: None)
    monkeypatch.setattr(investigator, "cache_set", lambda sub, key, v: None)
    return investigator


def _fake_whois(monkeypatch, creation):
    mod = types.ModuleType("whois")
    if isinstance(creation, Exception):
        def whois(d, **kw):
            raise creation
    else:
        def whois(d, **kw):
            return {"creation_date": creation}
    mod.whois = whois
    monkeypatch.setitem(sys.modules, "whois", mod)


def test_rdap_first(inv, monkeypatch):
    when = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=12)).isoformat()
    monkeypatch.setattr(inv, "rdap", lambda d: {"events": [{"eventAction": "registration", "eventDate": when}]})
    _fake_whois(monkeypatch, RuntimeError("must not be called"))
    assert inv.domain_age_days("fake.example") == 12


def test_whois_fallback_when_rdap_has_no_date(inv, monkeypatch):
    monkeypatch.setattr(inv, "rdap", lambda d: None)
    created = dt.datetime.now() - dt.timedelta(days=5)
    _fake_whois(monkeypatch, [created, created + dt.timedelta(days=1)])    # list + naive datetime, like real WHOIS
    log = []
    assert inv.domain_age_days("fake.example", log) == 5
    assert "WHOIS says 5 days" in log[0]


def test_both_fail_keeps_none_and_logs(inv, monkeypatch):
    monkeypatch.setattr(inv, "rdap", lambda d: {"events": []})
    _fake_whois(monkeypatch, ConnectionRefusedError("port 43 blocked"))
    log = []
    assert inv.domain_age_days("fake.example", log) is None
    assert "WHOIS failed" in log[0]


def test_local_names_skip_lookups(inv, monkeypatch):
    monkeypatch.setattr(inv, "rdap", lambda d: pytest.fail("no RDAP for local names"))
    assert inv.domain_age_days("shop.localhost:8080", []) is None
