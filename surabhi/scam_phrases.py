"""Scam-phrase hook for the FORGE investigator (prarthana/investigator.py imports find_scam_phrases from here).

Uses the dashboard's phrase checker (phrases.py): pressure and payment phrases such as "UPI only",
countdowns, prize fees. Returns one label per phrase type found, in page order.
"""
from surabhi.phrases import find_phrases


def find_scam_phrases(text: str) -> list[str]:
    return [label for label, *_ in find_phrases(text or '')]
