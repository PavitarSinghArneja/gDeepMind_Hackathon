from snapsort.sense.extract import extract, should_ignore
from snapsort.sense.fingerprint import hamming, sha256_file, wait_until_stable


def one(mock_dir, pattern):
    return next(mock_dir.rglob(pattern))


def test_text_pdf(mock_dir):
    c = extract(one(mock_dir, "Downloads/Airtel_Bill_*.pdf"))
    assert c.kind == "pdf_text" and "1,179.00" in c.text and c.images and c.error is None


def test_scanned_pdf_is_ocrd(mock_dir):
    c = extract(one(mock_dir, "Scanned_Rent_Agreement.pdf"))
    assert c.kind == "pdf_scanned" and "RENTAL" in c.text.upper()


def test_encrypted_pdf(mock_dir):
    assert extract(one(mock_dir, "Statement_Protected.pdf")).error == "encrypted"


def test_corrupt_pdf(mock_dir):
    assert extract(one(mock_dir, "broken_download.pdf")).error == "corrupt"


def test_screenshot_is_ocrd_and_hashed(mock_dir):
    c = extract(one(mock_dir, "Screenshot * at 21.14.03.png"))
    assert c.kind == "image" and "HomeNet" in c.text and c.dhash and c.images


def test_unsupported_and_text(mock_dir):
    assert extract(one(mock_dir, "setup_installer.dmg")).kind == "unsupported"
    note = extract(one(mock_dir, "ReadMe_Assistant.txt"))
    assert note.kind == "text" and "Ignore all previous instructions" in note.text


def test_should_ignore():
    from pathlib import Path

    assert should_ignore(Path("x.pdf.crdownload"))
    assert should_ignore(Path(".DS_Store"))
    assert should_ignore(Path("~$report.docx"))
    assert not should_ignore(Path("bill.pdf"))


def test_near_duplicate_screenshots_hash_close(mock_dir):
    a = extract(one(mock_dir, "Screenshot * at 21.14.03.png")).dhash
    b = extract(one(mock_dir, "Screenshot * at 21.14.10.png")).dhash
    other = extract(one(mock_dir, "IMG_2231.jpg")).dhash
    assert hamming(a, b) <= 6
    assert hamming(a, other) > 6


def test_exact_copy_has_same_sha(mock_dir):
    assert sha256_file(one(mock_dir, "Downloads/Airtel_Bill_*.pdf")) == sha256_file(one(mock_dir, "document(1).pdf"))


def test_wait_until_stable(tmp_path):
    p = tmp_path / "f.bin"
    p.write_bytes(b"abc")
    assert wait_until_stable(p, interval=0.01, checks=2, timeout=1)
    assert not wait_until_stable(tmp_path / "missing", interval=0.01, checks=2, timeout=0.1)
