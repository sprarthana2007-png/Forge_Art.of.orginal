"""#12 evaluation: runs `python -m prarthana.evaluate` on the 20 labelled local pages (no external services)."""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_evaluation_runs_and_meets_floor(tmp_path):
    out = tmp_path / "eval_report.md"
    env = {**__import__("os").environ, "FORGE_CACHE_DIR": str(tmp_path / "cache")}
    r = subprocess.run([sys.executable, "-m", "prarthana.evaluate", "--out", str(out)], cwd=ROOT, env=env,
                       capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, r.stderr[-2000:]
    res = json.loads((tmp_path / "eval_results.json").read_text())
    assert res["n"] == 20 and res["n_fake"] == 10 and res["n_legit"] == 10
    by = {p["site"]: p for p in res["pages"]}
    assert by["store"]["score"] == 0 and by["reseller"]["score"] == 0            # own store + allow-list
    assert by["fake-cloaking"]["cloaking"] and by["fake-relaunch"]["relaunch_of"].startswith("old-fake.localhost")
    assert by["crafts-market"]["relaunch_of"] == ""                              # small shared logo never links
    m = res["flagged_50"]
    assert m["precision"] >= 0.8 and m["recall"] >= 0.7                          # regression floor, not a claim
    md = out.read_text()
    assert "tiny and synthetic" in md and "| fake-full | fake |" in md
