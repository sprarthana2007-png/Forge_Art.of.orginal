"""FORGE — find fake copies of a D2C brand's store.

    python main.py --store <url> [--suspect <url> ...] [--fresh] [--watch]
"""
from __future__ import annotations

# Load .env (project root) before anything else reads os.environ; real environment variables win.
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent / ".env", encoding="utf-8-sig")

import argparse
import sys

from prarthana.planner import run_forge


def print_summary(st) -> None:
    rows = sorted(st.suspects, key=lambda s: -s.score)
    print("\n" + "=" * 100)
    print(f"FORGE summary for {st.brand_dna.brand_name!r} ({st.brand_dna.store_url}) - {len(rows)} suspects")
    print("=" * 100)
    print(f"{'SCORE':>5}  {'LABEL':<12} {'FOUND BY':<16} {'CAMPAIGN':<11} {'STATUS':<12} URL")
    print("-" * 100)
    for s in rows:
        print(f"{s.score:>5}  {s.label:<12} {s.found_by:<16} {(s.campaign_id or '-'):<11} {s.status:<12} {s.url}")
    print("-" * 100)
    for s in rows:
        if s.label != "ignored":
            print(f"\n{s.url}\n  verdict: {s.verdict}\n  pdf: {s.pdf_path or '-'}")
    print()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="FORGE: hunt fake copies of your online store")
    ap.add_argument("--store", required=True, help="the brand's real store URL")
    ap.add_argument("--suspect", action="append", default=[], help="a known suspect URL (repeatable)")
    ap.add_argument("--fresh", action="store_true", help="ignore cache/state.json and start a new state")
    ap.add_argument("--watch", action="store_true", help="re-run the hunt every 6 hours (APScheduler)")
    a = ap.parse_args(argv)

    st = run_forge(a.store, a.suspect, fresh=a.fresh)
    print_summary(st)
    if a.watch:
        from apscheduler.schedulers.blocking import BlockingScheduler
        sched = BlockingScheduler()
        sched.add_job(lambda: print_summary(run_forge(a.store, [])), "interval", hours=6)
        print("[forge] watching: re-running the hunt every 6 hours (Ctrl+C to stop)")
        try:
            sched.start()
        except (KeyboardInterrupt, SystemExit):
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
