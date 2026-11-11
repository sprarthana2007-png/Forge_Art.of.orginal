"""End-to-end: serve the real store + the clone locally, run main.py, check the scores.

Uses surabhi/demo_sites/{store,clone} if they exist, else tests/fixtures/{store,clone}.
External services are disabled (no keys) so the test is deterministic and sends no e-mail.
"""
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _site_dirs():
    demo = ROOT / "surabhi" / "demo_sites"
    if (demo / "store" / "index.html").exists() and (demo / "clone" / "index.html").exists():
        return demo / "store", demo / "clone"
    return ROOT / "tests" / "fixtures" / "store", ROOT / "tests" / "fixtures" / "clone"


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _serve(directory, port):
    p = subprocess.Popen([sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1",
                          "--directory", str(directory)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(50):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            return p
        except OSError:
            time.sleep(0.1)
    p.kill()
    raise RuntimeError("http.server did not start")


def test_clone_flagged_real_store_ignored(tmp_path):
    store_dir, clone_dir = _site_dirs()
    sp, cp = _free_port(), _free_port()
    servers = [_serve(store_dir, sp), _serve(clone_dir, cp)]
    store, clone = f"http://localhost:{sp}/", f"http://localhost:{cp}/"
    env = dict(os.environ, FORGE_CACHE_DIR=str(tmp_path / "cache"), FORGE_OUTPUT_DIR=str(tmp_path / "output"),
               URLSCAN_API_KEY="", GEMINI_API_KEY="", RESEND_API_KEY="", GMAIL_APP_PASSWORD="", DEMO_MODE="on")
    try:
        r = subprocess.run([sys.executable, "main.py", "--store", store, "--suspect", clone, "--suspect", store,
                            "--fresh"], cwd=ROOT, env=env, capture_output=True, text=True, timeout=300)
    finally:
        for p in servers:
            p.kill()
    print(r.stdout[-3000:], r.stderr[-3000:])
    assert r.returncode == 0
    state = json.loads((tmp_path / "cache" / "state.json").read_text())
    by_url = {s["url"]: s for s in state["suspects"]}
    assert by_url[clone]["score"] >= 75, by_url[clone]
    assert by_url[clone]["label"] == "likely_fake"
    assert by_url[clone]["verdict"]
    assert by_url[store]["label"] == "ignored"
    # evidence + alert written; no raw contacts anywhere in the saved state
    assert Path(by_url[clone]["pdf_path"]).exists()
    assert (tmp_path / "output" / "alert_email.eml").exists()
    raw = (tmp_path / "cache" / "state.json").read_text()
    assert "91234" not in raw and "mittisale@okaxis" not in raw


def test_abuse_report_needs_confirmation(tmp_path, monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "")
    import prarthana.common as c
    import prarthana.alert as alert
    monkeypatch.setattr(c, "OUTPUT", tmp_path)
    monkeypatch.setattr(alert, "OUTPUT", tmp_path)
    monkeypatch.setattr(alert, "find_abuse_email", lambda d: "abuse@registrar.example")
    from schemas import BrandDNA, Suspect
    s = Suspect(url="https://fake.example/", domain="fake.example", score=80, label="likely_fake")
    dna = BrandDNA(store_url="https://real.example", brand_name="Real")
    assert alert.send_abuse_report(s, dna) == "not_confirmed" and s.status == "new"
    alert.confirm(s, True)
    how = alert.send_abuse_report(s, dna)
    assert how.startswith("eml:") and s.status == "reported"
    eml = Path(how[4:]).read_text(errors="ignore")
    assert "DEMO MODE" in eml and "abuse@registrar.example" in eml
    s2 = Suspect(url="https://ok.example/", domain="ok.example")
    alert.confirm(s2, False)
    assert s2.status == "not_fake" and alert.send_abuse_report(s2, dna) == "not_confirmed"
