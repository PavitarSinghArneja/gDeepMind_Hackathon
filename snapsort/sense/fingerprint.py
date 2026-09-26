"""Content fingerprints: exact (SHA-256) and visual (difference hash) for near-duplicate screenshots."""
from __future__ import annotations

import hashlib
import time
from pathlib import Path

from PIL import Image


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def dhash(img: Image.Image, size: int = 8) -> str:
    small = img.convert("L").resize((size + 1, size), Image.Resampling.LANCZOS)
    px = list(small.getdata())
    bits = 0
    for row in range(size):
        for col in range(size):
            left = px[row * (size + 1) + col]
            right = px[row * (size + 1) + col + 1]
            bits = (bits << 1) | (1 if left > right else 0)
    return f"{bits:0{size * size // 4}x}"


def hamming(a: str, b: str) -> int:
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def wait_until_stable(path: Path, interval: float = 0.5, checks: int = 3, timeout: float = 30.0) -> bool:
    """True once the file's size stops changing (a download or copy has finished)."""
    deadline = time.time() + timeout
    last, stable = -1, 0
    while time.time() < deadline:
        try:
            size = path.stat().st_size
        except FileNotFoundError:
            return False
        if size == last and size > 0:
            stable += 1
            if stable >= checks:
                return True
        else:
            stable = 0
        last = size
        time.sleep(interval)
    return False
