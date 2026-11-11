"""Import Surabhi's functions if they exist in surabhi/, else fall back to prarthana/stubs/."""
from __future__ import annotations

import importlib


def _first(candidates):
    for mod, fn in candidates:
        try:
            f = getattr(importlib.import_module(mod), fn)
            if callable(f):
                return f, f"{mod}.{fn}"
        except Exception:
            continue
    return None, None


def get_build_brand_dna():
    f, where = _first([("surabhi.brand_dna", "build_brand_dna"), ("surabhi", "build_brand_dna")])
    if f:
        return f, where
    from prarthana.stubs.brand_dna_stub import build_brand_dna
    return build_brand_dna, "prarthana.stubs.brand_dna_stub (stub)"


def get_name_net():
    f, where = _first([("surabhi.name_net", "name_net"), ("surabhi", "name_net")])
    if f:
        return f, where
    from prarthana.stubs.name_net_stub import name_net
    return name_net, "prarthana.stubs.name_net_stub (stub)"
