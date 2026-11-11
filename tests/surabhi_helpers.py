"""Test images and a throw-away Brand DNA database for Surabhi's module tests."""
from __future__ import annotations

import random
from io import BytesIO

import pytest
from PIL import Image, ImageDraw


def picture(seed: int, size=(400, 300)) -> Image.Image:
    """A deterministic 'photo' of coloured rectangles (distinct seeds give unrelated perceptual hashes)."""
    r = random.Random(seed)
    im = Image.new("RGB", size, (r.randrange(256),) * 3)
    d = ImageDraw.Draw(im)
    for _ in range(25):
        x, y = r.randrange(size[0]), r.randrange(size[1])
        d.rectangle([x, y, x + r.randrange(30, 150), y + r.randrange(30, 120)],
                    fill=tuple(r.randrange(256) for _ in range(3)))
    return im


def encode(im: Image.Image, fmt: str = "PNG", **kw) -> bytes:
    buf = BytesIO()
    im.save(buf, fmt, **kw)
    return buf.getvalue()


LOGO = encode(picture(1, (200, 200)))
PHOTO_A = encode(picture(2), "JPEG", quality=92)
PHOTO_B = encode(picture(3), "JPEG", quality=92)
PHOTO_A_RESIZED = encode(picture(2).resize((300, 225)), "JPEG", quality=70)  # same picture, different bytes
UNRELATED = encode(picture(99), "JPEG")
CORRUPT = b"\xff\xd8\xff\xe0 this is not really a jpeg"
SVG = b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"></svg>'


@pytest.fixture
def brand_db(tmp_path, monkeypatch):
    path = tmp_path / "brand.db"
    monkeypatch.setenv("FORGE_BRAND_DB", str(path))
    return path


def fake_resolver(live: dict):
    """DNS stand-in: {domain: [ips]} resolve, everything else is NXDOMAIN."""
    def resolve(domain):
        if domain in live:
            return {"status": "resolves", "addresses": live[domain], "error": ""}
        return {"status": "no_such_domain", "addresses": [], "error": ""}
    return resolve
