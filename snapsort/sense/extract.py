"""Turn a file into text (and a page image for the model). All local: PyMuPDF + Tesseract."""
from __future__ import annotations

import io
from dataclasses import dataclass, field
from pathlib import Path

import fitz
import pytesseract
from PIL import Image, ImageFilter, ImageOps, UnidentifiedImageError

from .fingerprint import dhash

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}
PDF_EXT = {".pdf"}
TEXT_EXT = {".txt", ".md", ".csv"}
PARTIAL_SUFFIXES = {".crdownload", ".part", ".download", ".tmp"}
MAX_MODEL_SIDE = 1280
LOW_QUALITY = 25  # fewer letters/digits than this = "couldn't really read it"


@dataclass
class Content:
    kind: str
    text: str = ""
    images: list[bytes] = field(default_factory=list)
    pages: int = 0
    error: str | None = None
    enhanced: bool = False
    dhash: str | None = None


def should_ignore(path: Path) -> bool:
    return path.name.startswith(".") or path.name.startswith("~$") or path.suffix.lower() in PARTIAL_SUFFIXES


def text_quality(text: str) -> int:
    return sum(ch.isalnum() for ch in text)


def _png(img: Image.Image) -> bytes:
    img = img.convert("RGB")
    img.thumbnail((MAX_MODEL_SIDE, MAX_MODEL_SIDE))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def _ocr(img: Image.Image) -> str:
    return pytesseract.image_to_string(img.convert("L")).strip()


def _enhance(img: Image.Image) -> Image.Image:
    g = ImageOps.grayscale(img)
    g = g.resize((g.width * 2, g.height * 2), Image.Resampling.LANCZOS)
    return ImageOps.autocontrast(g).filter(ImageFilter.SHARPEN)


def extract(path: Path) -> Content:
    ext = path.suffix.lower()
    if ext in IMAGE_EXT:
        return _image(path)
    if ext in PDF_EXT:
        return _pdf(path)
    if ext in TEXT_EXT:
        return Content(kind="text", text=path.read_text(errors="replace")[:20000])
    return Content(kind="unsupported")


def _image(path: Path) -> Content:
    try:
        with Image.open(path) as im:
            im.load()
            img = im.copy()
    except (UnidentifiedImageError, OSError):
        return Content(kind="image", error="corrupt")
    text, enhanced = _ocr(img), False
    if text_quality(text) < LOW_QUALITY:
        retry = _ocr(_enhance(img))
        if text_quality(retry) > text_quality(text):
            text, enhanced = retry, True
    return Content(kind="image", text=text, images=[_png(img)], enhanced=enhanced, dhash=dhash(img))


def _pdf(path: Path) -> Content:
    try:
        doc = fitz.open(path)
    except Exception:
        return Content(kind="pdf_text", error="corrupt")
    with doc:
        if doc.needs_pass:
            return Content(kind="pdf_text", error="encrypted", pages=doc.page_count)
        if doc.page_count == 0:
            return Content(kind="pdf_text", error="corrupt")
        text = "\n".join(doc[i].get_text() for i in range(min(doc.page_count, 5))).strip()
        page_img = Image.open(io.BytesIO(doc[0].get_pixmap(dpi=150).tobytes("png")))
        page_img.load()
        kind = "pdf_text"
        if text_quality(text) < LOW_QUALITY:
            kind, text = "pdf_scanned", _ocr(page_img)
        return Content(kind=kind, text=text[:20000], images=[_png(page_img)], pages=doc.page_count)
