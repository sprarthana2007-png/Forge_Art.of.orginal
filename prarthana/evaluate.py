"""#12 Evaluation on a small labelled test set.

    python -m prarthana.evaluate [--out output/eval_report.md]

Serves tests/eval/sites (10 fake + 10 legitimate pages, each on its own <name>.localhost domain), runs the real
FORGE pipeline on each (Brand DNA -> investigate incl. cloaking check -> relaunch check -> scorer) and reports
precision / recall / a confusion table at two thresholds: "flagged" (score >= 50, sent to the owner) and
"likely fake" (score >= 75). Writes output/eval_report.md + output/eval_results.json.
No external services are called (API keys are blanked; local pages only).
"""
from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import math
import os
import socket
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EVAL = ROOT / "tests" / "eval"


def _isolate_env():
    """Throw-away cache + no API keys, set BEFORE prarthana.common is imported."""
    os.environ.setdefault("FORGE_CACHE_DIR", tempfile.mkdtemp(prefix="forge-eval-"))
    for k in ("URLSCAN_API_KEY", "GEMINI_API_KEY", "RESEND_API_KEY", "GMAIL_APP_PASSWORD"):
        os.environ[k] = ""
    os.environ.setdefault("FORGE_CLOAK_BUDGET", "100000")


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _start_server(port):
    spec = importlib.util.spec_from_file_location("forge_eval_server", EVAL / "server.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    srv = mod.serve(port)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def wilson(k, n, z=1.96):
    """95% Wilson score interval for k successes out of n."""
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def metrics(rows, threshold):
    tp = sum(1 for r in rows if r["label"] == "fake" and r["score"] >= threshold)
    fn = sum(1 for r in rows if r["label"] == "fake" and r["score"] < threshold)
    fp = sum(1 for r in rows if r["label"] == "legit" and r["score"] >= threshold)
    tn = sum(1 for r in rows if r["label"] == "legit" and r["score"] < threshold)
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    return {"threshold": threshold, "tp": tp, "fp": fp, "fn": fn, "tn": tn, "precision": prec, "recall": rec,
            "f1": f1, "accuracy": (tp + tn) / len(rows) if rows else 0.0,
            "precision_ci": wilson(tp, tp + fp), "recall_ci": wilson(tp, tp + fn)}


def run(out_md: Path, out_json: Path, quiet: bool = False) -> dict:
    _isolate_env()
    sys.path.insert(0, str(ROOT))
    from schemas import Suspect
    from prarthana.common import domain_of
    from prarthana.investigator import investigate
    from prarthana.relaunch import detect
    from prarthana.scorer import score_suspect
    from prarthana.teammate import get_build_brand_dna

    labels = json.loads((EVAL / "labels.json").read_text())
    port = _free_port()
    srv = _start_server(port)
    url = lambda site: f"http://{site}.localhost:{port}/"
    say = (lambda *a: None) if quiet else print
    try:
        build, where = get_build_brand_dna()
        dna = build(url(labels["brand_store"]))
        dna.allow_list = list(dna.allow_list or []) + [domain_of(url(x)) for x in labels["allow_list"]]
        history = []
        for h in labels["history"]:
            old = Suspect(url=url(h["site"]), found_by="eval")
            investigate(old, dna, [])
            old.status = h["status"]
            history.append({"url": old.url, "domain": old.domain, "status": old.status, "ip": old.ip,
                            "contact_hashes": old.contact_hashes, "image_sha256s": old.image_sha256s})
        rows = []
        for p in labels["pages"]:
            s = Suspect(url=url(p["site"]), found_by="eval")
            investigate(s, dna, [])
            if "domain_age_days" in p:          # local pages have no RDAP record: age is injected from labels.json
                s.domain_age_days = p["domain_age_days"]
            detect([s], history, dna, [])
            score_suspect(s, dna)
            rows.append({"site": p["site"], "label": p["label"], "description": p["description"], "score": s.score,
                         "forge_label": s.label, "signals": s.signals_fired, "cloaking": s.cloaking,
                         "relaunch_of": s.relaunch_of})
            say(f"  {p['site']:<18} {p['label']:<6} score {s.score:>3}  {s.label:<12} {','.join(s.signals_fired)}")
    finally:
        srv.shutdown()

    m50, m75 = metrics(rows, 50), metrics(rows, 75)
    res = {"date": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), "n": len(rows),
           "n_fake": sum(r["label"] == "fake" for r in rows), "n_legit": sum(r["label"] == "legit" for r in rows),
           "brand_dna_from": where, "flagged_50": m50, "likely_fake_75": m75, "pages": rows}
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(res, indent=1))
    out_md.write_text(report_md(res), encoding="utf-8")
    say(summary(res))
    return res


def _pct(x):
    return f"{round(100 * x)}%"


def _ci(ci):
    return f"{_pct(ci[0])}–{_pct(ci[1])}"


def summary(res) -> str:
    a, b = res["flagged_50"], res["likely_fake_75"]
    return (f"\nFORGE evaluation on {res['n']} labelled pages ({res['n_fake']} fake / {res['n_legit']} legit)\n"
            f"  flagged (score >= 50):     precision {_pct(a['precision'])}  recall {_pct(a['recall'])}  "
            f"(TP {a['tp']} FP {a['fp']} FN {a['fn']} TN {a['tn']})\n"
            f"  likely fake (score >= 75): precision {_pct(b['precision'])}  recall {_pct(b['recall'])}  "
            f"(TP {b['tp']} FP {b['fp']} FN {b['fn']} TN {b['tn']})")


def report_md(res) -> str:
    def block(title, m):
        return (f"### {title}\n\n"
                f"| | predicted fake | predicted legit |\n|---|---|---|\n"
                f"| **actually fake** ({m['tp'] + m['fn']}) | {m['tp']} (TP) | {m['fn']} (FN) |\n"
                f"| **actually legit** ({m['fp'] + m['tn']}) | {m['fp']} (FP) | {m['tn']} (TN) |\n\n"
                f"Precision **{_pct(m['precision'])}** (95% CI {_ci(m['precision_ci'])}) · "
                f"Recall **{_pct(m['recall'])}** (95% CI {_ci(m['recall_ci'])}) · "
                f"F1 {m['f1']:.2f} · Accuracy {_pct(m['accuracy'])}\n")
    lines = [f"# FORGE evaluation — small labelled test set", "",
             f"Run {res['date']} · **{res['n']} pages** ({res['n_fake']} fake, {res['n_legit']} legitimate) · "
             f"`python -m prarthana.evaluate`", "",
             "> **Read this first: the sample is tiny and synthetic.** 20 hand-made local pages, built by the FORGE "
             "team itself, cannot tell you how FORGE performs on the real internet. The 95% confidence intervals "
             "below are very wide for that reason. Treat the numbers as a regression check (did a change break "
             "something?) and a sanity check of the scoring rules, not as a measured accuracy.", "",
             "## Results", "",
             block("Flagged for the owner's review (score ≥ 50 = likely fake or suspicious)", res["flagged_50"]),
             block("Likely fake (score ≥ 75)", res["likely_fake_75"]),
             "## Per page", "",
             "| page | truth | FORGE score | FORGE label | signals that fired | what it is |",
             "|---|---|---|---|---|---|"]
    for r in res["pages"]:
        ok = (r["score"] >= 50) == (r["label"] == "fake")
        lines.append(f"| {r['site']} | {r['label']} | {r['score']} {'✓' if ok else '✗'} | {r['forge_label']} | "
                     f"{', '.join(r['signals']) or '—'} | {r['description']} |")
    lines += ["", "## How it was run and its limits", "",
              "- Every page runs through the real pipeline: Brand DNA (from the `store` page, with "
              f"`{res['brand_dna_from']}`), the Investigator with the 3-way cloaking check, the relaunch check against "
              "one confirmed fake (`old-fake`), and the scorer with default weights. No external services are used.",
              "- Each page has its own domain (`<name>.localhost`), so the brand's own store and the allow-listed "
              "reseller are recognised by domain, as in production.",
              "- Not exercised: the **young-domain** signal comes from RDAP / WHOIS, which local pages do not have, so "
              "one page's age is injected from `labels.json`; **IP** links (no DNS for `*.localhost`); cloaking visit 3 "
              "(urlscan cannot reach local pages); Gemini (not part of the score).",
              "- The pages were written after the scorer and the scorer was not tuned to them, but the same people "
              "wrote both, so the set reflects the clues we already know about. Real fakes vary far more "
              "(other languages, images cropped or with text on top, prices in other formats, pages behind logins).",
              "- The two missed fakes are the hard cases on purpose: one with only look-alike photos + its own UPI "
              "and normal prices, one with the brand's product names and prices but its own photos. The false alarm "
              "is an influencer post that legitimately shows the brand's photos and logo next to her own e-mail: a "
              "human confirmation step (FORGE never reports without it) is exactly for cases like this.",
              "- **The evaluation found one real bug, fixed before these numbers:** a legitimate marketplace was "
              "marked as a *relaunch* of the confirmed fake because both pages showed the same small 200×80 logo. "
              "Many unrelated shops share small images (UPI / card badges, theme icons), so images under 100 px on "
              "the short side no longer link sites (relaunch, campaigns, campaign graph). They still count for "
              "matching the brand's own photos / logo. The score was below 50 anyway, but the false relaunch "
              "label would have been shown to the owner.",
              "- To grow this into a real benchmark: label 100+ real suspects found by the urlscan / name nets "
              "(owner answers from `cache/feedback.json` are a natural source) and report per-signal results."]
    return "\n".join(lines) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description="Evaluate FORGE on the small labelled test set in tests/eval/")
    ap.add_argument("--out", default=str(ROOT / "output" / "eval_report.md"))
    a = ap.parse_args(argv)
    out = Path(a.out)
    run(out, out.with_name("eval_results.json"))
    print(f"\nreport: {out}")


if __name__ == "__main__":
    main()
