"""The 4 demo cases (surabhi/demo_sites) through the real FORGE engine: python main.py on separately served sites.

Expected: the two high-risk fakes (case1 sale, case4 rewards) score 75+ ("likely_fake"), the look-alike outlet
(case2) is "suspicious", and the authorised partner (case3) stays low (under the 50-point "suspicious" line).
External services are disabled, as in test_e2e.py.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "surabhi" / "demo_sites"))
import serve  # noqa: E402


def test_demo_cases_through_forge(tmp_path):
    urls, servers = serve.start(0)  # free ports: the official store and each case on its own origin
    env = dict(os.environ, FORGE_CACHE_DIR=str(tmp_path / "cache"), FORGE_OUTPUT_DIR=str(tmp_path / "output"),
               FORGE_BRAND_DB=str(tmp_path / "brand.db"), URLSCAN_API_KEY="", GEMINI_API_KEY="",
               RESEND_API_KEY="", GMAIL_APP_PASSWORD="", DEMO_MODE="on")
    cmd = [sys.executable, "main.py", "--store", urls["official"], "--fresh"]
    for case in ("case1", "case2", "case3", "case4"):
        cmd += ["--suspect", urls[case]]
    try:
        r = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True, timeout=400)
    finally:
        for srv in servers:
            srv.shutdown()
    print(r.stdout[-4000:], r.stderr[-3000:])
    assert r.returncode == 0

    state = json.loads((tmp_path / "cache" / "state.json").read_text())
    dna = state["brand_dna"]
    assert dna["brand_name"] == "Northstar"                       # from the <title>, not "Localhost"
    assert dna["logo_sha256"] and len(dna["photo_sha256s"]) >= 4 and len(dna["prices"]) >= 4
    assert dna["official_contact_hashes"]                          # care@northstar.example
    by_case = {s["url"].rstrip("/").rsplit("/", 1)[-1]: s for s in state["suspects"]}

    for case in ("case1", "case4"):                                # high-risk fakes
        s = by_case[case]
        assert s["score"] >= 75 and s["label"] == "likely_fake", s["reasons"]
        assert {"photos", "logo", "price", "contacts"} <= set(s["signals_fired"]), s["signals_fired"]
        assert len(s["image_exact_matches"]) >= 1 and s["logo_match"]

    outlet = by_case["case2"]                                      # look-alike photo, 40% off, own contact
    assert outlet["label"] == "suspicious" and 50 <= outlet["score"] < 75, outlet["reasons"]
    assert len(outlet["image_similar_matches"]) == 1 and not outlet["image_exact_matches"]

    partner = by_case["case3"]                                     # authorised retailer: official photos, fair prices
    assert partner["score"] < 50 and partner["label"] == "ignored", partner["reasons"]
    assert "price" not in partner["signals_fired"] and not partner["logo_match"]
